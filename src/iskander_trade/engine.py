"""Multi-symbol paper trading loop backed by the independent FIFO ledger."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from .config import Settings
from .market_data import BinanceMarketData
from .paper_ledger import PaperLedger
from .risk import size_position
from .state import save_state
from .strategy import ema_crossover

log = logging.getLogger("iskander_trade")


class PaperTradingEngine:
    def __init__(self, settings: Settings, market_data: BinanceMarketData, state: dict[str, Any]):
        self.settings = settings
        self.market_data = market_data
        self.state = state
        ledger_path = settings.state_path.with_suffix(".ledger.sqlite3")
        self.ledger = PaperLedger(
            ledger_path, Decimal(str(settings.virtual_capital_usdt))
        )
        audit = self.ledger.reconcile()
        if audit["status"] == "FAIL":
            raise RuntimeError("Paper ledger reconciliation failed; trading cycle blocked.")
        ledger_positions = audit["open_positions"]
        state_positions = state.get("positions", {})
        if set(ledger_positions) != set(state_positions):
            if ledger_positions or state_positions:
                raise RuntimeError(
                    "Legacy JSON positions do not match the FIFO ledger; manual reconciliation "
                    "is required before another paper cycle."
                )
        for symbol, position in state_positions.items():
            if abs(Decimal(str(position["quantity"])) -
                   Decimal(ledger_positions[symbol]["quantity"])) > Decimal("0.00000002"):
                raise RuntimeError(
                    f"Legacy position quantity for {symbol} differs from the FIFO ledger."
                )

    def _roll_day(self) -> None:
        today = date.today().isoformat()
        if self.state["day"] != today:
            self.state["day"] = today
            self.state["daily_pnl_usdt"] = 0.0

    def _close_position(self, symbol: str, price: float, reason: str) -> None:
        position = self.state["positions"][symbol]
        quantity = Decimal(str(position["quantity"]))
        fill_id = f"sell-{symbol}-{position['opened_at']}-{reason}"
        net_pnl = self.ledger.record_fill(
            fill_id=fill_id, symbol=symbol, side="SELL", quantity=quantity,
            reference_price=Decimal(str(price)), fill_price=Decimal(str(price)),
        )
        self.state["positions"].pop(symbol)
        pnl = float(net_pnl)
        self.state["daily_pnl_usdt"] += pnl
        self.state["total_realized_pnl_usdt"] = float(self.ledger.portfolio().realized_pnl)
        self.state["trade_history"].append({
            "symbol": symbol, "side": "sell", "price": price, "quantity": float(quantity),
            "pnl_usdt": pnl, "reason": reason, "closed_at": date.today().isoformat(),
        })
        log.info("PAPER CLOSE %s reason=%s net_pnl=%.4f USDT", symbol, reason, pnl)

    def process_symbol(self, symbol: str) -> None:
        candles = self.market_data.fetch_candles(
            symbol, self.settings.timeframe, self.settings.candle_limit
        )
        price = self.market_data.fetch_last_price(symbol)
        position = self.state["positions"].get(symbol)
        if position:
            change_pct = (price / float(position["entry_price"]) - 1.0) * 100.0
            if change_pct <= -self.settings.stop_loss_pct:
                self._close_position(symbol, price, "stop_loss")
            elif change_pct >= self.settings.take_profit_pct:
                self._close_position(symbol, price, "take_profit")

        signal = ema_crossover(symbol, candles, self.settings.ema_fast, self.settings.ema_slow)
        if signal is None:
            return
        previous = self.state["last_processed_candles"].get(symbol)
        if previous == signal.candle_timestamp:
            return
        self.state["last_processed_candles"][symbol] = signal.candle_timestamp
        position = self.state["positions"].get(symbol)
        if signal.action == "sell" and position:
            self._close_position(symbol, price, "ema_crossover")
            return
        if signal.action != "buy" or position:
            return

        cash = float(self.ledger.portfolio().cash)
        decision = size_position(
            price=price, capital_usdt=cash,
            risk_per_trade_usdt=self.settings.risk_per_trade_usdt,
            stop_loss_pct=self.settings.stop_loss_pct,
            max_position_notional_usdt=self.settings.max_position_notional_usdt,
            open_positions=len(self.state["positions"]),
            max_open_positions=self.settings.max_open_positions,
            daily_pnl_usdt=float(self.state["daily_pnl_usdt"]),
            max_daily_loss_usdt=self.settings.max_daily_loss_usdt,
            current_exposure_usdt=0.0,
        )
        if not decision.allowed:
            log.info("SKIP %s: %s", symbol, decision.reason)
            return
        fill_id = f"buy-{symbol}-{signal.candle_timestamp}"
        fee = self.ledger.fee_for(
            Decimal(str(decision.notional_usdt))
        )
        self.ledger.record_fill(
            fill_id=fill_id, symbol=symbol, side="BUY",
            quantity=Decimal(str(decision.quantity)),
            reference_price=Decimal(str(price)), fill_price=Decimal(str(price)),
        )
        self.state["positions"][symbol] = {
            "entry_price": price, "quantity": decision.quantity,
            "notional_usdt": decision.notional_usdt,
            "entry_fee_usdt": float(fee),
            "stop_loss_price": price * (1 - self.settings.stop_loss_pct / 100),
            "take_profit_price": price * (1 + self.settings.take_profit_pct / 100),
            "opened_at": signal.candle_timestamp,
        }
        self.state["trade_history"].append({
            "symbol": symbol, "side": "buy", "price": price,
            "quantity": decision.quantity, "notional_usdt": decision.notional_usdt,
            "fee_usdt": float(fee), "reason": signal.reason,
            "opened_at": signal.candle_timestamp,
        })
        log.info("PAPER BUY %s qty=%.8f price=%.4f notional=%.2f fee=%.6f",
                 symbol, decision.quantity, price, decision.notional_usdt, float(fee))

    def run_cycle(self) -> None:
        self._roll_day()
        for symbol in self.settings.watchlist:
            try:
                self.process_symbol(symbol)
            except Exception:
                log.exception("Market processing failed for %s; continuing with other symbols", symbol)
        self.state["total_realized_pnl_usdt"] = float(self.ledger.portfolio().realized_pnl)
        save_state(self.settings.state_path, self.state)
