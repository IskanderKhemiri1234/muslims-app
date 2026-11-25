"""Authenticated, read-only Binance Live safety endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .live_control import LiveControlError, LiveControlService

log = logging.getLogger("iskander_trade.live_api")
router = APIRouter(prefix="/api/v1/live", tags=["live"])
service = LiveControlService()


@router.get("/status", dependencies=[Depends(__import__("iskander_trade.api", fromlist=["require_dashboard_token"]).require_dashboard_token)])
def live_status() -> dict[str, Any]:
    return service.status()


@router.get("/preflight", dependencies=[Depends(__import__("iskander_trade.api", fromlist=["require_dashboard_token"]).require_dashboard_token)])
def live_preflight() -> dict[str, Any]:
    try:
        return service.preflight()
    except LiveControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/emergency-stop", dependencies=[Depends(__import__("iskander_trade.api", fromlist=["require_dashboard_token"]).require_dashboard_token)])
def live_emergency_stop() -> dict[str, Any]:
    service.set_stopped(True)
    return {"status": "stopped", "emergency_stop": True, "orders_enabled": False}
