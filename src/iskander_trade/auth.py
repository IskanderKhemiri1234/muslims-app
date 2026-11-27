"""Shared HTTP Bearer authentication for dashboard and protected API routes."""
from __future__ import annotations

import hmac
import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Declaring a real HTTP Bearer scheme makes Swagger UI expose its Authorize dialog
# and attach the Authorization header to every protected operation.
dashboard_bearer = HTTPBearer(auto_error=False, scheme_name="DashboardBearer")


def require_dashboard_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(dashboard_bearer),
) -> None:
    expected = os.getenv("DASHBOARD_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Dashboard access token is not configured on the server.",
        )

    supplied = credentials.credentials.strip() if credentials else ""
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid dashboard token.")
