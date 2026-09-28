"""JSON API for the analytical dashboard. Everything reads the latest completed run."""
import json

import numpy as np
import pandas as pd
from flask import Blueprint, current_app, g, jsonify, request

from analytics.pipeline import load_settings, run_analysis
from analytics.preprocess import week_start

from .auth import api_login_required
from .db import get_db, rows
from .jobs import active_job, start_job

bp = Blueprint("api_analytics", __name__, url_prefix="/api")

ACTION_LABELS = {
    "REORDER_URGENT": "Reorder now", "INCREASE_STOCK": "Stock more", "INVESTIGATE": "Check unusual sales",
    "REDUCE": "Reduce or clear stock", "PAUSE_REORDER": "Pause reordering", "REVIEW_RANGE": "Review whether to keep stocking",
}


def _uid():
    return g.user["id"]


def latest_run(db, uid):
    return db.execute("SELECT * FROM model_runs WHERE user_id=? AND status='completed' ORDER BY run_id DESC LIMIT 1",
                      (uid,)).fetchone()


def _no_run():
    return jsonify(run=None)


# ---------------------------------------------------------------- status & runs
@bp.get("/status")
@api_login_required
def status():
    db, uid = get_db(), _uid()
    run = latest_run(db, uid)
    counts = db.execute("""SELECT (SELECT COUNT(*) FROM products WHERE user_id=? AND is_active=1) AS products,
                                  (SELECT COUNT(*) FROM stock_movements WHERE user_id=?) AS movements,
                                  (SELECT MAX(recorded_at) FROM stock_movements WHERE user_id=?) AS last_entry,
                                  (SELECT MAX(is_synthetic) FROM import_batches WHERE user_id=?) AS synthetic,
                                  (SELECT MAX(is_scenario) FROM import_batches WHERE user_id=?) AS scenario""",
                        (uid,) * 5).fetchone()
    job = active_job(db, uid, "analysis")
    last_job = db.execute("SELECT * FROM jobs WHERE user_id=? AND kind='analysis' ORDER BY job_id DESC LIMIT 1",
                          (uid,)).fetchone()
    stale = bool(run and counts["last_entry"] and counts["last_entry"] > run["trained_at"])
    return jsonify(
        run=dict(run_id=run["run_id"], trained_at=run["trained_at"], data_end=run["data_end"],
                 duration_s=run["duration_s"]) if run else None,
        products=counts["products"], movements=counts["movements"], stale=stale,
        synthetic=bool(counts["synthetic"]), scenario=bool(counts["scenario"]),
        job=dict(job) if job else None, last_job=dict(last_job) if last_job else None,
        settings=load_settings(db, uid))


@bp.post("/analysis/run")
@api_login_required
def run():
    uid = _uid()
    app = current_app._get_current_object()

    def work(conn, progress):
        run_id = run_analysis(conn, uid, progress=progress, model_dir=app.config["MODEL_DIR"])
        r = conn.execute("SELECT n_products, n_modelled, duration_s FROM model_runs WHERE run_id=?", (run_id,)).fetchone()
        return f"Analysed {r['n_products']:,} products in {r['duration_s']:.0f} seconds."
    return jsonify(job_id=start_job(app, get_db(), uid, "analysis", work)), 202


