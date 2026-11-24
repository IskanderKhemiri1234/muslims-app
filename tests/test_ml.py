import numpy as np
import pytest

from iskander_trade.ml import build_features


def test_feature_builder_uses_next_candle_target_and_aligned_rows():
    closes = 100.0 * np.exp(np.cumsum(np.sin(np.arange(120) / 5) * 0.001))
    features, labels = build_features(closes)
    assert features.shape == (89, 5)
    assert labels.shape == (89,)
    assert set(np.unique(labels)).issubset({0, 1})


def test_feature_rows_do_not_depend_on_future_closes():
    closes = 100.0 * np.exp(np.cumsum(np.sin(np.arange(120) / 5) * 0.001))
    changed_future = closes.copy()
    changed_future[85:] *= 1.7

    original_features, _ = build_features(closes)
    changed_features, _ = build_features(changed_future)

    # Row 54 represents information through close index 84; later prices cannot alter it.
    np.testing.assert_allclose(original_features[54], changed_features[54])


def test_feature_builder_rejects_short_or_invalid_series():
    with pytest.raises(ValueError):
        build_features([100.0] * 40)
    with pytest.raises(ValueError):
        build_features([100.0] * 79 + [-1.0])
    with pytest.raises(ValueError):
        build_features([100.0] * 79 + [float("nan")])
