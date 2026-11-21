"""Command-line entry point."""

from __future__ import annotations

import logging
import time

from .config import Settings
from .engine import PaperTradingEngine
from .market_data import BinanceMarketData
from .state import load_state


def run() -> None:
    settings = Settings()
    settings.validate()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("iskander_trade")
    log.warning("%s starting in PAPER mode only | markets=%s | timeframe=%s",
                settings.app_name, ",".join(settings.watchlist), settings.timeframe)
    log.warning("No real orders can be submitted by this build.")
    market_data = BinanceMarketData()
    engine = PaperTradingEngine(settings, market_data, load_state(settings.state_path))
    try:
        while True:
            engine.run_cycle()
            time.sleep(settings.poll_seconds)
    except KeyboardInterrupt:
        log.info("Shutdown requested.")
    finally:
        market_data.close()


if __name__ == "__main__":
    run()
