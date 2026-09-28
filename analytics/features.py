"""Feature engineering for the global gradient-boosting model.

A training row is (product, origin week t, horizon h). All features are
computed from data up to and including week t, and the target is demand in
week t+h. ``tests/test_features.py`` checks this property directly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .preprocess import Panel

NUMERIC = ["y0", "y1", "y3", "y_last_year", "rm4", "rm8", "rm12", "rs4", "rs8", "rs12", "nz12",
           "weeks_since_sale", "age", "rel_price", "week_of_year", "month", "holidays", "horizon", "cv"]
CATEGORICAL = ["abc", "xyz", "pattern", "category"]
FEATURES = NUMERIC + CATEGORICAL

ABC_CODE = {"A": 0, "B": 1, "C": 2}
XYZ_CODE = {"X": 0, "Y": 1, "Z": 2}
PATTERN_CODE = {"smooth": 0, "erratic": 1, "intermittent": 2, "lumpy": 3, "insufficient": 4}


def _rolling(df: pd.DataFrame, k: int, fn: str) -> np.ndarray:
    r = df.rolling(k, min_periods=max(2, k // 2))
    return getattr(r, fn)().to_numpy().T


def holiday_counts(weeks: pd.DatetimeIndex, extra: int, country: str | None) -> np.ndarray:
    """Number of public holidays in each week (weeks plus ``extra`` future weeks)."""
    all_weeks = pd.date_range(weeks[0], periods=len(weeks) + extra, freq="7D")
    counts = np.zeros(len(all_weeks))
    if not country:
        return counts
    try:
        import holidays
        cal = holidays.country_holidays(country, years=range(all_weeks[0].year, all_weeks[-1].year + 2))
    except Exception:      # unknown country code or library missing: feature stays at zero
        return counts
    days = pd.to_datetime(list(cal.keys()))
    for d in days:
        i = (d - all_weeks[0]).days // 7
        if 0 <= i < len(all_weeks):
            counts[i] += 1
    return counts


class DesignBuilder:
    """Pre-computes origin features for every (product, week) once."""

    def __init__(self, panel: Panel, cfg: AnalysisConfig, categories: dict | None = None,
                 holiday_country: str | None = None):
        self.panel, self.cfg = panel, cfg
        U = panel.units
        P, T = U.shape
        H = cfg.horizons
        dfU = pd.DataFrame(U.T)

        f = {}
        f["y0"] = U
        f["y1"] = np.hstack([np.full((P, 1), np.nan), U[:, :-1]])
        f["y3"] = np.hstack([np.full((P, 3), np.nan), U[:, :-3]]) if T > 3 else np.full((P, T), np.nan)
        for k in (4, 8, 12):
            f[f"rm{k}"] = _rolling(dfU, k, "mean")
            f[f"rs{k}"] = _rolling(dfU, k, "std")
        nz = pd.DataFrame(np.where(np.isnan(U), np.nan, (np.nan_to_num(U) > 0).astype(float)).T)
        f["nz12"] = _rolling(nz, 12, "mean")

        idx = np.where(np.nan_to_num(U) > 0, np.arange(T)[None, :], np.nan)
        last_sale = pd.DataFrame(idx.T).ffill().to_numpy().T
        f["weeks_since_sale"] = np.arange(T)[None, :] - last_sale
        f["age"] = np.arange(T)[None, :] - panel.first_idx[:, None] + 1.0

        price_ff = pd.DataFrame(panel.price.T).ffill().to_numpy().T
        ref = pd.DataFrame(panel.price.T).rolling(52, min_periods=1).median().ffill().to_numpy().T
        with np.errstate(invalid="ignore", divide="ignore"):
            f["rel_price"] = price_ff / ref
        self.origin = {k: v.astype(np.float32) for k, v in f.items()}

        self.hol = holiday_counts(panel.weeks, H, holiday_country)
        self.all_weeks = pd.date_range(panel.weeks[0], periods=T + H, freq="7D")
        cat = categories or {}
        codes = {name: i for i, name in enumerate(sorted({c for c in cat.values() if c}))}
        self.category = np.array([codes.get(cat.get(pid), np.nan) for pid in panel.product_ids], dtype=np.float32)

    def rows(self, t_min: int, t_max: int, require_target: bool) -> pd.DataFrame:
        """Design rows for origins t in [t_min, t_max] and all horizons (without profile columns)."""
        cfg, panel = self.cfg, self.panel
        U = panel.units
        P, T = U.shape
        ts = np.arange(max(t_min, 0), t_max + 1)
        frames = []
        for h in range(1, cfg.horizons + 1):
            pp, tt = np.meshgrid(np.arange(P), ts, indexing="ij")
            pp, tt = pp.ravel(), tt.ravel()
            age = self.origin["age"][pp, tt]
            wss = self.origin["weeks_since_sale"][pp, tt]
            keep = (age >= cfg.min_feature_weeks) & (wss <= cfg.active_window_weeks)
            target_idx = tt + h
            if require_target:
                keep &= target_idx < T
            pp, tt, target_idx = pp[keep], tt[keep], target_idx[keep]
            d = {k: v[pp, tt] for k, v in self.origin.items()}
            ly = target_idx - 52
            d["y_last_year"] = np.where(ly >= 0, U[pp, np.clip(ly, 0, None)], np.nan).astype(np.float32)
            tw = self.all_weeks[target_idx]
            d["week_of_year"] = tw.isocalendar().week.to_numpy().astype(np.float32)
            d["month"] = tw.month.to_numpy().astype(np.float32)
            d["holidays"] = self.hol[target_idx].astype(np.float32)
            d["horizon"] = np.full(len(pp), h, dtype=np.float32)
            d["category"] = self.category[pp]
            d["_p"] = pp; d["_t"] = tt; d["_h"] = np.full(len(pp), h)
            d["_y"] = np.where(target_idx < T, U[pp, np.clip(target_idx, None, T - 1)], np.nan)
            frames.append(pd.DataFrame(d))
        return pd.concat(frames, ignore_index=True)


def add_profile(rows: pd.DataFrame, prof: pd.DataFrame, panel: Panel) -> pd.DataFrame:
    """Attach profile features (as computed at the fold's origin) by product."""
    pos = pd.Series(np.arange(len(panel.product_ids)), index=panel.product_ids)
    p = prof.set_index(prof["product_id"].map(pos))
    rows = rows.copy()
    rows["abc"] = rows["_p"].map(p["abc_class"].map(ABC_CODE)).astype(np.float32)
    rows["xyz"] = rows["_p"].map(p["xyz_class"].map(XYZ_CODE)).astype(np.float32)
    rows["pattern"] = rows["_p"].map(p["demand_pattern"].map(PATTERN_CODE)).astype(np.float32)
    rows["cv"] = rows["_p"].map(p["cv"]).astype(np.float32)
    return rows
