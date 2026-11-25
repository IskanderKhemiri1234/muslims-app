"""Deterministic fill-level Spot accounting primitives.

This module deliberately does not convert third-asset commissions into quote
currency. Such fees remain separately visible until a timestamped conversion
price source is implemented and verified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from collections.abc import Iterable


class AccountingError(ValueError):
    """Raised when exchange fills cannot form a valid Spot ledger."""


def _d(value: object, label: str, *, zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AccountingError(f"{label} must be a decimal") from exc
    if not result.is_finite() or (result < 0 if zero else result <= 0):
        raise AccountingError(f"{label} must be {'non-negative' if zero else 'positive'}")
    return result


@dataclass(frozen=True)
class SpotLedger:
    base_asset: str
    quote_asset: str
    base_quantity: Decimal
    quote_cost: Decimal
    realized_quote_pnl: Decimal
    fees_by_asset: dict[str, Decimal] = field(default_factory=dict)

    @property
    def average_entry(self) -> Decimal:
        if self.base_quantity == 0:
            return Decimal(0)
        return self.quote_cost / self.base_quantity


def replay_spot_fills(
    symbol: str, fills: Iterable[dict[str, object]]
) -> SpotLedger:
    """Replay deduplicated Binance fills in chronological order using average cost.

    Required fill fields: side, amount, price, fee_cost, fee_currency, timestamp.
    Base-asset commissions reduce buy inventory and increase sell inventory
    consumed. Quote-asset commissions increase buy cost or reduce sell proceeds.
    Third-asset commissions are reported but never silently FX-converted.
    """
    try:
        base, quote = symbol.replace("-", "/").split("/", 1)
    except ValueError as exc:
        raise AccountingError("symbol must be BASE/QUOTE") from exc
    base, quote = base.upper(), quote.upper()
    if not base or not quote or base == quote:
        raise AccountingError("symbol must contain distinct base and quote assets")

    rows = sorted(fills, key=lambda row: str(row.get("timestamp", "")))
    seen: set[str] = set()
    quantity = Decimal(0)
    cost = Decimal(0)
    realized = Decimal(0)
    fees: dict[str, Decimal] = {}

    for row in rows:
        fill_id = str(row.get("id") or "")
        if not fill_id:
            raise AccountingError("each fill requires a stable exchange trade id")
        if fill_id in seen:
            continue
        seen.add(fill_id)
        side = str(row.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            raise AccountingError("fill side must be buy or sell")
        amount = _d(row.get("amount"), "fill amount")
        price = _d(row.get("price"), "fill price")
        fee = _d(row.get("fee_cost", "0"), "fee", zero=True)
        fee_asset = str(row.get("fee_currency") or "").upper()
        notional = amount * price
        if fee and not fee_asset:
            raise AccountingError("non-zero commission requires fee currency")
        if fee:
            fees[fee_asset] = fees.get(fee_asset, Decimal(0)) + fee

        if side == "buy":
            net_base = amount - (fee if fee_asset == base else Decimal(0))
            if net_base <= 0:
                raise AccountingError("base-asset buy commission consumes the entire fill")
            quote_debit = notional + (fee if fee_asset == quote else Decimal(0))
            quantity += net_base
            cost += quote_debit
        else:
            base_debit = amount + (fee if fee_asset == base else Decimal(0))
            if base_debit > quantity:
                raise AccountingError("sell fill plus base commission exceeds available inventory")
            quote_credit = notional - (fee if fee_asset == quote else Decimal(0))
            if quote_credit < 0:
                raise AccountingError("quote commission exceeds sell proceeds")
            average = cost / quantity if quantity else Decimal(0)
            allocated_cost = average * base_debit
            realized += quote_credit - allocated_cost
            quantity -= base_debit
            cost -= allocated_cost
            if quantity == 0:
                cost = Decimal(0)

    return SpotLedger(
        base_asset=base,
        quote_asset=quote,
        base_quantity=quantity,
        quote_cost=cost,
        realized_quote_pnl=realized,
        fees_by_asset=fees,
    )
