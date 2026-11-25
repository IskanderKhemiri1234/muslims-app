"""Authenticated, read-only Binance Live safety endpoints."""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .live_control import LiveControlError, LiveControlService
from .live_orders import LiveManualOrderService, LiveOrderError
from .live_execution import ExecutionDisabled, OrderValidationError

log = logging.getLogger("iskander_trade.live_api")
router = APIRouter(prefix="/api/v1/live", tags=["live"])
service = LiveControlService()
orders = LiveManualOrderService()


class ManualOrderRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=24)
    side: str
    order_type: str
    amount: str = Field(min_length=1, max_length=40)
    price: str | None = Field(default=None, max_length=40)


def _require_dashboard_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("DASHBOARD_TOKEN", "")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected:
        raise HTTPException(status_code=503, detail="Dashboard access token is not configured.")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid dashboard token.")


@router.get("/status", dependencies=[Depends(_require_dashboard_token)])
def live_status() -> dict[str, Any]:
    return service.status()


@router.get("/preflight", dependencies=[Depends(_require_dashboard_token)])
def live_preflight() -> dict[str, Any]:
    try:
        return service.preflight()
    except LiveControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/emergency-stop", dependencies=[Depends(__import__("iskander_trade.api", fromlist=["require_dashboard_token"]).require_dashboard_token)])
def live_emergency_stop() -> dict[str, Any]:
    service.set_stopped(True)
    return {"status": "stopped", "emergency_stop": True, "orders_enabled": False}

@router.get("/orders", dependencies=[Depends(_require_dashboard_token)])
def live_orders_status() -> dict[str, Any]:
    return orders.status()


@router.post("/arm", dependencies=[Depends(_require_dashboard_token)])
def arm_manual_live() -> dict[str, Any]:
    try:
        return orders.arm()
    except LiveOrderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/disarm", dependencies=[Depends(_require_dashboard_token)])
def disarm_manual_live() -> dict[str, Any]:
    return orders.disarm()


@router.post("/emergency-stop", dependencies=[Depends(_require_dashboard_token)])
def live_emergency_stop() -> dict[str, Any]:
    return orders.emergency_stop()


@router.post("/emergency-stop/release", dependencies=[Depends(_require_dashboard_token)])
def release_live_emergency_stop() -> dict[str, Any]:
    service.set_stopped(False)
    return orders.status()


@router.post("/orders", dependencies=[Depends(_require_dashboard_token)])
def submit_manual_live_order(payload: ManualOrderRequest) -> dict[str, Any]:
    try:
        return orders.submit(
            symbol=payload.symbol,
            side=payload.side,
            order_type=payload.order_type,
            amount=payload.amount,
            price=payload.price,
        )
    except (LiveOrderError, ExecutionDisabled, OrderValidationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Manual Live order request failed.")
        raise HTTPException(status_code=502, detail="Order outcome may be uncertain; reconcile before retrying.") from exc


@router.post("/reconcile", dependencies=[Depends(_require_dashboard_token)])
def reconcile_live_orders() -> dict[str, Any]:
    try:
        return orders.reconcile()
    except Exception as exc:
        log.exception("Live order reconciliation failed.")
        raise HTTPException(status_code=502, detail="Live reconciliation failed; unresolved orders remain blocked.") from exc
