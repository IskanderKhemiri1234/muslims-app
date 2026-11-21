from fastapi.testclient import TestClient

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
