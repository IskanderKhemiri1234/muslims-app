from dataclasses import replace

import pytest

from iskander_trade.config import Settings


def test_live_mode_is_rejected():
    with pytest.raises(ValueError, match="paper mode only"):
        replace(Settings(), mode="live").validate()


def test_valid_default_settings_are_paper_only():
    settings = Settings()
    settings.validate()
    assert settings.mode == "paper"
    assert len(settings.watchlist) > 1
