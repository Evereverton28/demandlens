"""Hyperparameter tuning by time-series cross-validation.

Validation origins sit *before* the backtest window, so the backtest remains
an untouched out-of-sample test of the tuned model. Only the median (P50)
model is tuned; the P90 model uses the same settings. The best settings are
saved and picked up by later analysis runs.
"""
from __future__ import annotations

import itertools
import json
import warnings
from pathlib import Path

import numpy as np

from .baselines import naive_scale
from .config import AnalysisConfig
from .evaluate import origins_for, training_rows
from .features import DesignBuilder, add_profile
from .forecasting import QuantileGBM
from .pipeline import load_data, load_settings
from .preprocess import build_panel
from .segmentation import profile

GRID = {"learning_rate": [0.05, 0.1], "max_leaf_nodes": [15, 31, 63], "min_samples_leaf": [20, 40, 80]}


def tuned_params_path(model_dir, user_id) -> Path:
    return Path(model_dir) / f"tuned_user{user_id}.json"


def tune(conn, user_id: int, cfg: AnalysisConfig | None = None, grid: dict | None = None,
         model_dir: str | None = None, progress=print) -> dict:
    cfg = cfg or AnalysisConfig()
    grid = grid or GRID
    warnings.simplefilter("ignore", RuntimeWarning)
    products, mv = load_data(conn, user_id)
    settings = load_settings(conn, user_id)
    panel = build_panel(mv)
    cats = dict(zip(products["product_id"], products["category"]))
    builder = DesignBuilder(panel, cfg, cats, settings.get("holiday_country"))
    T = len(panel.weeks)
    rows = builder.rows(0, T - 1, require_target=False)
    bt = origins_for(panel, cfg)
    first = bt[0] if bt else T - 1
    val_origins = [o for o in (first - 2 * cfg.origin_step, first - cfg.origin_step) if o >= cfg.min_train_weeks]
    if not val_origins:
        raise ValueError("Not enough history before the backtest window to tune.")

    results = []
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    for k, combo in enumerate(combos, 1):
        params = {**cfg.gbm_params, **combo}
        scores = []
        for o in val_origins:
            prof = profile(panel, o, cfg)
            train = training_rows(rows, o, cfg)
            test = rows[(rows["_t"] == o) & (rows["age"] >= cfg.min_history_weeks) & rows["_y"].notna()]
            m = QuantileGBM(params, cfg.random_state, with_mean=False)
            m.quantiles = (0.5,)
            m.fit(add_profile(train, prof, panel), train["_y"].to_numpy())
            A = add_profile(test, prof, panel)
            p50 = m.predict(A)[0]
            scale = naive_scale(panel.units, o)[test["_p"].to_numpy()]
            ok = scale > 0
            err = np.abs(test["_y"].to_numpy() - p50)
            per_product = np.bincount(test["_p"].to_numpy()[ok], weights=err[ok] / scale[ok])
            counts = np.bincount(test["_p"].to_numpy()[ok])
            scores.append(float(np.mean(per_product[counts > 0] / counts[counts > 0])))
        results.append({"params": combo, "mase": float(np.mean(scores))})
        progress(f"[{k}/{len(combos)}] {combo} -> MASE {np.mean(scores):.4f}")
    best = min(results, key=lambda r: r["mase"])
    out = {"best": best, "results": results, "validation_origins": [str(panel.weeks[o].date()) for o in val_origins]}
    if model_dir:
        Path(model_dir).mkdir(parents=True, exist_ok=True)
        tuned_params_path(model_dir, user_id).write_text(json.dumps(out, indent=2))
    return out
