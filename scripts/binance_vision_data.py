"""Downloader/parser for Binance's official public data.vision SPOT kline archives."""
from __future__ import annotations

import csv
import io
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

BASE = "https://data.binance.vision/data/spot"
SYMBOLS = {
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
    "BNB/USDT": "BNBUSDT",
    "SOL/USDT": "SOLUSDT",
    "XRP/USDT": "XRPUSDT",
    "ADA/USDT": "ADAUSDT",
}
INTERVAL_MS = {"4h": 14_400_000, "1d": 86_400_000}
START = date(2017, 8, 1)


class ArchiveUnavailable(RuntimeError):
    """Raised when Binance's public archive cannot be reached or downloaded."""


def _url(symbol: str, interval: str, period: date, daily: bool = False) -> str:
    code = SYMBOLS[symbol]
    if daily:
        stamp = period.strftime("%Y-%m-%d")
        return f"{BASE}/daily/klines/{code}/{interval}/{code}-{interval}-{stamp}.zip"
    stamp = period.strftime("%Y-%m")
    return f"{BASE}/monthly/klines/{code}/{interval}/{code}-{interval}-{stamp}.zip"


def _download(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "Iskander-AI-Trader-Research/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                if response.status != 200:
                    raise ArchiveUnavailable(f"Binance archive HTTP {response.status}: {url}")
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if (exc.code == 429 or 500 <= exc.code < 600) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise ArchiveUnavailable(f"Binance archive HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise ArchiveUnavailable(f"Binance archive transport failure for {url}: {exc}") from exc
    raise ArchiveUnavailable(f"Binance archive retry limit exceeded: {url}")


def probe_source() -> None:
    """A known BTC archive must be retrievable; never silently switch providers."""
    for interval in ("4h", "1d"):
        url = _url("BTC/USDT", interval, date(2017, 8, 1))
        payload = _download(url)
        if payload is None:
            raise ArchiveUnavailable(f"Required Binance source probe returned 404: {url}")
        try:
            if not _parse_zip(payload, interval, url):
                raise ArchiveUnavailable(f"Binance archive contains no kline rows: {url}")
        except zipfile.BadZipFile as exc:
            raise ArchiveUnavailable(f"Invalid Binance ZIP archive: {url}") from exc


def _parse_zip(payload: bytes, interval: str, source: str) -> list[list[float]]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ArchiveUnavailable(f"Expected one CSV in {source}; found {len(names)}")
            raw = archive.read(names[0]).decode("utf-8-sig")
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise ArchiveUnavailable(f"Could not parse Binance ZIP {source}: {exc}") from exc

    rows: dict[int, list[float]] = {}
    reader = csv.reader(io.StringIO(raw))
    duration = INTERVAL_MS[interval]
    for values in reader:
        if not values or values[0].lower().replace("_", "") in {"opentime", "open_time"}:
            continue
        if len(values) < 6:
            raise ArchiveUnavailable(f"Malformed kline row in {source}: {values!r}")
        try:
            stamp = int(values[0])
            # Binance archive timestamps switched from milliseconds to microseconds in 2025.
            if stamp >= 100_000_000_000_000:
                stamp //= 1000
            row = [float(stamp), *(float(v) for v in values[1:6])]
        except ValueError as exc:
            raise ArchiveUnavailable(f"Non-numeric kline row in {source}: {values!r}") from exc
        if int(row[0]) % duration:
            raise ArchiveUnavailable(f"Off-grid {interval} timestamp in {source}: {row[0]}")
        ts, open_, high, low, close, volume = row
        if min(open_, high, low, close) <= 0 or volume < 0 or high < max(open_, close, low) or low > min(open_, close):
            raise ArchiveUnavailable(f"Invalid OHLCV row in {source}: {row!r}")
        rows[int(ts)] = row
    return [rows[key] for key in sorted(rows)]


def _months(start: date, end: date) -> Iterable[date]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield date(year, month, 1)
        month += 1
        if month == 13:
            year, month = year + 1, 1


def _days(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _fetch_one(args: tuple[str, str, date, bool]) -> tuple[str, str, date, list[list[float]] | None]:
    symbol, interval, period, daily = args
    url = _url(symbol, interval, period, daily=daily)
    payload = _download(url)
    if payload is None:
        return symbol, interval, period, None
    return symbol, interval, period, _parse_zip(payload, interval, url)


def fetch_market_history(symbol: str, today_utc: date) -> dict[str, list[list[float]]]:
    """Load monthly history plus daily archives for the current UTC month."""
    previous_month_end = today_utc.replace(day=1) - timedelta(days=1)
    jobs = []
    for interval in INTERVAL_MS:
        jobs.extend((symbol, interval, month, False) for month in _months(START, previous_month_end))
        jobs.extend((symbol, interval, day, True) for day in _days(today_utc.replace(day=1), today_utc))
    collected: dict[str, dict[int, list[float]]] = {key: {} for key in INTERVAL_MS}
    missing_archives = {key: [] for key in INTERVAL_MS}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_fetch_one, job) for job in jobs]
        for future in as_completed(futures):
            _market, interval, period, rows = future.result()
            if rows is None:
                missing_archives[interval].append(period.isoformat())
                continue
            for row in rows:
                collected[interval][int(row[0])] = row
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    result = {}
    for interval, duration in INTERVAL_MS.items():
        result[interval] = [
            collected[interval][stamp]
            for stamp in sorted(collected[interval])
            if stamp + duration <= now_ms
        ]
        if not result[interval]:
            raise ArchiveUnavailable(f"No completed Binance {interval} candles returned for {symbol}")
    return {
        **result,
        "_missing_archive_periods": missing_archives,  # report-only metadata, not candle data
    }


def gap_count(rows: list[list[float]], duration: int) -> int:
    return sum(1 for left, right in pairwise(rows) if int(right[0]) - int(left[0]) != duration)
