"""Evaluation: rolling-origin backtest, accuracy metrics and method selection.

Origins are placed every ``origin_step`` weeks across the last
``backtest_weeks`` weeks. At each origin every method sees only data up to
that week (Tashman, 2000; Bergmeir & Benitez, 2012), forecasts 1..H weeks
ahead, and is scored against what actually sold.

Metrics (Hyndman & Koehler, 2006):
* MAE  - mean absolute error, in units
* WAPE - total absolute error / total actual demand
* MASE - absolute error scaled by the product's in-sample naive error,
         averaged per product, then across products
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .baselines import METHODS, all_baselines, naive_scale
from .config import AnalysisConfig
from .features import DesignBuilder, add_profile
from .forecasting import QuantileGBM, coherent_mean
from .preprocess import Panel
from .segmentation import profile

MIN_TRAIN_ROWS = 500
MIN_GROUP_PRODUCTS = 10


def origins_for(panel: Panel, cfg: AnalysisConfig) -> list[int]:
    T = len(panel.weeks)
    weeks = min(cfg.backtest_weeks, (T - cfg.min_train_weeks) // cfg.origin_step * cfg.origin_step)
    if weeks < cfg.origin_step:
        return []
    return list(range(T - 1 - weeks, T - 1 - cfg.horizons + 1, cfg.origin_step))


def training_rows(all_rows: pd.DataFrame, o: int, cfg: AnalysisConfig) -> pd.DataFrame:
    """Rows whose target week is already observed at origin o, from the recent training window."""
    return all_rows[(all_rows["_t"] + all_rows["_h"] <= o) & all_rows["_y"].notna()
                    & (all_rows["_t"] >= o - cfg.train_window_weeks)]


def run_backtest(panel: Panel, builder: DesignBuilder, all_rows: pd.DataFrame, cfg: AnalysisConfig,
                 progress=lambda msg: None, cache_dir: str | Path | None = None, fingerprint: str = "") -> pd.DataFrame:
    """Forecast from every origin with every method and record the outcomes.

    When ``cache_dir`` is given, each origin's results are saved under a
    fingerprint of the data and configuration, so a re-run after a settings
    change (or after an interruption) reuses finished origins.
    """
    U = panel.units
    T = U.shape[1]
    H = cfg.horizons
    out = []
    origins = origins_for(panel, cfg)
    for k, o in enumerate(origins):
        cached = Path(cache_dir) / f"{fingerprint}_o{o}.pkl" if cache_dir else None
        if cached is not None and cached.exists():
            progress(f"Backtest origin {k + 1} of {len(origins)} (reused from cache)")
            fold = pd.read_pickle(cached)
            if "gbm_mean" in fold:
                fold["gbm_mean"] = coherent_mean(fold["gbm"], fold["gbm_p90"], fold["gbm_mean"])
            out.append(fold)
            continue
        progress(f"Backtest origin {k + 1} of {len(origins)} (week of {panel.weeks[o].date()})")
        fold = []
        prof = profile(panel, o, cfg)
        base = all_baselines(U, o, cfg)
        scale = naive_scale(U, o)

        gbm_p50 = np.full((U.shape[0], H), np.nan)
        gbm_p90 = np.full((U.shape[0], H), np.nan)
        gbm_mean = np.full((U.shape[0], H), np.nan)
        train = training_rows(all_rows, o, cfg)
        pred = all_rows[(all_rows["_t"] == o)]
        pred = pred[pred["age"] >= cfg.min_history_weeks]
        if len(train) >= MIN_TRAIN_ROWS and len(pred):
            model = QuantileGBM(cfg.gbm_params, cfg.random_state).fit(add_profile(train, prof, panel), train["_y"].to_numpy())
            p50, p90, mean = model.predict(add_profile(pred, prof, panel))
            ij = (pred["_p"].to_numpy(), pred["_h"].to_numpy() - 1)
            gbm_p50[ij], gbm_p90[ij], gbm_mean[ij] = p50, p90, mean
            del model

        age = o - panel.first_idx + 1
        wss = builder.origin["weeks_since_sale"][:, o]
        active = (age >= 1) & (wss <= cfg.active_window_weeks)
        for h in range(1, H + 1):
            if o + h >= T:
                continue
            idx = np.flatnonzero(active)
            rec = pd.DataFrame({"_p": idx, "origin": o, "horizon": h, "target": o + h,
                                "actual": U[idx, o + h], "scale": scale[idx], "age": age[idx]})
            for m in METHODS:
                rec[m] = base[m][idx, h - 1]
            rec["gbm"] = gbm_p50[idx, h - 1]
            rec["gbm_p90"] = gbm_p90[idx, h - 1]
            rec["gbm_mean"] = gbm_mean[idx, h - 1]
            fold.append(rec)
        fold = pd.concat(fold, ignore_index=True) if fold else pd.DataFrame()
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            fold.to_pickle(cached)
        out.append(fold)
    if not out:
        return pd.DataFrame()
    bt = pd.concat(out, ignore_index=True)
    bt["product_id"] = panel.product_ids[bt["_p"]]
    return bt


def _scores(df: pd.DataFrame, col: str) -> dict:
    e = (df["actual"] - df[col]).abs()
    total = df["actual"].sum()
    ok = df["scale"] > 0
    mase = (e[ok] / df.loc[ok, "scale"]).groupby(df.loc[ok, "product_id"]).mean().mean()
    return {"mae": float(e.mean()), "wape": float(e.sum() / total) if total > 0 else None,
            "mase": None if np.isnan(mase) else float(mase),
            "bias": float(df[col].sum() / total) if total > 0 else None, "n": int(len(df))}


def pinball(actual: np.ndarray, q_pred: np.ndarray, tau: float) -> float:
    d = actual - q_pred
    return float(np.mean(np.maximum(tau * d, (tau - 1) * d)))


def summarise(bt: pd.DataFrame, patterns: pd.Series) -> dict:
    """Accuracy tables, per-pattern method choice and P90 calibration."""
    bt = bt.assign(pattern=bt["product_id"].map(patterns).fillna("insufficient"))
    has_gbm = bt["gbm"].notna()
    methods = list(METHODS) + (["gbm"] if has_gbm.any() else [])
    comp = bt[has_gbm] if has_gbm.any() else bt       # like-for-like comparison set

    overall = {m: _scores(comp, m) for m in methods}
    by_horizon = {int(h): {m: _scores(g, m)["mase"] for m in methods} for h, g in comp.groupby("horizon")}
    by_pattern, selection = {}, {}
    best_overall = min(methods, key=lambda m: overall[m]["mase"] if overall[m]["mase"] is not None else np.inf)
    best_base_overall = min(METHODS, key=lambda m: overall[m]["mase"] if overall[m]["mase"] is not None else np.inf)
    for pat, g in comp.groupby("pattern"):
        s = {m: _scores(g, m) for m in methods}
        by_pattern[pat] = {"products": int(g["product_id"].nunique()), **s}
        valid = {m: v["mase"] for m, v in s.items() if v["mase"] is not None}
        enough = g["product_id"].nunique() >= MIN_GROUP_PRODUCTS and valid
        selection[pat] = {
            "method": min(valid, key=valid.get) if enough else best_overall,
            "baseline": (min((m for m in METHODS if m in valid), key=valid.get) if enough else best_base_overall),
        }
    for pat in ("smooth", "erratic", "intermittent", "lumpy", "insufficient"):
        selection.setdefault(pat, {"method": best_overall, "baseline": best_base_overall})

    # P90 for baseline methods: p50 + 0.9-quantile of scaled residuals, per method/pattern/horizon.
    resid_q = {}
    ok = bt["scale"] > 0
    for m in METHODS:
        r = ((bt.loc[ok, "actual"] - bt.loc[ok, m]) / bt.loc[ok, "scale"])
        q = r.groupby([bt.loc[ok, "pattern"], bt.loc[ok, "horizon"]]).quantile(0.9)
        q_all = r.groupby(bt.loc[ok, "horizon"]).quantile(0.9)
        resid_q[m] = {"by_pattern": {f"{p}|{h}": float(v) for (p, h), v in q.items()},
                      "all": {int(h): float(v) for h, v in q_all.items()}}

    calibration = {}
    if has_gbm.any():
        g = bt[has_gbm]
        mean_scores = _scores(g, "gbm_mean") if "gbm_mean" in g else None
        calibration = {
            "gbm_mean": mean_scores,
            "gbm_p90_coverage": float((g["actual"] <= g["gbm_p90"]).mean()),
            "gbm_p90_pinball": pinball(g["actual"].to_numpy(), g["gbm_p90"].to_numpy(), 0.9),
            "by_pattern": {p: float((x["actual"] <= x["gbm_p90"]).mean()) for p, x in g.groupby("pattern")},
        }

    return {
        "origins": sorted(int(o) for o in bt["origin"].unique()),
        "n_points": int(len(bt)), "n_products": int(bt["product_id"].nunique()),
        "comparison_set": "products eligible for the ML model" if has_gbm.any() else "all active products",
        "overall": overall, "by_horizon": by_horizon, "by_pattern": by_pattern,
        "selection": selection, "residual_q90": resid_q, "calibration": calibration,
        "best_overall": best_overall,
    }


def baseline_p90(p50: np.ndarray, scale: np.ndarray, method: str, pattern: str, horizon: int, summary: dict) -> np.ndarray:
    rq = summary["residual_q90"][method]
    q = rq["by_pattern"].get(f"{pattern}|{horizon}", rq["all"].get(horizon, 0.0))
    return np.maximum(p50 + max(q, 0.0) * np.nan_to_num(scale), p50)
