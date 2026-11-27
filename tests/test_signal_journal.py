from dataclasses import dataclass

import pytest

from iskander_trade.signal_journal import SignalJournal


@dataclass(frozen=True)
class FakeSignal:
    action: str = "buy"
    symbol: str = "BTC/USDT"
    candle_timestamp: int = 1_800_000_000_000
    reason: str = "EMA20 crossed above EMA50 on a closed candle"
    close_price: float = 65000.25


def test_signal_claim_is_atomic_and_survives_restart(tmp_path):
    path = tmp_path / "signals.sqlite3"
    first = SignalJournal(path)
    signal = FakeSignal()

    assert first.claim(signal) is True
    assert first.claim(signal) is False

    restored = SignalJournal(path)
    assert restored.claim(signal) is False
    saved = restored.get("BTC/USDT", signal.candle_timestamp)
    assert saved is not None
    assert saved["action"] == "buy"
    assert saved["status"] == "detected"


def test_one_strategy_market_candle_cannot_be_claimed_twice_even_if_action_differs(tmp_path):
    journal = SignalJournal(tmp_path / "signals.sqlite3")
    assert journal.claim(FakeSignal(action="buy")) is True
    assert journal.claim(FakeSignal(action="sell")) is False


def test_distinct_symbols_candles_and_strategy_versions_have_distinct_keys(tmp_path):
    journal = SignalJournal(tmp_path / "signals.sqlite3")
    signal = FakeSignal()
    assert journal.claim(signal) is True
    assert journal.claim(FakeSignal(symbol="ETH/USDT")) is True
    assert journal.claim(FakeSignal(candle_timestamp=signal.candle_timestamp + 900_000)) is True
    assert journal.claim(signal, strategy_version="ema20-50-v2") is True


def test_status_update_does_not_create_unclaimed_signal(tmp_path):
    journal = SignalJournal(tmp_path / "signals.sqlite3")
    signal = FakeSignal()
    assert journal.update_status(signal, "rejected") is False
    assert journal.claim(signal) is True
    assert journal.update_status(signal, "testnet_pending", detail={"client_order_id": "safe-id"})
    saved = journal.get(signal.symbol, signal.candle_timestamp)
    assert saved["status"] == "testnet_pending"
    assert '"client_order_id": "safe-id"' in saved["detail_json"]


@pytest.mark.parametrize("status", ["submitted", "live", ""])
def test_unknown_signal_status_is_rejected(tmp_path, status):
    journal = SignalJournal(tmp_path / "signals.sqlite3")
    with pytest.raises(ValueError, match="Unsupported signal status"):
        journal.update_status(FakeSignal(), status)


def test_invalid_signal_is_rejected(tmp_path):
    journal = SignalJournal(tmp_path / "signals.sqlite3")
    with pytest.raises(ValueError):
        journal.claim(FakeSignal(candle_timestamp=0))