# ---------------------------------------------------------------- overview
@bp.get("/overview")
@api_login_required
def overview():
    db, uid = get_db(), _uid()
    run = latest_run(db, uid)
    if not run:
        return _no_run()
    rid = run["run_id"]
    summ = json.loads(run["metrics_json"])
    k = db.execute("""SELECT COUNT(*) AS products,
                        SUM(forecast_method!='inactive') AS active,
                        SUM(runout_worst_days IS NOT NULL AND runout_worst_days <= lead_time_days AND velocity_12w > 0) AS at_risk,
                        SUM(stock_on_hand IS NOT NULL) AS with_stock,
                        SUM(overstock_units IS NOT NULL) AS overstocked,
                        SUM(overstock_value) AS overstock_value
                      FROM product_metrics WHERE run_id=?""", (rid,)).fetchone()
    stock_value = db.execute("""SELECT SUM(m.stock_on_hand * COALESCE(p.cost_price, p.selling_price))
                                FROM product_metrics m JOIN products p ON p.product_id=m.product_id
                                WHERE m.run_id=? AND m.stock_on_hand > 0""", (rid,)).fetchone()[0]
    open_anoms = db.execute("SELECT COUNT(*) FROM anomalies WHERE user_id=? AND status='open'", (uid,)).fetchone()[0]
    wt = summ.get("weekly_totals", [])

    def window(key, a, b):
        return sum(w[key] for w in wt[len(wt) - b:len(wt) - a]) if len(wt) >= b else None
    kpi = dict(k)
    kpi.update(revenue_4w=window("revenue", 0, 4), revenue_prev_4w=window("revenue", 4, 8),
               units_4w=window("units", 0, 4), units_prev_4w=window("units", 4, 8),
               stock_value=stock_value, open_anomalies=open_anoms)
    attention = rows(db.execute(
        f"""SELECT r.product_id, r.action, r.quantity, r.priority, r.reason, p.name, p.sku,
                   m.stock_on_hand, m.runout_expected_days, m.runout_worst_days, m.lead_time_days, m.abc_class
            FROM recommendations r JOIN products p ON p.product_id=r.product_id
            JOIN product_metrics m ON m.run_id=r.run_id AND m.product_id=r.product_id
            WHERE r.run_id=? AND r.priority<=2
            ORDER BY r.priority, COALESCE(m.runout_worst_days, 9999), m.revenue_52w DESC LIMIT 8""", (rid,)))
    for a in attention:
        a["label"] = ACTION_LABELS.get(a["action"], a["action"])
    action_counts = rows(db.execute("SELECT action, COUNT(*) AS n FROM recommendations WHERE run_id=? GROUP BY action",
                                    (rid,)))
    return jsonify(run=dict(run_id=rid, trained_at=run["trained_at"], data_end=run["data_end"]),
                   kpi=kpi, weekly=wt, forecast=summ.get("forecast_totals", []), attention=attention,
                   actions={a["action"]: a["n"] for a in action_counts},
                   best_method=summ.get("best_overall"), currency=load_settings(db, uid)["currency"])


# ---------------------------------------------------------------- products
SORTS = {"revenue": "m.revenue_52w DESC", "runout": "COALESCE(m.runout_worst_days, 99999) ASC",
         "velocity": "m.velocity_12w DESC", "trend": "m.trend_slope DESC", "name": "p.name ASC",
         "overstock": "COALESCE(m.overstock_value, 0) DESC"}


