"""Trend detection with the Mann-Kendall test and Sen's slope.

Mann (1945) / Kendall (1975): non-parametric test for a monotonic trend,
with the variance corrected for tied values (weekly sales contain many ties,
especially zeros). Sen (1968): median of all pairwise slopes.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .preprocess import Panel


def mann_kendall(x: np.ndarray) -> tuple[float, float, float]:
    """Return (S, z, two-sided p) for a 1-D series without NaNs."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3:
        return 0.0, 0.0, 1.0
    diff = x[None, :] - x[:, None]                      # diff[i, j] = x_j - x_i
    s = float(np.sign(diff[np.triu_indices(n, k=1)]).sum())
    _, counts = np.unique(x, return_counts=True)
    ties = counts[counts > 1]
    var = (n * (n - 1) * (2 * n + 5) - np.sum(ties * (ties - 1) * (2 * ties + 5))) / 18.0
    if var <= 0:
        return s, 0.0, 1.0
    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    return s, z, p


def sen_slope(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return 0.0
    i, j = np.triu_indices(n, k=1)
    return float(np.median((x[j] - x[i]) / (j - i)))


def classify_trends(panel: Panel, t: int, cfg: AnalysisConfig) -> pd.DataFrame:
    lo = max(0, t - cfg.trend_window + 1)
    rows = []
    for k, pid in enumerate(panel.product_ids):
        if panel.first_idx[k] > t:
            continue
        x = panel.units[k, lo:t + 1]
        x = x[~np.isnan(x)]
        if len(x) < 12 or np.count_nonzero(x) < 3:
            rows.append((pid, "insufficient data", np.nan, np.nan))
            continue
        s, _, p = mann_kendall(x)
        slope = sen_slope(x)
        if p < cfg.trend_alpha and s > 0:
            label = "growing"
        elif p < cfg.trend_alpha and s < 0:
            label = "declining"
        else:
            label = "stable"
        rows.append((pid, label, slope, p))
    return pd.DataFrame(rows, columns=["product_id", "trend", "trend_slope", "trend_p"])
