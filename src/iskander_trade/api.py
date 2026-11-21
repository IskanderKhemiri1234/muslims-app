"""Paper-only FastAPI dashboard API designed to tolerate free-tier sleep."""
from __future__ import annotations

import hmac
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

import ccxt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .engine import PaperTradingEngine
from .market_data import BinanceMarketData
from .ml import train_research_model
from .state import load_state

settings = Settings()
settings.validate()
dashboard_dir = Path(__file__).resolve().parents[2] / "dashboard"
cycle_lock = threading.Lock()
app = FastAPI(title="Iskander AI Trade API", version="0.4.0")
if dashboard_dir.exists():
    app.mount("/assets", StaticFiles(directory=dashboard_dir), name="assets")


def require_dashboard_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("DASHBOARD_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Dashboard access token is not configured.")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid dashboard token.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ok", "mode": "paper",
            "execution": "manual_cycle_only", "hosting": "free_tier_compatible"}


@app.get("/")
def dashboard() -> FileResponse:
    page = dashboard_dir / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Dashboard assets are not installed.")
    return FileResponse(page)


def status_payload() -> dict[str, Any]:
    state = load_state(settings.state_path)
    return {
        "mode": "paper", "watchlist": list(settings.watchlist),
        "timeframe": settings.timeframe,
        "virtual_capital_usdt": settings.virtual_capital_usdt,
        "risk_per_trade_usdt": settings.risk_per_trade_usdt,
        "daily_pnl_usdt": state.get("daily_pnl_usdt", 0.0),
        "total_realized_pnl_usdt": state.get("total_realized_pnl_usdt", 0.0),
        "positions": state.get("positions", {}),
        "trade_history": state.get("trade_history", [])[-50:],
        "state_persistence": "ephemeral_free_host_storage",
    }


@app.get("/api/v1/status", dependencies=[Depends(require_dashboard_token)])
def status() -> dict[str, Any]:
    return status_payload()


@app.post("/api/v1/paper/cycle", dependencies=[Depends(require_dashboard_token)])
def run_paper_cycle() -> dict[str, Any]:
    """Run one user-requested paper cycle; no always-on loop on a sleeping free instance."""
    if not cycle_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A paper cycle is already running.")
    client = BinanceMarketData()
    try:
        engine = PaperTradingEngine(settings, client, load_state(settings.state_path))
        engine.run_cycle()
        return {"status": "completed", "mode": "paper", "portfolio": status_payload()}
    except (ccxt.BaseError, OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Paper cycle could not complete: {exc}") from exc
    finally:
        client.close()
        cycle_lock.release()


@app.get("/api/v1/markets", dependencies=[Depends(require_dashboard_token)])
def markets() -> dict[str, Any]:
    client = BinanceMarketData()
    try:
        rows = []
        for symbol in settings.watchlist:
            try:
                rows.append({"symbol": symbol, "price": client.fetch_last_price(symbol)})
            except ccxt.BaseError as exc:
                rows.append({"symbol": symbol, "error": str(exc)})
        return {"markets": rows}
    finally:
        client.close()


@app.get("/api/v1/ml/evaluate", dependencies=[Depends(require_dashboard_token)])
def evaluate_ml() -> dict[str, Any]:
    """Run chronological offline evaluation; output is informational, never a trade signal."""
    client = BinanceMarketData()
    reports: list[dict[str, Any]] = []
    try:
        for symbol in settings.watchlist:
            try:
                candles = client.fetch_candles(symbol, settings.timeframe, settings.candle_limit)
                closed_closes = [float(row[4]) for row in candles[:-1]]
                report = asdict(train_research_model(closed_closes))
                reports.append({
                    "symbol": symbol, **report,
                    "assessment": "above_baseline" if report["accuracy"] > report["baseline_accuracy"] else "not_above_baseline",
                })
            except (ccxt.BaseError, ValueError) as exc:
                reports.append({"symbol": symbol, "error": str(exc)})
        return {"method": "RandomForest + TimeSeriesSplit", "purpose": "offline research only", "reports": reports}
    finally:
        client.close()