@bp.get("/products")
@api_login_required
def products():
    db, uid = get_db(), _uid()
    run = latest_run(db, uid)
    if not run:
        return _no_run()
    where, args = ["m.run_id=?"], [run["run_id"]]
    if request.args.get("search"):
        where.append("(p.name LIKE ? OR p.sku LIKE ?)")
        s = f"%{request.args['search'].strip()}%"
        args += [s, s]
    for param, col in (("abc", "m.abc_class"), ("pattern", "m.demand_pattern"), ("trend", "m.trend"),
                       ("movement", "m.movement_class")):
        if request.args.get(param):
            where.append(f"{col}=?")
            args.append(request.args[param])
    order = SORTS.get(request.args.get("sort", "revenue"), SORTS["revenue"])
    page = max(int(request.args.get("page", 1)), 1)
    size = 50
    base = f"FROM product_metrics m JOIN products p ON p.product_id=m.product_id WHERE {' AND '.join(where)}"
    total = db.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    items = rows(db.execute(
        f"""SELECT p.product_id, p.sku, p.name, m.revenue_52w, m.abc_class, m.xyz_class, m.demand_pattern, m.trend,
                   m.trend_slope, m.velocity_12w, m.movement_class, m.stock_on_hand, m.days_of_cover,
                   m.runout_expected_days, m.runout_worst_days, m.lead_time_days, m.forecast_method
            {base} ORDER BY {order} LIMIT ? OFFSET ?""", (*args, size, (page - 1) * size)))
    return jsonify(run_id=run["run_id"], items=items, total=total, page=page, pages=max(1, -(-total // size)),
                   currency=load_settings(db, uid)["currency"])


@bp.get("/products/<int:pid>")
@api_login_required
def product(pid):
    db, uid = get_db(), _uid()
    info = db.execute("""SELECT p.*, c.name AS category FROM products p LEFT JOIN categories c ON c.category_id=p.category_id
                         WHERE p.product_id=? AND p.user_id=?""", (pid, uid)).fetchone()
    if not info:
        return jsonify(error="Product not found."), 404
    run = latest_run(db, uid)
    mv = pd.read_sql_query("""SELECT type, quantity, unit_price, occurred_at FROM stock_movements
                              WHERE user_id=? AND product_id=? AND type IN ('SALE','RETURN')""", db, params=(uid, pid))
    history = []
    if not mv.empty:
        mv["week"] = week_start(pd.to_datetime(mv["occurred_at"], format="mixed"))
        mv["signed"] = np.where(mv["type"] == "SALE", 1, -1) * mv["quantity"]
        wk = mv.groupby("week")["signed"].sum().clip(lower=0)
        end = pd.Timestamp(run["data_end"]) if run else wk.index.max()
        idx = pd.date_range(wk.index.min(), max(end, wk.index.min()), freq="7D")
        wk = wk.reindex(idx, fill_value=0)
        history = [{"week": str(w.date()), "units": float(u)} for w, u in wk.items()]
    out = dict(product=dict(info), history=history, currency=load_settings(db, uid)["currency"])
    if run:
        rid = run["run_id"]
        m = db.execute("SELECT * FROM product_metrics WHERE run_id=? AND product_id=?", (rid, pid)).fetchone()
        out["metrics"] = dict(m) if m else None
        out["forecast"] = rows(db.execute("SELECT week_start, horizon, p50, p90, mean, method FROM forecasts "
                                          "WHERE run_id=? AND product_id=? ORDER BY horizon", (rid, pid)))
        out["backtest"] = rows(db.execute("SELECT week_start, horizon, actual, p50, p90 FROM backtest_points "
                                          "WHERE run_id=? AND product_id=? ORDER BY week_start", (rid, pid)))
        recs = rows(db.execute("SELECT action, quantity, priority, reason FROM recommendations WHERE run_id=? AND product_id=? "
                               "ORDER BY priority", (rid, pid)))
        for r in recs:
            r["label"] = ACTION_LABELS.get(r["action"], r["action"])
        out["recommendations"] = recs
    out["anomalies"] = rows(db.execute("SELECT * FROM anomalies WHERE user_id=? AND product_id=? ORDER BY week_start DESC",
                                       (uid, pid)))
    out["movements"] = rows(db.execute("""SELECT type, quantity, unit_price, occurred_at, source, note FROM stock_movements
                                          WHERE user_id=? AND product_id=? ORDER BY occurred_at DESC, movement_id DESC LIMIT 15""",
                                       (uid, pid)))
    return jsonify(out)


# ---------------------------------------------------------------- portfolio
@bp.get("/portfolio")
@api_login_required
def portfolio():
    db, uid = get_db(), _uid()
    run = latest_run(db, uid)
    if not run:
        return _no_run()
    rid = run["run_id"]
    m = pd.read_sql_query("""SELECT m.*, p.name, p.sku, c.name AS category FROM product_metrics m
                             JOIN products p ON p.product_id=m.product_id
                             LEFT JOIN categories c ON c.category_id=p.category_id WHERE m.run_id=?""", db, params=(rid,))
    live = m[m["revenue_52w"] > 0]
    matrix = (live.groupby(["abc_class", live["xyz_class"].fillna("n/a")])
                  .agg(products=("product_id", "size"), revenue=("revenue_52w", "sum")).reset_index()
                  .rename(columns={"xyz_class": "xyz"}).to_dict("records"))
    rev = live["revenue_52w"].sort_values(ascending=False).to_numpy()
    pareto = []
    if len(rev):
        cum = np.cumsum(rev) / rev.sum()
        idx = np.unique(np.linspace(0, len(rev) - 1, min(100, len(rev))).astype(int))
        pareto = [{"share_products": float((i + 1) / len(rev)), "share_revenue": float(cum[i])} for i in idx]
    top = lambda df, col, asc: df.sort_values(col, ascending=asc).head(10)[
        ["product_id", "name", "sku", "trend_slope", "trend_p", "velocity_12w", "revenue_52w", "weeks_since_sale", "abc_class"]
    ].replace({np.nan: None}).to_dict("records")
    ab = m[m["abc_class"].isin(["A", "B"])]
    cat = (live.groupby(live["category"].fillna("Uncategorised"))["revenue_52w"].sum()
               .sort_values(ascending=False).head(12))
    return jsonify(
        matrix=matrix, pareto=pareto,
        patterns=m["demand_pattern"].value_counts().to_dict(),
        movement=m["movement_class"].value_counts().to_dict(),
        trends=m["trend"].value_counts().to_dict(),
        growing=top(ab[ab["trend"] == "growing"], "trend_slope", False),
        declining=top(ab[ab["trend"] == "declining"], "trend_slope", True),
        fast=top(m[m["movement_class"] == "fast"], "velocity_12w", False),
        slow=top(m[(m["movement_class"].isin(["slow", "dormant"])) & (m["abc_class"] != "A") & (m["revenue_52w"] > 0)],
                 "weeks_since_sale", False),
        categories=[{"category": k, "revenue": float(v)} for k, v in cat.items()],
        total_revenue=float(live["revenue_52w"].sum()), currency=load_settings(db, uid)["currency"],
        a_share=float(live.loc[live["abc_class"] == "A", "revenue_52w"].sum() / live["revenue_52w"].sum()) if len(live) else None,
        a_count=int((live["abc_class"] == "A").sum()), live_count=int(len(live)))


# ---------------------------------------------------------------- stock risk
@bp.get("/risk")
@api_login_required
def risk():
    db, uid = get_db(), _uid()
    run = latest_run(db, uid)
    if not run:
        return _no_run()
    rid = run["run_id"]
    cols = """p.product_id, p.name, p.sku, m.abc_class, m.stock_on_hand, m.days_of_cover, m.runout_expected_days,
              m.runout_worst_days, m.lead_time_days, m.reorder_qty, m.overstock_units, m.overstock_value,
              m.overstock_basis, m.trend, m.velocity_12w"""
    base = "FROM product_metrics m JOIN products p ON p.product_id=m.product_id WHERE m.run_id=?"
    settings = load_settings(db, uid)
    horizon = settings["review_period_days"] + max(settings["default_lead_time_days"], 7) + 14
    selling = "AND m.velocity_12w > 0 AND m.runout_worst_days IS NOT NULL"
    stockout = rows(db.execute(f"""SELECT {cols} {base} {selling} AND m.runout_worst_days <= ?
                                   ORDER BY m.runout_worst_days, m.revenue_52w DESC LIMIT 100""", (rid, horizon)))
    at_risk = db.execute(f"SELECT COUNT(*) {base} {selling} AND m.runout_worst_days <= m.lead_time_days", (rid,)).fetchone()[0]
    within = db.execute(f"SELECT COUNT(*) {base} {selling} AND m.runout_worst_days <= ?", (rid, horizon)).fetchone()[0]
    over_total = db.execute(f"SELECT COUNT(*), SUM(m.overstock_value) {base} AND m.overstock_units IS NOT NULL", (rid,)).fetchone()
    over = rows(db.execute(f"""SELECT {cols} {base} AND m.overstock_units IS NOT NULL
                               ORDER BY COALESCE(m.overstock_value, 0) DESC LIMIT 100""", (rid,)))
    unknown = db.execute(f"SELECT COUNT(*) {base} AND m.stock_on_hand IS NULL AND m.forecast_method!='inactive'",
                         (rid,)).fetchone()[0]
    return jsonify(stockout=stockout, overstock=over, stock_unknown=unknown, horizon_days=horizon, at_risk=at_risk,
                   within_horizon=within, overstock_total=over_total[0], overstock_value_total=over_total[1],
                   currency=settings["currency"], overstock_weeks=settings["overstock_weeks"])


# ---------------------------------------------------------------- anomalies & recommendations
@bp.get("/anomalies")
@api_login_required
def anomalies():
    db, uid = get_db(), _uid()
    where, args = ["a.user_id=?", "a.status=?"], [uid, request.args.get("status", "open")]
    for param in ("kind", "severity"):
        if request.args.get(param):
            where.append(f"a.{param}=?")
            args.append(request.args[param])
    items = rows(db.execute(f"""SELECT a.*, p.name, p.sku FROM anomalies a JOIN products p ON p.product_id=a.product_id
                                WHERE {' AND '.join(where)} ORDER BY a.week_start DESC, ABS(a.score) DESC LIMIT 300""", args))
    counts = {r["status"]: r["n"] for r in db.execute(
        "SELECT status, COUNT(*) AS n FROM anomalies WHERE user_id=? GROUP BY status", (uid,))}
    run = latest_run(db, uid)
    evaluation = json.loads(run["metrics_json"]).get("anomaly_evaluation") if run else None
    return jsonify(items=items, counts=counts, evaluation=evaluation)


@bp.patch("/anomalies/<int:aid>")
@api_login_required
def review_anomaly(aid):
    db, d = get_db(), request.get_json(force=True)
    status = d.get("status")
    if status not in ("open", "confirmed", "dismissed"):
        return jsonify(error="Choose confirmed, dismissed or open."), 400
    n = db.execute("UPDATE anomalies SET status=?, note=? WHERE anomaly_id=? AND user_id=?",
                   (status, (d.get("note") or "").strip() or None, aid, _uid())).rowcount
    db.commit()
    return jsonify(ok=bool(n)) if n else (jsonify(error="Not found."), 404)


@bp.get("/recommendations")
@api_login_required
def recommendations():
    db, uid = get_db(), _uid()
    run = latest_run(db, uid)
    if not run:
        return _no_run()
    where, args = "r.run_id=?", [run["run_id"]]
    if request.args.get("action"):
        where += " AND r.action=?"
        args.append(request.args["action"])
    items = rows(db.execute(
        f"""SELECT r.*, p.name, p.sku, m.abc_class, m.stock_on_hand, m.runout_worst_days, m.lead_time_days
            FROM recommendations r JOIN products p ON p.product_id=r.product_id
            JOIN product_metrics m ON m.run_id=r.run_id AND m.product_id=r.product_id
            WHERE {where} ORDER BY r.priority, COALESCE(m.runout_worst_days, 99999), m.revenue_52w DESC LIMIT 300""", args))
    for r in items:
        r["label"] = ACTION_LABELS.get(r["action"], r["action"])
    counts = {r["action"]: r["n"] for r in db.execute(
        "SELECT action, COUNT(*) AS n FROM recommendations WHERE run_id=? GROUP BY action", (run["run_id"],))}
    return jsonify(items=items, counts=counts, labels=ACTION_LABELS)


# ---------------------------------------------------------------- model performance
@bp.get("/model")
@api_login_required
def model():
    db, uid = get_db(), _uid()
    run = latest_run(db, uid)
    history = rows(db.execute("""SELECT run_id, trained_at, status, data_end, n_products, n_modelled, duration_s, error,
                                        uses_synthetic, uses_scenario FROM model_runs WHERE user_id=?
                                 ORDER BY run_id DESC LIMIT 20""", (uid,)))
    if not run:
        return jsonify(run=None, history=history)
    summ = json.loads(run["metrics_json"])
    for k in ("weekly_totals", "forecast_totals"):
        summ.pop(k, None)
    params = json.loads(run["params_json"])
    return jsonify(run=dict(run_id=run["run_id"], trained_at=run["trained_at"], data_start=run["data_start"],
                            data_end=run["data_end"], duration_s=run["duration_s"], n_modelled=run["n_modelled"],
                            n_products=run["n_products"], uses_synthetic=run["uses_synthetic"],
                            uses_scenario=run["uses_scenario"]),
                   summary=summ, config=params.get("config"), history=history)
