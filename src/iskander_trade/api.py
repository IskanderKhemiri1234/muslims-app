"""Paper-only FastAPI dashboard API with an optional continuous worker."""
from __future__ import annotations

import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import ccxt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .engine import PaperTradingEngine
from .live_execution import ExecutionDisabled
from .market_data import BinanceMarketData
from .paper_ledger import PaperLedger
from .state import load_state
from .testnet_trade_service import TestnetTradeService

log = logging.getLogger("iskander_trade.api")
settings = Settings()
settings.validate()
dashboard_dir = Path(__file__).resolve().parents[2] / "dashboard"
cycle_lock = threading.Lock()
worker_stop = threading.Event()
worker_thread: threading.Thread | None = None
testnet_stop = threading.Event()
testnet_thread: threading.Thread | None = None
testnet_service = TestnetTradeService()


class TradeConfigRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=24)
    quantity: str = Field(min_length=1, max_length=40)
    entry_price: str = Field(min_length=1, max_length=40)
    stop_loss_price: str = Field(min_length=1, max_length=40)
    take_profit_price: str = Field(min_length=1, max_length=40)


def testnet_worker_loop() -> None:
    while not testnet_stop.is_set():
        try:
            testnet_service.cycle()
        except Exception:
            log.exception("Binance Testnet worker cycle failed; will retry.")
        testnet_stop.wait(max(settings.poll_seconds, 10))


def _close_client(client: BinanceMarketData | None) -> None:
    if client is not None:
        try:
            client.close()
        except Exception:
            log.exception("Market-data client cleanup failed.")


def paper_worker_loop() -> None:
    """Run the existing paper engine continuously inside the API process."""
    while not worker_stop.is_set():
        client: BinanceMarketData | None = None
        try:
            client = BinanceMarketData()
            engine = PaperTradingEngine(settings, client, load_state(settings.state_path))
            while not worker_stop.is_set():
                if cycle_lock.acquire(blocking=False):
                    try:
                        engine.state = load_state(settings.state_path)
                        engine.run_cycle()
                    except Exception:
                        log.exception("Background paper cycle failed; the worker will retry.")
                    finally:
                        cycle_lock.release()
                worker_stop.wait(max(settings.poll_seconds, 5))
        except Exception:
            log.exception("Paper worker could not initialize; retrying after 15 seconds.")
            worker_stop.wait(15)
        finally:
            _close_client(client)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global worker_thread
    global testnet_thread
    enabled = os.getenv("RUN_PAPER_WORKER", "false").strip().lower() in {"1", "true", "yes"}
    testnet_enabled = os.getenv("RUN_BINANCE_TESTNET_WORKER", "false").strip().lower() in {"1", "true", "yes"}
    if testnet_enabled:
        testnet_stop.clear()
        testnet_thread = threading.Thread(
            target=testnet_worker_loop, name="iskander-binance-testnet-worker", daemon=True
        )
        testnet_thread.start()
        log.warning("Binance Spot Testnet worker started; emergency stop remains durable.")
    if enabled:
        worker_stop.clear()
        worker_thread = threading.Thread(
            target=paper_worker_loop, name="iskander-paper-worker", daemon=True
        )
        worker_thread.start()
        log.warning("Continuous worker started in PAPER mode only.")
    try:
        yield
    finally:
        if testnet_enabled:
            testnet_stop.set()
            if testnet_thread is not None:
                testnet_thread.join(timeout=25)
                testnet_thread = None
        if enabled:
            worker_stop.set()
            if worker_thread is not None:
                worker_thread.join(timeout=25)
                worker_thread = None
            log.warning("Continuous paper worker stopped.")


app = FastAPI(title="Iskander AI Trade API", version="0.6.0", lifespan=lifespan)
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
    worker_enabled = os.getenv("RUN_PAPER_WORKER", "false").strip().lower() in {"1", "true", "yes"}
    return {
        "service": settings.app_name,
        "status": "ok",
        "mode": "paper",
        "execution": "continuous_paper_worker" if worker_enabled else "manual_cycle_only",
        "hosting": "infrlo_candidate",
    }


@app.get("/")
def dashboard() -> FileResponse:
    page = dashboard_dir / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Dashboard assets are not installed.")
    return FileResponse(page)


def status_payload() -> dict[str, Any]:
    state = load_state(settings.state_path)
    ledger = PaperLedger(
        settings.state_path.with_suffix(".ledger.sqlite3"),
        initial_cash=Decimal(str(settings.virtual_capital_usdt)),
    )
    accounting = ledger.reconcile()
    state_positions = state.get("positions", {})
    ledger_positions = accounting["open_positions"]
    if set(state_positions) != set(ledger_positions):
        accounting["status"] = "FAIL"
        accounting["issues"].append(
            "JSON engine positions do not match the independent SQLite ledger."
        )
    return {
        "mode": "paper",
        "watchlist": list(settings.watchlist),
        "timeframe": settings.timeframe,
        "virtual_capital_usdt": settings.virtual_capital_usdt,
        "daily_pnl_usdt": state.get("daily_pnl_usdt", 0.0),
        "total_realized_pnl_usdt": float(ledger.portfolio().realized_pnl),
        "cash_usdt": str(ledger.portfolio().cash),
        "positions": state_positions,
        "trade_history": state.get("trade_history", [])[-50:],
        "accounting": accounting,
        "state_persistence": "atomic JSON state + durable SQLite FIFO ledger; back up both files",
    }



