from datetime import UTC, datetime

import pytest

import iskander_trade.api as api_module
from iskander_trade.state import load_state, save_state


def test_state_backup_recovers_previous_valid_snapshot(tmp_path):
    path = tmp_path / "paper.json"
    first = {"positions": {}, "trade_history": [{"symbol": "BTC/USDT"}]}
    second = {"positions": {"ETH/USDT": {"quantity": 1}}, "trade_history": []}
    save_state(path, first)
    save_state(path, second)

    path.write_text("{broken", encoding="utf-8")

    recovered = load_state(path)
    assert recovered["trade_history"] == first["trade_history"]


def test_state_round_trip_keeps_positions_and_history(tmp_path):
    path = tmp_path / "paper.json"
    expected = {
        "positions": {"BTC/USDT": {"quantity": 0.01}},
        "trade_history": [{"symbol": "BTC/USDT", "side": "buy"}],
    }
    save_state(path, expected)
    actual = load_state(path)
    assert actual["positions"] == expected["positions"]
    assert actual["trade_history"] == expected["trade_history"]


def test_scheduler_runs_eight_seconds_after_next_15m_boundary(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 22, 10, 14, 59, tzinfo=UTC)

    monkeypatch.setattr(api_module, "datetime", FrozenDateTime)
    assert api_module._seconds_until_next_closed_candle() == pytest.approx(9.0)


def test_dashboard_token_requires_long_secret(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setenv("DASHBOARD_TOKEN", "short")
    with pytest.raises(HTTPException) as error:
        api_module.require_dashboard_token("Bearer short")
    assert error.value.status_code == 503


def test_dashboard_token_compares_supplied_secret(monkeypatch):
    from fastapi import HTTPException

    secret = "a" * 40
    monkeypatch.setenv("DASHBOARD_TOKEN", secret)
    api_module.require_dashboard_token(f"Bearer {secret}")
    with pytest.raises(HTTPException) as error:
        api_module.require_dashboard_token("Bearer " + "b" * 40)
    assert error.value.status_code == 401
