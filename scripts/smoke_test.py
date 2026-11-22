"""Post-deployment smoke test for the Iskander AI Trade paper service.

Usage:
  BASE_URL=https://your-host.example DASHBOARD_TOKEN=<secret> python scripts/smoke_test.py

This script never submits a paper cycle or any exchange order.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_MARKETS = {
    "BTC/USDT", "ETH/USDT", "BNB/USDT",
    "SOL/USDT", "XRP/USDT", "ADA/USDT",
}


def get_json(base_url: str, path: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{base_url.rstrip('/')}{path}", headers=headers)
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} did not return a JSON object.")
    return payload


def main() -> int:
    base_url = os.getenv("BASE_URL", "").strip()
    token = os.getenv("DASHBOARD_TOKEN", "")
    if not base_url.startswith("https://"):
        print("FAIL: BASE_URL must be the deployed HTTPS URL.")
        return 2
    if len(token) < 32:
        print("FAIL: DASHBOARD_TOKEN must contain at least 32 characters.")
        return 2

    try:
        health = get_json(base_url, "/health")
        assert health.get("status") == "ok", "health status is not ok"
        assert health.get("mode") == "paper", "service is not in paper mode"
        assert health.get("execution") == "automatic_15m", "automatic scheduler is disabled"
        assert health.get("scheduler") == "enabled", "scheduler has not started"
        assert health.get("timeframe") == "15m", "timeframe is not 15m"
        assert set(health.get("watchlist", [])) == EXPECTED_MARKETS, "six-market watchlist mismatch"
        print("PASS: HTTPS health, paper-only mode, scheduler and six markets")

        try:
            get_json(base_url, "/api/v1/status")
        except HTTPError as error:
            assert error.code == 401, f"unauthenticated status returned HTTP {error.code}, expected 401"
        else:
            raise AssertionError("protected status endpoint accepted a request without a token")
        print("PASS: dashboard API rejects unauthenticated requests")

        status = get_json(base_url, "/api/v1/status", token)
        assert status.get("mode") == "paper", "authenticated status is not paper mode"
        assert status.get("timeframe") == "15m", "authenticated status timeframe mismatch"
        assert float(status.get("virtual_capital_usdt", 0)) == 70.0, "virtual capital mismatch"
        assert float(status.get("risk_per_trade_usdt", 0)) == 0.35, "planned risk mismatch"
        assert isinstance(status.get("positions"), dict), "positions payload is invalid"
        assert isinstance(status.get("trade_history"), list), "trade history payload is invalid"
        print("PASS: authenticated status and paper risk configuration")

        markets = get_json(base_url, "/api/v1/markets", token).get("markets", [])
        assert {item.get("symbol") for item in markets} == EXPECTED_MARKETS, "market endpoint did not return all six symbols"
        print("PASS: public market-data adapter responds for all six configured symbols")
        print("SAFE: no paper cycle was triggered; no exchange order endpoint was called")
        return 0
    except (AssertionError, HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
