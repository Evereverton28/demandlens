"""The full analysis run.

ledger -> weekly panel -> profiling and trends -> rolling-origin backtest ->
method selection -> final forecasts -> stock risk -> anomalies ->
recommendations -> stored, versioned results.
"""
from __future__ import annotations

import json
import sqlite3
import time
import warnings
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from . import anomalies as anom
from .baselines import all_baselines, naive_scale
from .config import AnalysisConfig
from .evaluate import baseline_p90, run_backtest, summarise, training_rows
from .features import DesignBuilder, add_profile
from .forecasting import QuantileGBM, coherent_mean
from .preprocess import build_panel, stock_on_hand
from .recommend import recommend
from .risk import assess
from .segmentation import profile
from .trends import classify_trends

MIN_WEEKS = 8
CACHE_VERSION = 2     # bump when backtest code changes in a way that alters fold results
KEEP_DETAILED_RUNS = 3


def load_settings(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    return dict(row)


def load_data(conn: sqlite3.Connection, user_id: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    products = pd.read_sql_query(
        """SELECT p.product_id, p.sku, p.name, c.name AS category, p.cost_price, p.selling_price, p.lead_time_days
           FROM products p LEFT JOIN categories c ON c.category_id = p.category_id
           WHERE p.user_id=? AND p.is_active=1""", conn, params=(user_id,))
    mv = pd.read_sql_query(
        """SELECT m.movement_id, m.product_id, m.type, m.quantity, m.unit_price, m.occurred_at, m.source
           FROM stock_movements m JOIN products p ON p.product_id = m.product_id
           WHERE m.user_id=? AND p.is_active=1""", conn, params=(user_id,))
    mv["occurred_at"] = pd.to_datetime(mv["occurred_at"], format="mixed")
    return products, mv


def _json(obj) -> str:
    def conv(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, (pd.Timestamp,)):
            return str(o.date())
        raise TypeError(type(o))
    return json.dumps(obj, default=conv)


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)) and (np.isnan(v) or np.isinf(v)):
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def run_analysis(conn: sqlite3.Connection, user_id: int, cfg: AnalysisConfig | None = None,
                 progress=None, model_dir: str | Path | None = None) -> int:
    cfg = cfg or AnalysisConfig()
    progress = progress or (lambda msg: None)
    started = time.time()
    if model_dir:
        tuned = Path(model_dir) / f"tuned_user{user_id}.json"
        if tuned.exists():
            cfg.gbm_params = {**cfg.gbm_params, **json.loads(tuned.read_text())["best"]["params"]}
    settings = load_settings(conn, user_id)
    cur = conn.execute("INSERT INTO model_runs (user_id, status, algorithm, params_json) VALUES (?, 'running', ?, ?)",
                       (user_id, "HistGradientBoosting quantile (P50/P90) + baselines",
                        _json({"config": asdict(cfg), "settings": settings})))
    run_id = cur.lastrowid
    conn.commit()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            _run(conn, user_id, run_id, cfg, settings, progress, model_dir, started)
    except Exception as exc:
        conn.execute("UPDATE model_runs SET status='failed', error=?, duration_s=? WHERE run_id=?",
                     (str(exc), time.time() - started, run_id))
        conn.commit()
        raise
    return run_id


