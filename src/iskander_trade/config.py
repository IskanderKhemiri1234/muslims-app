"""Validated application configuration. Live trading is intentionally unavailable."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Iskander AI Trade")
    mode: str = os.getenv("MODE", "paper").strip().lower()
    quote_asset: str = os.getenv("QUOTE_ASSET", "USDT").upper()
    watchlist: tuple[str, ...] = tuple(
        item.strip().upper()
        for item in os.getenv(
            "WATCHLIST",
            "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT",
        ).split(",")
        if item.strip()
    )
    timeframe: str = os.getenv("TIMEFRAME", "15m")
    context_timeframe: str = os.getenv("CONTEXT_TIMEFRAME", "1h")
    taker_fee_rate: float = _float("TAKER_FEE_RATE", 0.001)
    slippage_rate: float = _float("SLIPPAGE_RATE", 0.0005)
    candle_limit: int = _int("CANDLE_LIMIT", 180)
    poll_seconds: int = _int("POLL_SECONDS", 900)
    virtual_capital_usdt: float = _float("VIRTUAL_CAPITAL_USDT", 70.0)
    risk_per_trade_usdt: float = _float("RISK_PER_TRADE_USDT", 0.35)
    max_daily_loss_usdt: float = _float("MAX_DAILY_LOSS_USDT", 1.40)
    max_open_positions: int = _int("MAX_OPEN_POSITIONS", 3)
    max_position_notional_usdt: float = _float("MAX_POSITION_NOTIONAL_USDT", 20.0)
    stop_loss_pct: float = _float("STOP_LOSS_PCT", 2.0)
    take_profit_pct: float = _float("TAKE_PROFIT_PCT", 4.0)
    ema_fast: int = _int("EMA_FAST", 20)
    ema_slow: int = _int("EMA_SLOW", 50)
    state_path: Path = Path(os.getenv("STATE_PATH", "data/paper_state.json"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    def validate(self) -> None:
        if self.mode != "paper":
            raise ValueError(
                "This build supports paper mode only. Live/testnet order execution "
                "has not been implemented or safety-reviewed."
            )
        if self.timeframe != "15m":
            raise ValueError("The automatic paper scheduler currently requires TIMEFRAME=15m.")
        if self.context_timeframe != "1h":
            raise ValueError("The trade-selection context currently requires CONTEXT_TIMEFRAME=1h.")
        if not 0 <= self.taker_fee_rate < 0.02 or not 0 <= self.slippage_rate < 0.02:
            raise ValueError("TAKER_FEE_RATE and SLIPPAGE_RATE must be between 0 and 0.02.")
        if not self.watchlist:
            raise ValueError("WATCHLIST must contain at least one market.")
        if len(set(self.watchlist)) != len(self.watchlist):
            raise ValueError("WATCHLIST cannot contain duplicate markets.")
        if any(not symbol.endswith(f"/{self.quote_asset}") for symbol in self.watchlist):
            raise ValueError(f"Every WATCHLIST market must be quoted in {self.quote_asset}.")
        if self.ema_fast < 2 or self.ema_slow <= self.ema_fast:
            raise ValueError("EMA_SLOW must be greater than EMA_FAST >= 2.")
        if self.candle_limit < self.ema_slow + 3:
            raise ValueError("CANDLE_LIMIT is too small for the selected EMA periods.")
        if self.poll_seconds < 5:
            raise ValueError("POLL_SECONDS must be at least 5.")
        positive = (
            self.virtual_capital_usdt,
            self.risk_per_trade_usdt,
            self.max_daily_loss_usdt,
            self.max_position_notional_usdt,
            self.stop_loss_pct,
            self.take_profit_pct,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Capital, risk, loss, sizing, stop and target values must be positive.")
        if self.risk_per_trade_usdt > self.virtual_capital_usdt:
            raise ValueError("RISK_PER_TRADE_USDT cannot exceed virtual capital.")
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be at least 1.")
