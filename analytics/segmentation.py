"""Product profiling: ABC, XYZ, demand pattern and sales velocity.

All statistics are computed from the panel up to (and including) week index
``t`` so the same function can profile products as they looked at any past
forecast origin - which keeps the backtest free of look-ahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .preprocess import Panel


def profile(panel: Panel, t: int, cfg: AnalysisConfig) -> pd.DataFrame:
    lo = max(0, t - cfg.segmentation_window + 1)
    U = panel.units[:, lo:t + 1]
    R = panel.revenue[:, lo:t + 1]
    exists = panel.first_idx <= t

    with np.errstate(invalid="ignore", divide="ignore"):
        units_52 = np.nansum(U, axis=1)
        rev_52 = np.nansum(R, axis=1)
        n_obs = np.sum(~np.isnan(U), axis=1)
        mean = np.nanmean(U, axis=1)
        std = np.nanstd(U, axis=1, ddof=1)
        cv = np.where(mean > 0, std / mean, np.nan)

    df = pd.DataFrame({"product_id": panel.product_ids, "revenue_52w": rev_52, "units_52w": units_52,
                       "n_weeks": n_obs, "cv": cv})
    df = df[exists].copy()

    # ABC: cumulative share of revenue, highest first.
    total = df["revenue_52w"].sum()
    order = df["revenue_52w"].sort_values(ascending=False)
    cum = order.cumsum() / total if total > 0 else order * 0
    a, b = cfg.abc_cutoffs
    # A product belongs to A if the cumulative share *before* it is below the cut-off.
    prev = cum.shift(fill_value=0)
    abc = pd.Series(np.where(prev < a, "A", np.where(prev < b, "B", "C")), index=order.index)
    abc[order <= 0] = "C"
    df["abc_class"] = abc
    df["revenue_share"] = df["revenue_52w"] / total if total > 0 else 0.0

    # XYZ: variability of weekly demand (needs 8+ weeks of observations).
    x, y = cfg.xyz_cutoffs
    df["xyz_class"] = np.select([df["cv"] <= x, df["cv"] <= y, df["cv"] > y], ["X", "Y", "Z"], default=None)
    df.loc[df["n_weeks"] < 8, "xyz_class"] = None

    # Demand pattern (Syntetos, Boylan & Croston, 2005).
    pat = demand_patterns(U[exists], cfg)
    df = df.assign(adi=pat["adi"].to_numpy(), cv2=pat["cv2"].to_numpy(), demand_pattern=pat["pattern"].to_numpy())

    # Velocity and recency.
    v_lo = max(0, t - cfg.velocity_window + 1)
    with np.errstate(invalid="ignore"):
        vel = np.nanmean(panel.units[:, v_lo:t + 1], axis=1)
    df["velocity_12w"] = vel[exists]
    df["weeks_since_sale"] = weeks_since_sale(panel.units[:, :t + 1])[exists]
    df["movement_class"] = movement_classes(df, cfg)
    df["age_weeks"] = (t - panel.first_idx[exists] + 1)
    return df.drop(columns="n_weeks").reset_index(drop=True)


def demand_patterns(U: np.ndarray, cfg: AnalysisConfig) -> pd.DataFrame:
    """ADI = observed weeks / weeks with demand; CV^2 of non-zero demand sizes."""
    obs = ~np.isnan(U)
    nz = np.nan_to_num(U) > 0
    n_obs = obs.sum(axis=1)
    n_nz = nz.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        adi = np.where(n_nz > 0, n_obs / n_nz, np.nan)
        sizes = np.where(nz, U, np.nan)
        m = np.nanmean(sizes, axis=1)
        s = np.nanstd(sizes, axis=1, ddof=0)
        cv2 = np.where(n_nz >= 2, (s / m) ** 2, np.nan)
    pattern = np.select(
        [n_nz < 2, (adi < cfg.adi_cutoff) & (cv2 < cfg.cv2_cutoff), (adi < cfg.adi_cutoff),
         (cv2 < cfg.cv2_cutoff)],
        ["insufficient", "smooth", "erratic", "intermittent"], default="lumpy")
    return pd.DataFrame({"adi": adi, "cv2": cv2, "pattern": pattern})


def weeks_since_sale(U: np.ndarray) -> np.ndarray:
    T = U.shape[1]
    nz = np.nan_to_num(U) > 0
    last = np.where(nz.any(axis=1), T - 1 - np.argmax(nz[:, ::-1], axis=1), -1)
    return np.where(last >= 0, T - 1 - last, T)


def movement_classes(df: pd.DataFrame, cfg: AnalysisConfig) -> np.ndarray:
    """fast / medium / slow by velocity quartiles among non-dormant products; dormant if no recent sale."""
    dormant = df["weeks_since_sale"] >= cfg.dormant_weeks
    live = df.loc[~dormant, "velocity_12w"]
    if len(live) >= 4:
        q1, q3 = live.quantile([0.25, 0.75])
    else:
        q1 = q3 = live.median() if len(live) else 0
    cls = np.where(df["velocity_12w"] >= q3, "fast", np.where(df["velocity_12w"] <= q1, "slow", "medium"))
    return np.where(dormant, "dormant", cls)
