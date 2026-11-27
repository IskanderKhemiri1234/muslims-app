"""Shared HTTP Bearer authentication for dashboard and protected API routes."""
from __future__ import annotations

import hmac
import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# A module-level dependency keeps the Bearer security scheme visible in OpenAPI,
# so Swagger UI shows its Authorize dialog and attaches the Authorization header.
dashboard_bearer = HTTPBearer(auto_error=False, scheme_name="DashboardBearer")
dashboard_bearer_dependency = Depends(dashboard_bearer)


def require_dashboard_token(
    credentials: HTTPAuthorizationCredentials | None = dashboard_bearer_dependency,
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
