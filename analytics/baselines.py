"""Baseline forecasting methods, vectorised across all products.

Each function takes the demand matrix U [products x weeks] (NaN before a
product's first sale) and an origin index t, and returns forecasts for weeks
t+1 .. t+H as an array [products x H]. Only data up to and including week t
is used.

* seasonal naive  - same week last year (falls back to the last observed week)
* moving average  - mean of the last k weeks
* SES             - simple exponential smoothing, ETS(A,N,N); alpha chosen per
                    product by minimising in-sample one-step squared error
* SBA             - Croston's method with the Syntetos-Boylan bias correction,
                    for intermittent demand
"""
from __future__ import annotations

import numpy as np

from .config import AnalysisConfig

METHODS = ("seasonal_naive", "moving_average", "ses", "sba")


def seasonal_naive(U: np.ndarray, t: int, H: int) -> np.ndarray:
    last = U[:, t]
    out = np.empty((U.shape[0], H))
    for h in range(1, H + 1):
        idx = t + h - 52
        if idx >= 0:
            val = U[:, idx]
            out[:, h - 1] = np.where(np.isnan(val), last, val)
        else:
            out[:, h - 1] = last
    return np.nan_to_num(out)


def moving_average(U: np.ndarray, t: int, H: int, k: int) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        m = np.nanmean(U[:, max(0, t - k + 1):t + 1], axis=1)
    return np.repeat(np.nan_to_num(m)[:, None], H, axis=1)


def ses(U: np.ndarray, t: int, H: int, alphas=(0.1, 0.2, 0.3, 0.5)) -> np.ndarray:
    a = np.asarray(alphas)[None, :]
    P = U.shape[0]
    level = np.full((P, a.shape[1]), np.nan)
    sse = np.zeros((P, a.shape[1]))
    n = np.zeros(P)
    for j in range(t + 1):
        y = U[:, j][:, None]
        seen = ~np.isnan(level)
        obs = ~np.isnan(y)
        err = np.where(seen & obs, y - level, 0.0)
        sse += err ** 2
        n += (seen[:, 0] & obs[:, 0])
        level = np.where(seen & obs, level + a * err, level)
        level = np.where(~seen & obs, np.broadcast_to(y, level.shape), level)
    best = np.argmin(sse, axis=1)
    fc = level[np.arange(P), best]
    fc = np.where(n < 2, U[:, t], fc)       # too short to estimate alpha: last value
    return np.repeat(np.nan_to_num(fc)[:, None], H, axis=1)


def sba(U: np.ndarray, t: int, H: int, alpha: float = 0.1) -> np.ndarray:
    P = U.shape[0]
    z = np.full(P, np.nan)       # smoothed demand size
    p = np.full(P, np.nan)       # smoothed interval between demands
    q = np.zeros(P)              # periods since the last demand
    for j in range(t + 1):
        y = U[:, j]
        obs = ~np.isnan(y)
        q = np.where(obs, q + 1, q)
        pos = obs & (y > 0)
        init = pos & np.isnan(z)
        upd = pos & ~np.isnan(z)
        z = np.where(init, y, np.where(upd, z + alpha * (y - z), z))
        p = np.where(init, q, np.where(upd, p + alpha * (q - p), p))
        q = np.where(pos, 0, q)
    with np.errstate(invalid="ignore", divide="ignore"):
        fc = (1 - alpha / 2) * z / p
    return np.repeat(np.nan_to_num(fc)[:, None], H, axis=1)


def all_baselines(U: np.ndarray, t: int, cfg: AnalysisConfig) -> dict[str, np.ndarray]:
    H = cfg.horizons
    return {
        "seasonal_naive": seasonal_naive(U, t, H),
        "moving_average": moving_average(U, t, H, cfg.moving_average_window),
        "ses": ses(U, t, H, cfg.ses_alphas),
        "sba": sba(U, t, H, cfg.croston_alpha),
    }


def naive_scale(U: np.ndarray, t: int) -> np.ndarray:
    """In-sample MAE of the one-step naive forecast up to week t (the MASE denominator)."""
    X = U[:, :t + 1]
    with np.errstate(invalid="ignore"):
        return np.nanmean(np.abs(np.diff(X, axis=1)), axis=1)