@app.get("/api/v1/testnet/preflight", dependencies=[Depends(require_dashboard_token)])
def testnet_preflight() -> dict[str, Any]:
    try:
        return testnet_service.preflight()
    except (ExecutionDisabled, ccxt.BaseError, RuntimeError, ValueError) as exc:
        log.exception("Binance Spot Testnet preflight failed.")
        raise HTTPException(
            status_code=502,
            detail="Binance Spot Testnet connection failed. Check server configuration and logs.",
        ) from exc


@app.get("/api/v1/testnet/trades", dependencies=[Depends(require_dashboard_token)])
def testnet_trades() -> dict[str, Any]:
    return testnet_service.snapshot()


@app.post("/api/v1/testnet/trades", dependencies=[Depends(require_dashboard_token)])
def create_testnet_trade(payload: TradeConfigRequest) -> dict[str, Any]:
    try:
        return testnet_service.create_trade(**payload.model_dump())
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/testnet/trades/{trade_id}/enable", dependencies=[Depends(require_dashboard_token)])
def enable_testnet_trade(trade_id: str) -> dict[str, Any]:
    try:
        return testnet_service.set_enabled(trade_id, True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trade not found.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/testnet/trades/{trade_id}/pause", dependencies=[Depends(require_dashboard_token)])
def pause_testnet_trade(trade_id: str) -> dict[str, Any]:
    try:
        return testnet_service.set_enabled(trade_id, False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trade not found.") from exc


@app.post("/api/v1/testnet/emergency-stop", dependencies=[Depends(require_dashboard_token)])
def testnet_emergency_stop() -> dict[str, Any]:
    return testnet_service.emergency_stop()


@app.post("/api/v1/testnet/emergency-stop/release", dependencies=[Depends(require_dashboard_token)])
def release_testnet_emergency_stop() -> dict[str, Any]:
    return testnet_service.release_emergency_stop()


@app.post("/api/v1/testnet/cycle", dependencies=[Depends(require_dashboard_token)])
def run_testnet_cycle() -> dict[str, Any]:
    try:
        return testnet_service.cycle()
    except Exception as exc:
        log.exception("Testnet cycle failed.")
        raise HTTPException(status_code=502, detail="Testnet cycle failed. Check server logs.") from exc

@app.get("/api/v1/accounting", dependencies=[Depends(require_dashboard_token)])
def accounting_report() -> dict[str, Any]:
    return status_payload()["accounting"]


@app.get("/api/v1/status", dependencies=[Depends(require_dashboard_token)])
def status() -> dict[str, Any]:
    return status_payload()


@app.post("/api/v1/paper/cycle", dependencies=[Depends(require_dashboard_token)])
def run_paper_cycle() -> dict[str, Any]:
    """Run one user-requested paper cycle; never submits exchange orders."""
    if not cycle_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A paper cycle is already running.")
    client: BinanceMarketData | None = None
    try:
        client = BinanceMarketData()
        engine = PaperTradingEngine(settings, client, load_state(settings.state_path))
        engine.run_cycle()
        return {"status": "completed", "mode": "paper", "portfolio": status_payload()}
    except ccxt.BaseError as exc:
        log.exception("Paper cycle market-data error.")
        raise HTTPException(status_code=502, detail="Market data provider could not complete the paper cycle.") from exc
    except Exception as exc:
        log.exception("Unexpected paper cycle failure.")
        raise HTTPException(status_code=500, detail="Unexpected paper-cycle error. Check server logs.") from exc
    finally:
        _close_client(client)
        cycle_lock.release()


@app.get("/api/v1/markets", dependencies=[Depends(require_dashboard_token)])
def markets() -> dict[str, Any]:
    client: BinanceMarketData | None = None
    try:
        client = BinanceMarketData()
        rows = []
        for symbol in settings.watchlist:
            try:
                rows.append({"symbol": symbol, "price": client.fetch_last_price(symbol)})
            except (ccxt.BaseError, RuntimeError, TypeError, ValueError) as exc:
                log.warning("Market ticker failed for %s: %s", symbol, exc)
                rows.append({"symbol": symbol, "error": "Price temporarily unavailable"})
        return {"markets": rows}
    finally:
        _close_client(client)


@app.get("/api/v1/ml/evaluate", dependencies=[Depends(require_dashboard_token)])
def evaluate_ml() -> dict[str, Any]:
    """Run bounded chronological offline evaluation, isolated per market."""
    from .ml import train_research_model

    client: BinanceMarketData | None = None
    reports: list[dict[str, Any]] = []
    try:
        client = BinanceMarketData()
        for symbol in settings.watchlist:
            try:
                candles = client.fetch_candles(symbol, settings.timeframe, settings.candle_limit)
                closed_closes = [float(row[4]) for row in candles[:-1]]
                report = asdict(train_research_model(closed_closes))
                reports.append({
                    "symbol": symbol,
                    **report,
                    "assessment": (
                        "above_baseline"
                        if report["accuracy"] > report["baseline_accuracy"]
                        else "not_above_baseline"
                    ),
                })
            except Exception as exc:
                log.exception("Offline ML evaluation failed for %s.", symbol)
                reports.append({
                    "symbol": symbol,
                    "error": f"Evaluation unavailable ({type(exc).__name__}). Check server logs.",
                })
        return {
            "method": "RandomForest + TimeSeriesSplit",
            "purpose": "offline research only; not a trade signal",
            "reports": reports,
        }
    except Exception as exc:
        log.exception("Offline ML evaluation could not initialize.")
        raise HTTPException(
            status_code=502,
            detail="ML evaluation could not initialize. Check server logs.",
        ) from exc
    finally:
        _close_client(client)


# Live safety endpoints are read-only until the full order manager passes review.
from .live_api import router as live_router
app.include_router(live_router)
