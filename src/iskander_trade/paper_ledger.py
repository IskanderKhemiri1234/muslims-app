"""Independent SQLite paper ledger with Decimal accounting and FIFO reconciliation.

This module is simulation-only. It never imports an exchange client or submits orders.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any

ZERO = Decimal("0")
TOLERANCE = Decimal("0.00000002")
DEFAULT_FEE_RATE = Decimal("0.001")
FEE_QUANTUM = Decimal("0.00000001")


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class LedgerPortfolio:
    initial_cash: Decimal
    cash: Decimal
    realized_pnl: Decimal
    fees_paid: Decimal
    open_positions: int


class PaperLedger:
    """Durable, idempotent quote-currency ledger for simulated spot fills."""

    def __init__(self, database_path: str | Path, initial_cash: Decimal = Decimal("70.00")):
        self.database_path = str(database_path)
        self.initial_cash = _d(initial_cash)
        if self.initial_cash <= ZERO:
            raise ValueError("Initial paper cash must be positive.")
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ledger_account (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    initial_cash TEXT NOT NULL,
                    cash TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL DEFAULT '0',
                    fees_paid TEXT NOT NULL DEFAULT '0'
                );
                CREATE TABLE IF NOT EXISTS ledger_fills (
                    fill_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    quantity TEXT NOT NULL,
                    reference_price TEXT NOT NULL,
                    fill_price TEXT NOT NULL,
                    gross_quote TEXT NOT NULL,
                    fee_quote TEXT NOT NULL,
                    fee_rate TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS ledger_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity TEXT NOT NULL,
                    gross_cost TEXT NOT NULL,
                    entry_fees TEXT NOT NULL
                );
            """)
            db.execute(
                """INSERT OR IGNORE INTO ledger_account
                   (id,initial_cash,cash,realized_pnl,fees_paid) VALUES(1,?,?,?,?)""",
                (str(self.initial_cash), str(self.initial_cash), "0", "0"),
            )
            row = db.execute("SELECT initial_cash FROM ledger_account WHERE id=1").fetchone()
            if _d(row["initial_cash"]) != self.initial_cash:
                raise ValueError("Ledger already exists with a different initial cash balance.")

    @staticmethod
    def fee_for(notional: Decimal, fee_rate: Decimal = DEFAULT_FEE_RATE) -> Decimal:
        notional, fee_rate = _d(notional), _d(fee_rate)
        if notional <= ZERO or fee_rate < ZERO:
            raise ValueError("Notional must be positive and fee rate cannot be negative.")
        return (notional * fee_rate).quantize(FEE_QUANTUM, rounding=ROUND_CEILING)

    def _lots(self, db: sqlite3.Connection) -> dict[str, deque[dict[str, Decimal]]]:
        lots: dict[str, deque[dict[str, Decimal]]] = defaultdict(deque)
        rows = db.execute("SELECT * FROM ledger_fills ORDER BY rowid").fetchall()
        for row in rows:
            qty, gross, fee = _d(row["quantity"]), _d(row["gross_quote"]), _d(row["fee_quote"])
            if row["side"] == "BUY":
                lots[row["symbol"]].append({
                    "quantity": qty, "gross_per_unit": gross / qty, "fee_per_unit": fee / qty,
                })
                continue
            remaining = qty
            while remaining > ZERO and lots[row["symbol"]]:
                lot = lots[row["symbol"]][0]
                matched = min(remaining, lot["quantity"])
                lot["quantity"] -= matched
                remaining -= matched
                if lot["quantity"] == ZERO:
                    lots[row["symbol"]].popleft()
            if remaining > ZERO:
                raise ValueError(f"SELL history exceeds FIFO inventory for {row['symbol']}.")
        return lots

    def record_fill(
        self, *, fill_id: str, symbol: str, side: str, quantity: Decimal,
        reference_price: Decimal, fill_price: Decimal,
        fee_rate: Decimal = DEFAULT_FEE_RATE,
    ) -> Decimal:
        symbol, side = symbol.strip().upper(), side.strip().upper()
        qty, reference, price, rate = map(_d, (quantity, reference_price, fill_price, fee_rate))
        if not fill_id.strip() or not symbol or side not in {"BUY", "SELL"}:
            raise ValueError("Fill ID, symbol and a BUY/SELL side are required.")
        if min(qty, reference, price) <= ZERO or rate < ZERO:
            raise ValueError("Quantity and prices must be positive; fee rate cannot be negative.")
        gross = qty * price
        fee = self.fee_for(gross, rate)
        realized = ZERO
        with self._connect() as db:
            if db.execute("SELECT 1 FROM ledger_fills WHERE fill_id=?", (fill_id,)).fetchone():
                return ZERO
            account = db.execute("SELECT cash FROM ledger_account WHERE id=1").fetchone()
            cash = _d(account["cash"])
            lots = self._lots(db)
            if side == "BUY":
                if cash < gross + fee:
                    raise ValueError("Insufficient paper cash for BUY including fees.")
                cash -= gross + fee
            else:
                remaining = qty
                available = sum((lot["quantity"] for lot in lots[symbol]), ZERO)
                if available < qty:
                    raise ValueError("SELL quantity exceeds available FIFO inventory.")
                for lot in lots[symbol]:
                    if remaining == ZERO:
                        break
                    matched = min(remaining, lot["quantity"])
                    entry_cost = lot["gross_per_unit"] * matched
                    entry_fee = lot["fee_per_unit"] * matched
                    proceeds = gross * matched / qty
                    exit_fee = fee * matched / qty
                    realized += proceeds - exit_fee - entry_cost - entry_fee
                    lot["quantity"] -= matched
                    remaining -= matched
                cash += gross - fee
            db.execute(
                """INSERT INTO ledger_fills
                   (fill_id,symbol,side,quantity,reference_price,fill_price,gross_quote,
                    fee_quote,fee_rate,realized_pnl)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (fill_id, symbol, side, str(qty), str(reference), str(price),
                 str(gross), str(fee), str(rate), str(realized)),
            )
            old = db.execute(
                "SELECT realized_pnl,fees_paid FROM ledger_account WHERE id=1"
            ).fetchone()
            db.execute(
                """UPDATE ledger_account SET cash=?,realized_pnl=?,fees_paid=? WHERE id=1""",
                (str(cash), str(_d(old["realized_pnl"]) + realized),
                 str(_d(old["fees_paid"]) + fee)),
            )
            self._sync_positions(db)
        return realized

    def _sync_positions(self, db: sqlite3.Connection) -> None:
        lots = self._lots(db)
        db.execute("DELETE FROM ledger_positions")
        for symbol, entries in lots.items():
            quantity = sum((x["quantity"] for x in entries), ZERO)
            gross = sum((x["gross_per_unit"] * x["quantity"] for x in entries), ZERO)
            fees = sum((x["fee_per_unit"] * x["quantity"] for x in entries), ZERO)
            if quantity > ZERO:
                db.execute(
                    "INSERT INTO ledger_positions VALUES(?,?,?,?)",
                    (symbol, str(quantity), str(gross), str(fees)),
                )

    def portfolio(self) -> LedgerPortfolio:
        with self._connect() as db:
            row = db.execute("SELECT * FROM ledger_account WHERE id=1").fetchone()
            count = db.execute("SELECT COUNT(*) FROM ledger_positions").fetchone()[0]
        return LedgerPortfolio(
            _d(row["initial_cash"]), _d(row["cash"]), _d(row["realized_pnl"]),
            _d(row["fees_paid"]), int(count),
        )

    def reconcile(self, market_prices: dict[str, Decimal] | None = None) -> dict[str, Any]:
        """Read-only reconciliation of cash, FIFO realized PnL, fees and open lots."""
        prices = {k.upper(): _d(v) for k, v in (market_prices or {}).items()}
        with self._connect() as db:
            account = db.execute("SELECT * FROM ledger_account WHERE id=1").fetchone()
            fills = db.execute("SELECT * FROM ledger_fills ORDER BY rowid").fetchall()
            recorded = {r["symbol"]: r for r in db.execute("SELECT * FROM ledger_positions")}
            lots: dict[str, deque[dict[str, Decimal]]] = defaultdict(deque)
            expected_cash = _d(account["initial_cash"])
            fees = ZERO
            realized = ZERO
            issues: list[str] = []
            valid = True
            for fill in fills:
                qty, price = _d(fill["quantity"]), _d(fill["fill_price"])
                gross, fee = _d(fill["gross_quote"]), _d(fill["fee_quote"])
                if qty <= ZERO or price <= ZERO or abs(qty * price - gross) > TOLERANCE:
                    valid = False
                    issues.append(f"Invalid quantity/price/gross in fill {fill['fill_id']}.")
                    continue
                fees += fee
                if fill["side"] == "BUY":
                    expected_cash -= gross + fee
                    lots[fill["symbol"]].append({
                        "quantity": qty, "gross_per_unit": gross / qty, "fee_per_unit": fee / qty,
                    })
                else:
                    expected_cash += gross - fee
                    remaining = qty
                    sell_realized = ZERO
                    while remaining > ZERO and lots[fill["symbol"]]:
                        lot = lots[fill["symbol"]][0]
                        matched = min(remaining, lot["quantity"])
                        sell_realized += (
                            gross * matched / qty - fee * matched / qty
                            - lot["gross_per_unit"] * matched - lot["fee_per_unit"] * matched
                        )
                        lot["quantity"] -= matched
                        remaining -= matched
                        if lot["quantity"] == ZERO:
                            lots[fill["symbol"]].popleft()
                    if remaining > ZERO:
                        valid = False
                        issues.append(f"Fill {fill['fill_id']} sells more than FIFO inventory.")
                    if abs(sell_realized - _d(fill["realized_pnl"])) > TOLERANCE:
                        valid = False
                        issues.append(f"FIFO realized PnL mismatch in fill {fill['fill_id']}.")
                    realized += sell_realized
            expected_positions = {}
            for symbol, entries in lots.items():
                qty = sum((x["quantity"] for x in entries), ZERO)
                gross = sum((x["gross_per_unit"] * x["quantity"] for x in entries), ZERO)
                entry_fees = sum((x["fee_per_unit"] * x["quantity"] for x in entries), ZERO)
                if qty > ZERO:
                    expected_positions[symbol] = (qty, gross, entry_fees)
            position_match = set(expected_positions) == set(recorded)
            if not position_match:
                issues.append("Open position symbols do not match FIFO fill history.")
            for symbol in set(expected_positions) & set(recorded):
                expected = expected_positions[symbol]
                actual = recorded[symbol]
                if any(abs(a - _d(b)) > TOLERANCE for a, b in zip(
                    expected, (actual["quantity"], actual["gross_cost"], actual["entry_fees"])
                )):
                    position_match = False
                    issues.append(f"Open position {symbol} differs from FIFO fill history.")
            cash_match = abs(expected_cash - _d(account["cash"])) <= TOLERANCE
            fees_match = abs(fees - _d(account["fees_paid"])) <= TOLERANCE
            pnl_match = abs(realized - _d(account["realized_pnl"])) <= TOLERANCE
            if not cash_match:
                issues.append("Cash balance does not match fill history.")
            if not fees_match:
                issues.append("Fees paid do not match fill history.")
            if not pnl_match:
                issues.append("Realized PnL does not match FIFO fill history.")
            market_value = ZERO
            open_cost = ZERO
            marks_complete = True
            for symbol, (qty, gross, entry_fee) in expected_positions.items():
                open_cost += gross + entry_fee
                if symbol not in prices or prices[symbol] <= ZERO:
                    marks_complete = False
                    continue
                market_value += qty * prices[symbol]
            unrealized = market_value - open_cost if marks_complete else None
            equity = _d(account["cash"]) + market_value
            checks = {
                "fills_valid_and_fifo_matched": valid,
                "cash_matches_fills": cash_match,
                "fees_match_fills": fees_match,
                "realized_pnl_matches_fifo": pnl_match,
                "open_positions_match_fifo": position_match,
                "all_positions_marked": marks_complete,
            }
            status = "PASS" if all(checks.values()) else (
                "INCOMPLETE" if all(v for k, v in checks.items() if k != "all_positions_marked")
                and not marks_complete else "FAIL"
            )
            return {
                "status": status, "checks": checks, "issues": issues,
                "initial_cash": str(account["initial_cash"]),
                "cash": str(account["cash"]), "expected_cash": str(expected_cash),
                "fees_paid": str(account["fees_paid"]), "fees_from_fills": str(fees),
                "realized_pnl": str(account["realized_pnl"]), "fifo_realized_pnl": str(realized),
                "open_positions": {s: {"quantity": str(v[0]), "gross_cost": str(v[1]),
                                       "entry_fees": str(v[2])} for s, v in expected_positions.items()},
                "market_value": str(market_value),
                "unrealized_pnl": str(unrealized) if unrealized is not None else None,
                "equity": str(equity),
                "equity_includes_unrealized": marks_complete,
            }
