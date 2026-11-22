"""Regime-aware multi-symbol paper trading loop with auditable decisions."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .config import Settings
from .market_data import BinanceMarketData
from .risk import size_position
from .selection import SelectionDecision, evaluate_long_candidate
from .state import save_state
from .strategy import ema_crossover

log = logging.getLogger("iskander_trade")


class PaperTradingEngine:
    def __init__(self, settings: Settings, market_data: BinanceMarketData, state: dict[str, Any]):
        self.settings = settings
        self.market_data = market_data
        self.state = state
        self.state.setdefault("decision_history", [])

    def _roll_day(self) -> None:
        today = date.today().isoformat()
        if self.state["day"] != today:
            self.state["day"] = today
            self.state["daily_pnl_usdt"] = 0.0

    def _record_decision(
        self,
        symbol: str,
        timestamp: int,
        decision: SelectionDecision,
    ) -> None:
        record = {
            "symbol": symbol,
            "candle_timestamp": timestamp,
            "accepted": decision.accepted,
            "regime": decision.regime,
            "reasons": list(decision.reasons),
            "metrics": decision.metrics,
        }
        history = self.state.setdefault("decision_history", [])
        history.append(record)
        del history[:-500]
        status = "ACCEPT" if decision.accepted else "REJECT"
        log.info(
            "TRADE SELECTION %s %s regime=%s reasons=%s metrics=%s",
            status, symbol, decision.regime, ",".join(decision.reasons), decision.metrics,
        )

    def _close_position(self, symbol: str, market_price: float, reason: str) -> None:
        position = self.state["positions"].pop(symbol)
        quantity = float(position["quantity"])
        exit_price = market_price * (1.0 - self.settings.slippage_rate)
        gross_pnl = (exit_price - float(position["entry_price"])) * quantity
        entry_fee = float(position.get("entry_fee_usdt", 0.0))
        exit_fee = exit_price * quantity * self.settings.taker_fee_rate
        total_fees = entry_fee + exit_fee
        pnl = gross_pnl - total_fees
        self.state["daily_pnl_usdt"] += pnl
        self.state["total_realized_pnl_usdt"] += pnl
        self.state["trade_history"].append({
            "symbol": symbol, "side": "sell", "price": exit_price, "quantity": quantity,
            "gross_pnl_usdt": gross_pnl, "fees_usdt": total_fees,
            "pnl_usdt": pnl, "reason": reason, "closed_at": date.today().isoformat(),
        })
        log.info(
            "PAPER CLOSE %s reason=%s gross=%.4f fees=%.4f net=%.4f USDT",
            symbol, reason, gross_pnl, total_fees, pnl,
        )

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

        try:
            context_candles = self.market_data.fetch_candles(
                symbol, self.settings.context_timeframe, self.settings.candle_limit
            )
            selection = evaluate_long_candidate(
                candles,
                context_candles,
                entry_price=price,
                stop_loss_pct=self.settings.stop_loss_pct,
                take_profit_pct=self.settings.take_profit_pct,
                taker_fee_rate=self.settings.taker_fee_rate,
                slippage_rate=self.settings.slippage_rate,
            )
        except Exception as exc:
            log.exception("Higher-timeframe selection data unavailable for %s", symbol)
            selection = SelectionDecision(
                False, "unavailable", ("higher_timeframe_data_unavailable",),
                {"error": float(len(str(exc)))},
            )
        self._record_decision(symbol, signal.candle_timestamp, selection)
        if not selection.accepted:
            return

        exposure = sum(
            float(item["entry_price"]) * float(item["quantity"])
            for item in self.state["positions"].values()
        )
        round_trip_cost_pct = 2.0 * (
            self.settings.taker_fee_rate + self.settings.slippage_rate
        ) * 100.0
        entry_price = price * (1.0 + self.settings.slippage_rate)
        decision = size_position(
            price=entry_price, capital_usdt=self.settings.virtual_capital_usdt,
            risk_per_trade_usdt=self.settings.risk_per_trade_usdt,
            stop_loss_pct=self.settings.stop_loss_pct,
            max_position_notional_usdt=self.settings.max_position_notional_usdt,
            open_positions=len(self.state["positions"]),
            max_open_positions=self.settings.max_open_positions,
            daily_pnl_usdt=float(self.state["daily_pnl_usdt"]),
            max_daily_loss_usdt=self.settings.max_daily_loss_usdt,
            current_exposure_usdt=exposure,
            cost_pct=round_trip_cost_pct,
        )
        if not decision.allowed:
            log.info("RISK REJECT %s: %s", symbol, decision.reason)
            return

        entry_fee = decision.notional_usdt * self.settings.taker_fee_rate
        self.state["positions"][symbol] = {
            "entry_price": entry_price, "quantity": decision.quantity,
            "notional_usdt": decision.notional_usdt,
            "entry_fee_usdt": entry_fee,
            "stop_loss_price": entry_price * (1 - self.settings.stop_loss_pct / 100),
            "take_profit_price": entry_price * (1 + self.settings.take_profit_pct / 100),
            "opened_at": signal.candle_timestamp,
            "selection_regime": selection.regime,
        }
        self.state["trade_history"].append({
            "symbol": symbol, "side": "buy", "price": entry_price,
            "quantity": decision.quantity, "notional_usdt": decision.notional_usdt,
            "entry_fee_usdt": entry_fee, "reason": signal.reason,
            "selection_regime": selection.regime, "opened_at": signal.candle_timestamp,
        })
        log.info(
            "PAPER BUY %s qty=%.8f price=%.4f notional=%.2f entry_fee=%.4f",
            symbol, decision.quantity, entry_price, decision.notional_usdt, entry_fee,
        )

    def run_cycle(self) -> None:
        self._roll_day()
        for symbol in self.settings.watchlist:
            try:
                self.process_symbol(symbol)
            except Exception:
                log.exception("Market processing failed for %s; continuing with other symbols", symbol)
        save_state(self.settings.state_path, self.state)
