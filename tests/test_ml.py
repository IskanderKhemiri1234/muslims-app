import numpy as np
import pytest

from iskander_trade.ml import build_features


def test_feature_builder_uses_next_candle_target_and_aligned_rows():
    closes = 100.0 * np.exp(np.cumsum(np.sin(np.arange(120) / 5) * 0.001))
    features, labels = build_features(closes)
    assert features.shape == (89, 5)
    assert labels.shape == (89,)
    assert set(np.unique(labels)).issubset({0, 1})


def test_feature_builder_rejects_short_or_invalid_series():
    with pytest.raises(ValueError):
        build_features([100.0] * 40)
    with pytest.raises(ValueError):
        build_features([100.0] * 79 + [-1.0])
