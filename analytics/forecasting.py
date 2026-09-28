"""Global gradient-boosting forecaster: median, 90th percentile and mean.

* P50 and P90: quantile loss on log1p(units). Quantiles are preserved by
  monotonic transforms, so expm1 of a predicted log-quantile is the quantile
  of demand, and the log scale stops high-volume products dominating.
* Mean: Poisson loss on raw units. Weekly medians of skewed demand add up to
  far less than total demand, so cumulative quantities (days of cover,
  expected run-out, overstock) use the mean forecast instead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .features import CATEGORICAL, FEATURES


class QuantileGBM:
    quantiles = (0.5, 0.9)

    def __init__(self, params: dict, random_state: int = 42, with_mean: bool = True):
        self.params = dict(params)
        self.with_mean = with_mean
        self.random_state = random_state
        self.models: dict[float, HistGradientBoostingRegressor] = {}

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> "QuantileGBM":
        cat_mask = [c in CATEGORICAL for c in FEATURES]
        target = np.log1p(np.clip(y, 0, None))
        A = X[FEATURES].to_numpy(dtype=np.float32)
        for q in self.quantiles:
            m = HistGradientBoostingRegressor(loss="quantile", quantile=q, categorical_features=cat_mask,
                                              random_state=self.random_state, **self.params)
            self.models[q] = m.fit(A, target)
        if self.with_mean:
            m = HistGradientBoostingRegressor(loss="poisson", categorical_features=cat_mask,
                                              random_state=self.random_state, **self.params)
            self.models["mean"] = m.fit(A, np.clip(y, 0, None))
        return self

    def predict(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        A = X[FEATURES].to_numpy(dtype=np.float32)
        p50 = np.clip(np.expm1(self.models[0.5].predict(A)), 0, None)
        p90 = np.maximum(np.clip(np.expm1(self.models[0.9].predict(A)), 0, None), p50)   # quantiles must not cross
        mean = self.models["mean"].predict(A) if "mean" in self.models else p50
        return p50, p90, coherent_mean(p50, p90, mean)


def coherent_mean(p50: np.ndarray, p90: np.ndarray, mean: np.ndarray) -> np.ndarray:
    """Make the separately trained mean consistent with the quantiles.

    For non-negative demand D, E[D] >= q * P(D >= q), so the mean is at least
    10% of the 90th percentile and half the median. Separately trained models
    can break this; the bound is applied rather than trusting either blindly.
    """
    return np.maximum.reduce([np.asarray(mean, float), 0.1 * np.asarray(p90, float), 0.5 * np.asarray(p50, float)])
