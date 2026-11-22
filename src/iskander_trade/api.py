"""Paper-only FastAPI dashboard API with a single-process 15-minute scheduler."""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ccxt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .engine import PaperTradingEngine
from .market_data import BinanceMarketData
from .state import load_state

log = logging.getLogger("iskander_trade.api")
settings = Settings()
settings.validate()
dashboard_dir = Path(__file__).resolve().parents[2] / "dashboard"
cycle_lock = threading.Lock()
scheduler_task: asyncio.Task[None] | None = None
AUTO_PAPER_ENABLED = os.getenv("AUTO_PAPER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
SCHEDULER_GRACE_SECONDS = max(5, int(os.getenv("SCHEDULER_GRACE_SECONDS", "8")))


def require_dashboard_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("DASHBOARD_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="Set DASHBOARD_TOKEN to a random secret of at least 32 characters.")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid dashboard token.")


def _run_one_cycle() -> dict[str, Any]:
    if not cycle_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A paper cycle is already running.")
    client: BinanceMarketData | None = None
    try:
        client = BinanceMarketData()
        engine = PaperTradingEngine(settings, client, load_state(settings.state_path))
        engine.run_cycle()
        return {"status": "completed", "mode": "paper", "portfolio": status_payload()}
    except (ccxt.BaseError, OSError, ValueError, RuntimeError) as exc:
        log.exception("Paper cycle failed")
        raise HTTPException(status_code=502, detail=f"Paper cycle could not complete: {exc}") from exc
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            cycle_lock.release()


def _seconds_until_next_closed_candle() -> float:
    """Run shortly after each UTC 15-minute boundary, never on a forming candle."""
    now = datetime.now(UTC)
    next_minute = ((now.minute // 15) + 1) * 15
    boundary = now.replace(second=0, microsecond=0)
    if next_minute == 60:
        boundary = boundary.replace(minute=0) + timedelta(hours=1)
    else:
        boundary = boundary.replace(minute=next_minute)
    due = boundary + timedelta(seconds=SCHEDULER_GRACE_SECONDS)
    return max(1.0, (due - now).total_seconds())


async def _scheduler_loop() -> None:
    log.info("Automatic paper scheduler enabled; interval=15m; grace=%ss", SCHEDULER_GRACE_SECONDS)
    while True:
        await asyncio.sleep(_seconds_until_next_closed_candle())
        try:
            await asyncio.to_thread(_run_one_cycle)
        except HTTPException as exc:
            if exc.status_code == 409:
                log.warning("Scheduled paper cycle skipped because another cycle is active")
            else:
                log.error("Scheduled paper cycle failed: %s", exc.detail)
        except Exception:
            log.exception("Unexpected scheduled paper cycle failure")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global scheduler_task
    if AUTO_PAPER_ENABLED:
        scheduler_task = asyncio.create_task(_scheduler_loop(), name="paper-15m-scheduler")
    else:
        log.warning("Automatic paper scheduler disabled by AUTO_PAPER_ENABLED")
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
            scheduler_task = None


app = FastAPI(title="Iskander AI Trade API", version="0.5.0", lifespan=lifespan)
if dashboard_dir.exists():
    app.mount("/assets", StaticFiles(directory=dashboard_dir), name="assets")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": settings.app_name, "status": "ok", "mode": "paper",
        "execution": "automatic_15m" if AUTO_PAPER_ENABLED else "manual_only",
        "scheduler": "enabled" if scheduler_task is not None and not scheduler_task.done() else "starting_or_disabled",
        "watchlist": list(settings.watchlist), "timeframe": settings.timeframe,
        "state_path_configured": str(settings.state_path),
    }


@app.get("/")
def dashboard() -> FileResponse:
    page = dashboard_dir / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Dashboard assets are not installed.")
    return FileResponse(page, headers={"Cache-Control": "no-store"})


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
        "state_persistence": "atomic_json_with_backup_on_configured_host_path",
        "automatic_cycle": AUTO_PAPER_ENABLED,
    }


@app.get("/api/v1/status", dependencies=[Depends(require_dashboard_token)])
def status() -> dict[str, Any]:
    return status_payload()


@app.post("/api/v1/paper/cycle", dependencies=[Depends(require_dashboard_token)])
def run_paper_cycle() -> dict[str, Any]:
    return _run_one_cycle()


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
    """Load heavy research dependencies only when explicitly requested."""
    try:
        from .ml import train_research_model
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Install the optional research dependencies to enable ML evaluation.") from exc

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
