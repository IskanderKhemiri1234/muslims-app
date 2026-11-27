"""SillyDev-compatible application entry point for the repository root."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402


if __name__ == "__main__":
    uvicorn.run(
        "iskander_trade.api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "6007")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
