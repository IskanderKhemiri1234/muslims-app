"""Offline ML research utilities; models never place or authorize trades."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

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
    values = np.asarray(closes, dtype=float)
    if values.ndim != 1 or len(values) < 80 or np.any(values <= 0):
        raise ValueError("At least 80 positive closing prices are required.")
    returns = np.diff(np.log(values), prepend=np.log(values[0]))

    def ema(period: int) -> np.ndarray:
        alpha = 2.0 / (period + 1.0)
        result = np.empty_like(values)
        result[0] = values[0]
        for i in range(1, len(values)):
            result[i] = alpha * values[i] + (1.0 - alpha) * result[i - 1]
        return result

    rolling3 = np.convolve(returns, np.ones(3), mode="full")[:len(values)]
    rolling6 = np.convolve(returns, np.ones(6), mode="full")[:len(values)]
    volatility = np.zeros_like(values)
    for i in range(10, len(values)):
        volatility[i] = np.std(returns[i - 9:i + 1])
    fast, slow = ema(10), ema(30)
    features = np.column_stack((returns, rolling3, rolling6, volatility, fast / slow - 1.0))
    labels = (np.diff(values) > 0).astype(int)
    return features[30:-1], labels[30:]


def train_research_model(closes: Sequence[float]) -> ModelReport:
    x, y = build_features(closes)
    if len(x) < 40 or len(np.unique(y)) < 2:
        raise ValueError("Insufficient directional diversity for model evaluation.")
    model = RandomForestClassifier(
        n_estimators=250, max_depth=5, min_samples_leaf=8,
        class_weight="balanced_subsample", random_state=42, n_jobs=-1,
    )
    scores = cross_val_score(model, x, y, cv=TimeSeriesSplit(n_splits=4), scoring="accuracy")
    baseline = max(float(np.mean(y)), 1.0 - float(np.mean(y)))
    model.fit(x, y)
    return ModelReport(
        samples=len(y), accuracy=float(np.mean(scores)), baseline_accuracy=baseline,
        feature_importance=dict(zip(FEATURE_NAMES, model.feature_importances_.tolist())),
    )
