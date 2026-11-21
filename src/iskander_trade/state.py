"""Atomic JSON persistence for paper positions and performance."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any


def empty_state() -> dict[str, Any]:
    return {
        "day": date.today().isoformat(), "daily_pnl_usdt": 0.0,
        "total_realized_pnl_usdt": 0.0, "positions": {},
        "last_processed_candles": {}, "trade_history": [],
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("positions"), dict):
        raise ValueError("Paper state file has an invalid structure; refusing to overwrite it.")
    for key, value in empty_state().items():
        data.setdefault(key, value)
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
