"""Offline ML research utilities; models never place or authorize trades."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_val_score

FEATURE_NAMES = ("return_1", "return_3", "return_6", "volatility_10", "ema_gap")


@dataclass(frozen=True)
class ModelReport:
    samples: int
    accuracy: float
    baseline_accuracy: float
    feature_importance: dict[str, float]


def build_features(closes: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    """Build trailing-only features at t to predict the direction from t to t+1."""
    values = np.asarray(closes, dtype=np.float64)
    if values.ndim != 1 or len(values) < 80:
        raise ValueError("At least 80 closing prices are required.")
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("Closing prices must be finite positive numbers.")

    log_prices = np.log(values)
    returns = np.diff(log_prices, prepend=log_prices[0])

    def trailing_sum(period: int) -> np.ndarray:
        result = np.zeros(len(returns), dtype=np.float64)
        cumulative = np.concatenate(([0.0], np.cumsum(returns)))
        indices = np.arange(len(returns))
        starts = np.maximum(0, indices - period + 1)
        result[:] = cumulative[indices + 1] - cumulative[starts]
        return result

    def ema(period: int) -> np.ndarray:
        alpha = 2.0 / (period + 1.0)
        result = np.empty_like(values)
        result[0] = values[0]
        for index in range(1, len(values)):
            result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
        return result

    volatility = np.zeros_like(values)
    for index in range(9, len(values)):
        volatility[index] = np.std(returns[index - 9:index + 1])

    fast, slow = ema(10), ema(30)
    features = np.column_stack((
        returns,
        trailing_sum(3),
        trailing_sum(6),
        volatility,
        fast / slow - 1.0,
    ))

    # Feature row t only uses closes through t; label t is the move t -> t+1.
    labels = (values[1:] > values[:-1]).astype(np.int8)
    x = features[30:-1]
    y = labels[30:]
    if len(x) != len(y):
        raise ValueError("Feature/label alignment failed.")
    if not np.all(np.isfinite(x)):
        raise ValueError("Feature generation produced non-finite values.")
    return x, y


def train_research_model(closes: Sequence[float]) -> ModelReport:
    x, y = build_features(closes)
    if len(x) < 40 or len(np.unique(y)) < 2:
        raise ValueError("Insufficient directional diversity for model evaluation.")

    model = RandomForestClassifier(
        n_estimators=80,
        max_depth=4,
        min_samples_leaf=8,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=1,
    )
    splitter = TimeSeriesSplit(n_splits=4)
    scores = cross_val_score(
        model, x, y, cv=splitter, scoring="accuracy", n_jobs=1,
        error_score="raise",
    )
    baseline = max(float(np.mean(y)), 1.0 - float(np.mean(y)))
    model.fit(x, y)
    return ModelReport(
        samples=len(y),
        accuracy=float(np.mean(scores)),
        baseline_accuracy=baseline,
        feature_importance=dict(zip(FEATURE_NAMES, model.feature_importances_.tolist())),
    )
