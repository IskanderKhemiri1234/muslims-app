from dataclasses import replace

from fastapi.testclient import TestClient

import iskander_trade.api as api_module
from iskander_trade.api import app

client = TestClient(app)


def test_health_is_public_and_paper_only():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "paper"


def test_dashboard_api_fails_closed_without_token(monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    response = client.get("/api/v1/status")
    assert response.status_code == 503


def test_authenticated_status_exposes_native_dashboard_contract(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "test-dashboard-token")
    monkeypatch.setattr(api_module, "status_payload", lambda: {
        "mode": "paper",
        "virtual_capital_usdt": 70.0,
        "cash_usdt": "70",
        "daily_pnl_usdt": 0.0,
        "total_realized_pnl_usdt": 0.0,
        "positions": {},
        "trade_history": [],
        "accounting": {
            "status": "PASS",
            "cash": "70",
            "expected_cash": "70",
            "fees_paid": "0",
            "fifo_realized_pnl": "0",
            "issues": [],
        },
    })

    response = client.get(
        "/api/v1/status",
        headers={"Authorization": "Bearer test-dashboard-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "paper"
    assert payload["virtual_capital_usdt"] == 70.0
    assert payload["accounting"]["status"] == "PASS"
    assert payload["accounting"]["cash"] == payload["cash_usdt"]


def test_market_endpoint_reports_bad_symbol_without_hiding_other_prices(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "test-dashboard-token")
    monkeypatch.setattr(
        api_module, "settings",
        replace(api_module.settings, watchlist=("BTC/USDT", "ETH/USDT")),
    )

    class FakeMarketData:
        closed = False

        def fetch_last_price(self, symbol):
            if symbol == "ETH/USDT":
                raise ValueError("bad ticker")
            return 65000.0

        def close(self):
            self.closed = True

    fake = FakeMarketData()
    monkeypatch.setattr(api_module, "BinanceMarketData", lambda: fake)

    response = client.get(
        "/api/v1/markets",
        headers={"Authorization": "Bearer test-dashboard-token"},
    )

    assert response.status_code == 200
    rows = response.json()["markets"]
    assert rows[0] == {"symbol": "BTC/USDT", "price": 65000.0}
    assert rows[1] == {
        "symbol": "ETH/USDT",
        "error": "Price temporarily unavailable",
    }
    assert fake.closed



def test_swagger_openapi_declares_bearer_security_for_protected_routes():
    schema = client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["DashboardBearer"]["type"] == "http"
    assert schemes["DashboardBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/api/v1/status"]["get"]["security"] == [
        {"DashboardBearer": []}
    ]
    assert "security" not in schema["paths"]["/health"]["get"]
    assert schema["info"]["version"] == "0.6.1"
    assert "/api/v1/testnet/preflight" in schema["paths"]
    assert "/api/v1/live/orders" in schema["paths"]
    assert schema["paths"]["/api/v1/live/orders"]["post"]["security"] == [
        {"DashboardBearer": []}
    ]


def test_protected_route_accepts_bearer_token_and_rejects_invalid_token(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "rotated-test-token")
    monkeypatch.setattr(api_module, "status_payload", lambda: {"mode": "paper"})

    accepted = client.get(
        "/api/v1/status",
        headers={"Authorization": "Bearer rotated-test-token"},
    )
    rejected = client.get(
        "/api/v1/status",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert accepted.status_code == 200
    assert rejected.status_code == 401
