"""Measure production API import footprint without loading optional ML libraries."""
from __future__ import annotations

import resource
import sys

import iskander_trade.api  # noqa: F401


def main() -> int:
    peak_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mib = peak_kib / 1024 if sys.platform != "darwin" else peak_kib / (1024 * 1024)
    print(f"API import peak RSS: {peak_mib:.1f} MiB")
    limit = 400.0
    if peak_mib >= limit:
        print(f"ERROR: import footprint must remain below {limit:.0f} MiB for a 512 MiB host.")
        return 1
    print("PASS: API import footprint is below the 512 MiB host budget.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