def _fingerprint(mv: pd.DataFrame, cfg: AnalysisConfig, settings: dict) -> str:
    """Changes whenever the ledger or anything that affects the backtest changes."""
    import hashlib
    demand = mv[mv["type"].isin(["SALE", "RETURN"])]        # the backtest only uses sales and returns
    post_backtest = ("anomaly_", "txn_", "injection_", "runout_", "increase_", "review_")
    model_cfg = {k: v for k, v in asdict(cfg).items() if not k.startswith(post_backtest)}
    key = _json([len(demand), int(demand["movement_id"].max()), float(demand["quantity"].sum()),
                 str(demand["occurred_at"].max()), model_cfg, settings.get("holiday_country"), CACHE_VERSION])
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _run(conn, user_id, run_id, cfg, settings, progress, model_dir, started):
    progress("Loading the ledger")
    products, mv = load_data(conn, user_id)
    if mv.empty or products.empty:
        raise ValueError("There is no sales data yet. Import a sales file or record some sales first.")
    panel = build_panel(mv)
    T = len(panel.weeks)
    if T < MIN_WEEKS:
        raise ValueError(f"At least {MIN_WEEKS} complete weeks of sales are needed; the data covers {T}.")
    t_end = T - 1
    U = panel.units
    cats = dict(zip(products["product_id"], products["category"]))

    progress("Profiling products and testing for trends")
    prof = profile(panel, t_end, cfg).merge(classify_trends(panel, t_end, cfg), on="product_id", how="left")
    patterns = prof.set_index("product_id")["demand_pattern"]

    builder = DesignBuilder(panel, cfg, cats, settings.get("holiday_country"))
    all_rows = builder.rows(0, t_end, require_target=False)

    progress("Backtesting forecast methods")
    cache_dir = Path(model_dir) / "cache" / f"user{user_id}" if model_dir else None
    fp = _fingerprint(mv, cfg, settings)
    if cache_dir and cache_dir.exists():
        for old in cache_dir.glob("*.pkl"):
            if not old.name.startswith(fp):
                old.unlink()
    bt = run_backtest(panel, builder, all_rows, cfg, progress, cache_dir, fp)
    if bt.empty:
        summary = {"note": "Not enough history for a backtest; simple methods are used.",
                   "selection": {p: {"method": m, "baseline": m} for p, m in
                                 [("smooth", "moving_average"), ("erratic", "moving_average"),
                                  ("intermittent", "sba"), ("lumpy", "sba"), ("insufficient", "moving_average")]}}
    else:
        summary = summarise(bt, patterns)
    selection = summary["selection"]

    # ---------------- final forecasts ----------------
    progress("Training the final model and forecasting")
    base = all_baselines(U, t_end, cfg)
    scale = naive_scale(U, t_end)
    gbm_p50 = np.full((U.shape[0], cfg.horizons), np.nan)
    gbm_p90 = np.full_like(gbm_p50, np.nan)
    gbm_mean = np.full_like(gbm_p50, np.nan)
    uses_gbm = any(v["method"] == "gbm" for v in selection.values())
    n_modelled = 0
    if uses_gbm:
        train = training_rows(all_rows, t_end, cfg)
        pred = all_rows[(all_rows["_t"] == t_end) & (all_rows["age"] >= cfg.min_history_weeks)]
        if len(train) and len(pred):
            model = QuantileGBM(cfg.gbm_params, cfg.random_state).fit(add_profile(train, prof, panel), train["_y"].to_numpy())
            p50, p90, mean = model.predict(add_profile(pred, prof, panel))
            ij = (pred["_p"].to_numpy(), pred["_h"].to_numpy() - 1)
            gbm_p50[ij], gbm_p90[ij], gbm_mean[ij] = p50, p90, mean
            if model_dir:
                import joblib
                Path(model_dir).mkdir(parents=True, exist_ok=True)
                joblib.dump(model, Path(model_dir) / f"user{user_id}_run{run_id}.joblib")
    wss = builder.origin["weeks_since_sale"][:, t_end]
    age = t_end - panel.first_idx + 1

    def choose(i: int, pat: str) -> str:
        if wss[i] > cfg.active_window_weeks:
            return "inactive"
        sel = selection.get(pat, selection["insufficient"])
        if sel["method"] == "gbm" and age[i] >= cfg.min_history_weeks and not np.isnan(gbm_p50[i, 0]):
            return "gbm"
        return sel["baseline"] if sel["method"] == "gbm" else sel["method"]

    fc_rows, method_of, fc_arrays = [], {}, {}
    for i, pid in enumerate(panel.product_ids):
        pat = patterns.get(pid, "insufficient")
        m = choose(i, pat)
        method_of[pid] = m
        if m == "inactive":
            p50 = p90 = mean = np.zeros(cfg.horizons)
        elif m == "gbm":
            p50, p90, mean = gbm_p50[i], gbm_p90[i], gbm_mean[i]
            p90 = np.maximum(p90, mean)          # the planning level for a busy week is never below the expected week
            n_modelled += 1
        else:
            p50 = mean = base[m][i]          # the baselines estimate the mean level
            p90 = np.array([baseline_p90(np.array([p50[h]]), np.array([scale[i]]), m, pat, h + 1, summary)[0]
                            if "residual_q90" in summary else p50[h] * 1.5 for h in range(cfg.horizons)])
            mean = coherent_mean(p50, p90, p50)
        fc_arrays[pid] = (p50, p90, mean)
        for h in range(cfg.horizons):
            fc_rows.append((run_id, int(pid), str((panel.weeks[t_end] + pd.Timedelta(weeks=h + 1)).date()),
                            h + 1, float(p50[h]), float(p90[h]), float(mean[h]), m))

    # ---------------- backtest points (chosen method) ----------------
    points = pd.DataFrame()
    if not bt.empty:
        bt = bt.assign(method=bt["product_id"].map(method_of), pattern=bt["product_id"].map(patterns))
        bt = bt[bt["method"].notna() & (bt["method"] != "inactive")]
        pts = []
        for (m, pat, h), g in bt.groupby(["method", "pattern", "horizon"]):
            if m == "gbm":
                p50 = g["gbm"].to_numpy(); p90 = g["gbm_p90"].to_numpy()
                fb = selection.get(pat, selection["insufficient"])["baseline"]
                miss = np.isnan(p50)
                p50 = np.where(miss, g[fb].to_numpy(), p50)
                p90 = np.where(miss, baseline_p90(g[fb].to_numpy(), g["scale"].to_numpy(), fb, pat, h, summary), p90)
            else:
                p50 = g[m].to_numpy()
                p90 = baseline_p90(p50, g["scale"].to_numpy(), m, pat, h, summary)
            pts.append(pd.DataFrame({"product_id": g["product_id"], "week_start": panel.weeks[g["target"]],
                                     "horizon": h, "actual": g["actual"], "p50": p50, "p90": p90, "pattern": pat}))
        points = pd.concat(pts, ignore_index=True)

    # ---------------- stock risk ----------------
    progress("Assessing stock risk")
    stock = stock_on_hand(mv)
    pinfo = products.set_index("product_id")
    prof = prof.set_index("product_id")
    metrics = []
    for pid in panel.product_ids:
        p50, p90, mean = fc_arrays[pid]
        info = pinfo.loc[pid] if pid in pinfo.index else None
        lead = int(info["lead_time_days"]) if info is not None and pd.notna(info["lead_time_days"]) else settings["default_lead_time_days"]
        r = assess(stock.get(pid, np.nan), mean, p90, lead, settings["review_period_days"], settings["overstock_weeks"],
                   _clean(info["cost_price"]) if info is not None else None,
                   _clean(info["selling_price"]) if info is not None else None, cfg.runout_cap_days)
        row = {**prof.loc[pid].to_dict(), **r, "product_id": pid, "lead_time_days": lead, "forecast_method": method_of[pid]}
        metrics.append(row)
    met = pd.DataFrame(metrics)

    # ---------------- anomalies ----------------
    progress("Looking for unusual sales")
    anomaly_eval = {}
    recent_by_pid: dict = {}
    wk = anom.weekly_anomalies(points, cfg) if not points.empty else pd.DataFrame()
    if not points.empty:
        anomaly_eval = anom.injection_evaluation(points, cfg)      # measures the detector week by week
    since = panel.weeks[t_end] - pd.Timedelta(weeks=cfg.txn_window_weeks - 1)
    sales = mv[(mv["type"] == "SALE") & (mv["occurred_at"] < panel.weeks[t_end] + pd.Timedelta(weeks=1))]
    tx = anom.transaction_anomalies(sales[["movement_id", "product_id", "quantity", "occurred_at"]], since, cfg)
    wk, tx = anom.group_events(wk, tx)                             # ...but reports one alarm per event
    # Upsert: a later run may extend an event; the owner's review (status, note) is kept.
    conn.executemany(
        """INSERT INTO anomalies (user_id, run_id, product_id, kind, week_start, movement_id, actual, expected,
                                  score, direction, severity, weeks)
           VALUES (?, ?, ?, 'weekly_sales', ?, 0, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (user_id, product_id, kind, week_start, movement_id) DO UPDATE SET
             run_id=excluded.run_id, actual=excluded.actual, expected=excluded.expected, score=excluded.score,
             direction=excluded.direction, severity=excluded.severity, weeks=excluded.weeks""",
        [(user_id, run_id, int(r.product_id), str(pd.Timestamp(r.week_start).date()), float(r.actual), float(r.expected),
          float(r.score), r.direction, r.severity, int(r.weeks)) for r in wk.itertuples()])
    conn.executemany(
        """INSERT INTO anomalies (user_id, run_id, product_id, kind, week_start, movement_id, actual, expected,
                                  score, direction, severity)
           VALUES (?, ?, ?, 'transaction', ?, ?, ?, ?, ?, 'spike', ?)
           ON CONFLICT (user_id, product_id, kind, week_start, movement_id) DO UPDATE SET
             run_id=excluded.run_id, actual=excluded.actual, expected=excluded.expected, score=excluded.score,
             severity=excluded.severity""",
        [(user_id, run_id, int(r.product_id), str((r.occurred_at - pd.Timedelta(days=r.occurred_at.weekday())).date()),
          int(r.movement_id), float(r.actual), float(r.expected), float(r.score),
          "high" if r.score >= 50 else "moderate") for r in tx.itertuples()])
    # Unreviewed flags that this run did not find again are superseded (for example single weeks that
    # are now part of a longer event); flags the owner confirmed or dismissed are kept as history.
    conn.execute("DELETE FROM anomalies WHERE user_id=? AND status='open' AND run_id IS NOT ?", (user_id, run_id))
    recent_start = str((panel.weeks[t_end] - pd.Timedelta(weeks=3)).date())
    for a in conn.execute(
            """SELECT product_id, kind, week_start, actual, expected, direction, weeks FROM anomalies
               WHERE user_id=? AND status!='dismissed' AND severity='high' AND week_start>=?
               ORDER BY week_start""", (user_id, recent_start)):
        recent_by_pid[a["product_id"]] = dict(a)

    # ---------------- recommendations ----------------
    progress("Writing recommendations")
    rec_rows = []
    for m in met.to_dict("records"):
        m = {k: _clean(v) for k, v in m.items()}
        for r in recommend(m, settings, recent_by_pid.get(m["product_id"]), cfg):
            rec_rows.append((run_id, int(m["product_id"]), r["action"], _clean(r["quantity"]), r["priority"], r["reason"]))

    # ---------------- store ----------------
    cols = ["revenue_52w", "units_52w", "revenue_share", "abc_class", "xyz_class", "cv", "demand_pattern", "adi", "cv2",
            "trend", "trend_slope", "trend_p", "velocity_12w", "weeks_since_sale", "movement_class", "stock_on_hand",
            "days_of_cover", "runout_expected_days", "runout_worst_days", "overstock_units", "overstock_value",
            "overstock_basis", "reorder_qty", "lead_time_days", "forecast_method"]
    conn.executemany(
        f"INSERT INTO product_metrics (run_id, product_id, {', '.join(cols)}) VALUES (?, ?, {', '.join('?' * len(cols))})",
        [(run_id, int(r["product_id"]), *[_clean(r.get(c)) for c in cols]) for r in met.to_dict("records")])
    conn.executemany("INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", fc_rows)
    conn.executemany("INSERT INTO recommendations VALUES (?, ?, ?, ?, ?, ?)", rec_rows)
    if not points.empty:
        conn.executemany("INSERT OR REPLACE INTO backtest_points VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [(run_id, int(r.product_id), str(pd.Timestamp(r.week_start).date()), int(r.horizon),
                           float(r.actual), float(r.p50), float(r.p90)) for r in points.itertuples()])

    flags = conn.execute(
        """SELECT MAX(is_synthetic) AS syn, MAX(is_scenario) AS scn FROM import_batches WHERE user_id=?""",
        (user_id,)).fetchone()
    methods_used = pd.Series(method_of).value_counts().to_dict()
    summary_out = {k: v for k, v in summary.items() if k != "residual_q90"}
    rev_w = np.nansum(panel.revenue, axis=0); units_w = np.nansum(U, axis=0)
    last_price = pd.DataFrame(panel.price.T).ffill().iloc[-1].to_numpy()
    fc_tot = []
    for h in range(cfg.horizons):
        means = np.array([fc_arrays[pid][2][h] for pid in panel.product_ids])
        fc_tot.append({"week": str((panel.weeks[t_end] + pd.Timedelta(weeks=h + 1)).date()),
                       "units": float(means.sum()), "revenue": float(np.nansum(means * last_price))})
    summary_out.update({
        "weekly_totals": [{"week": str(w.date()), "revenue": float(r), "units": float(u)}
                          for w, r, u in zip(panel.weeks, rev_w, units_w)],
        "forecast_totals": fc_tot,
        "data": {"weeks": T, "first_week": str(panel.weeks[0].date()), "last_week": str(panel.weeks[t_end].date()),
                 "products": int(len(panel.product_ids)),
                 "active_products": int(sum(1 for m in method_of.values() if m != "inactive"))},
        "methods_used": methods_used,
        "anomaly_evaluation": anomaly_eval,
        "origin_weeks": [str(panel.weeks[o].date()) for o in summary.get("origins", [])],
    })
    conn.execute(
        """UPDATE model_runs SET status='completed', data_start=?, data_end=?, n_products=?, n_modelled=?,
           metrics_json=?, duration_s=?, uses_synthetic=?, uses_scenario=? WHERE run_id=?""",
        (str(panel.weeks[0].date()), str(panel.weeks[t_end].date()), len(panel.product_ids), n_modelled,
         _json(summary_out), time.time() - started, int(flags["syn"] or 0), int(flags["scn"] or 0), run_id))
    _prune(conn, user_id)
    conn.commit()
    progress("Done")


def _prune(conn, user_id):
    """Keep detailed tables for the most recent runs only; run summaries are kept for history."""
    old = [r[0] for r in conn.execute(
        """SELECT run_id FROM model_runs WHERE user_id=? AND status='completed'
           ORDER BY run_id DESC LIMIT -1 OFFSET ?""", (user_id, KEEP_DETAILED_RUNS))]
    for table in ("forecasts", "product_metrics", "recommendations", "backtest_points"):
        conn.executemany(f"DELETE FROM {table} WHERE run_id=?", [(r,) for r in old])
