"""Position sizing and daily risk controls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    quantity: float
    notional_usdt: float
    reason: str


def size_position(
    *, price: float, capital_usdt: float, risk_per_trade_usdt: float,
    stop_loss_pct: float, max_position_notional_usdt: float,
    open_positions: int, max_open_positions: int, daily_pnl_usdt: float,
    max_daily_loss_usdt: float, current_exposure_usdt: float,
) -> RiskDecision:
    if price <= 0:
        return RiskDecision(False, 0.0, 0.0, "Price must be positive.")
    if daily_pnl_usdt <= -max_daily_loss_usdt:
        return RiskDecision(False, 0.0, 0.0, "Daily loss limit reached.")
    if open_positions >= max_open_positions:
        return RiskDecision(False, 0.0, 0.0, "Maximum open positions reached.")
    available = max(0.0, capital_usdt - current_exposure_usdt)
    risk_based_notional = risk_per_trade_usdt / (stop_loss_pct / 100.0)
    notional = min(risk_based_notional, max_position_notional_usdt, available)
    if notional <= 0:
        return RiskDecision(False, 0.0, 0.0, "No available virtual capital.")
    return RiskDecision(True, notional / price, notional, "Risk limits passed.")
