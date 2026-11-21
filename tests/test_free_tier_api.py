import pytest
from fastapi import HTTPException

import iskander_trade.api as api_module


def test_paper_cycle_is_manual_and_closes_market_client(monkeypatch):
    events = []

    class FakeMarketData:
        def close(self):
            events.append("closed")

    class FakeEngine:
        def __init__(self, settings, market_data, state):
            events.append("created")

        def run_cycle(self):
            events.append("cycled")

    monkeypatch.setattr(api_module, "BinanceMarketData", FakeMarketData)
    monkeypatch.setattr(api_module, "PaperTradingEngine", FakeEngine)
    monkeypatch.setattr(api_module, "load_state", lambda path: {})
    monkeypatch.setattr(api_module, "status_payload", lambda: {"mode": "paper"})

    result = api_module.run_paper_cycle()

    assert result == {"status": "completed", "mode": "paper", "portfolio": {"mode": "paper"}}
    assert events == ["created", "cycled", "closed"]


def test_paper_cycle_rejects_concurrent_execution(monkeypatch):
    assert api_module.cycle_lock.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as exc_info:
            api_module.run_paper_cycle()
        assert exc_info.value.status_code == 409
    finally:
        api_module.cycle_lock.release()
