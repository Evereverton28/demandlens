"""Anomaly detection.

Weekly sales anomalies are judged against the forecasting model's expectation
(Chandola, Banerjee & Kumar, 2009: contextual anomalies). For each product,
the residual r = actual - P50 is converted into a modified z-score
(Iglewicz & Hoaglin, 1993):

    z = 0.6745 * (r - median(r)) / MAD(r)

Rules, chosen per demand pattern:
* spike - z > 3.5, and the week reached at least twice the model's own P90
  (a busy week) by at least ``anomaly_min_units`` units. Without the P90 test,
  products that usually sell nothing are flagged for any small sale;
* for products that only sell now and then (intermittent and lumpy), the
  typical forecast week is zero, so almost any sale clears the tests above.
  Such a week is judged against the product's own selling weeks instead: it
  must also reach ``anomaly_sporadic_multiple`` times the upper quartile of
  its non-zero weeks in the window. The upper quartile, not the median,
  because occasional big weeks are normal for these products;
* drop  - z < -3.5 and at least ``anomaly_min_units`` below expectation, only
  for smooth and erratic products. For intermittent and lumpy products,
  near-empty weeks are the normal pattern (Syntetos, Boylan & Croston, 2005),
  so a "drop" carries no information.
* severity is "high" for a spike of at least five times P90 or a drop with
  z < -7, otherwise "moderate".

Transaction anomalies: a single sale at least 10x the product's usual sale
size and larger than any earlier sale of that product - the signature of a
data-entry error such as an extra zero, or of a one-off bulk order.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .config import AnalysisConfig

DROP_PATTERNS = ("smooth", "erratic")
SPORADIC_PATTERNS = ("intermittent", "lumpy")
HIGH_Z = 7.0          # drop severity
TXN_MULTIPLE = 10


def robust_z(values: pd.Series, groups: pd.Series) -> pd.Series:
    med = values.groupby(groups).transform("median")
    dev = (values - med).abs()
    mad = dev.groupby(groups).transform("median")
    mean_ad = dev.groupby(groups).transform("mean")
    z = pd.Series(np.nan, index=values.index)
    ok = mad > 0
    z[ok] = 0.6745 * (values[ok] - med[ok]) / mad[ok]
    alt = ~ok & (mean_ad > 0)                    # MAD is zero when most residuals are identical
    z[alt] = (values[alt] - med[alt]) / (1.253314 * mean_ad[alt])
    return z


def weekly_anomalies(points: pd.DataFrame, cfg: AnalysisConfig) -> pd.DataFrame:
    """points: product_id, week_start, actual, p50, p90, pattern. Returns flagged weeks."""
    cols = ["product_id", "week_start", "actual", "expected", "score", "direction", "severity"]
    pts = points.copy()
    pts["resid"] = pts["actual"] - pts["p50"]
    n = pts.groupby("product_id")["resid"].transform("size")
    pts = pts[n >= cfg.anomaly_min_points]
    if pts.empty:
        return pd.DataFrame(columns=cols)
    pts["score"] = robust_z(pts["resid"], pts["product_id"])
    spike = ((pts["score"] > cfg.anomaly_z) & (pts["actual"] >= cfg.anomaly_p90_multiple * pts["p90"])
             & (pts["actual"] - pts["p90"] >= cfg.anomaly_min_units))
    sporadic = pts["pattern"].isin(SPORADIC_PATTERNS)
    if sporadic.any():
        usual = pts["actual"].where(pts["actual"] > 0).groupby(pts["product_id"]).transform(lambda s: s.quantile(0.75))
        spike &= ~sporadic | (pts["actual"] >= cfg.anomaly_sporadic_multiple * usual.fillna(0))
    drop = ((pts["score"] < -cfg.anomaly_z) & (pts["resid"] <= -cfg.anomaly_min_units)
            & pts["pattern"].isin(DROP_PATTERNS))
    out = pts[spike | drop].copy()
    out["p90"] = pts.loc[out.index, "p90"]
    out["direction"] = np.where(out["resid"] > 0, "spike", "drop")
    high = np.where(out["resid"] > 0, out["actual"] >= cfg.anomaly_high_multiple * out["p90"], out["score"] < -HIGH_Z)
    out["severity"] = np.where(high, "high", "moderate")
    return out.rename(columns={"p50": "expected"})[cols]


def transaction_anomalies(sales: pd.DataFrame, since: pd.Timestamp, cfg: AnalysisConfig) -> pd.DataFrame:
    """sales: movement_id, product_id, quantity, occurred_at (SALE rows only)."""
    s = sales.sort_values(["product_id", "occurred_at", "movement_id"]).copy()
    g = s.groupby("product_id")["quantity"]
    s["prev_n"] = g.cumcount()
    s["prev_max"] = g.transform(lambda x: x.shift().cummax())
    s["expected"] = g.transform(lambda x: x.shift().expanding().median())
    flag = ((s["occurred_at"] >= since) & (s["prev_n"] >= cfg.txn_min_history)
            & (s["quantity"] >= TXN_MULTIPLE * s["expected"]) & (s["quantity"] > s["prev_max"]))
    out = s[flag].rename(columns={"quantity": "actual"})
    out = out.assign(score=out["actual"] / out["expected"])      # multiple of the usual sale size
    return out[["product_id", "movement_id", "occurred_at", "actual", "expected", "score"]]


def injection_evaluation(points: pd.DataFrame, cfg: AnalysisConfig, multiples=(3, 5, 10), n: int | None = None) -> dict:
    """Plant anomalies of known size and location in a copy of the backtest data.

    * spikes: a week's actual sales multiplied by 3, 5 or 10 (any pattern);
    * drops: a week's sales set to zero where the model expected at least 5
      units (smooth and erratic products only).

    Precision is a lower bound: weeks that were already genuinely unusual
    count as false positives.
    """
    pts = points.reset_index(drop=True)
    cnt = pts.groupby("product_id")["actual"].transform("size")
    pts = pts[cnt >= cfg.anomaly_min_points].reset_index(drop=True)
    if pts.empty:
        return {}
    n = n or cfg.injection_count
    rng = np.random.default_rng(cfg.random_state)
    base = weekly_anomalies(pts, cfg)
    by_pat = (pts.loc[base.index, "pattern"].value_counts() / pts["pattern"].value_counts()).fillna(0)
    result = {"weeks_checked": int(len(pts)), "flagged_before_injection": int(len(base)),
              "flag_rate": float(len(base) / len(pts)),
              "flag_rate_by_pattern": {k: round(float(v), 4) for k, v in by_pat.items()},
              "spikes": {}, "drops": None}

    spike_pool = np.flatnonzero(pts["actual"].to_numpy() >= cfg.anomaly_min_units)
    if len(spike_pool):
        idx = rng.choice(spike_pool, size=min(n, len(spike_pool)), replace=False)
        for mult in multiples:
            test = pts.copy()
            test.loc[idx, "actual"] = test.loc[idx, "actual"] * mult
            hit = test.index.isin(weekly_anomalies(test, cfg).index)
            tp = int(hit[idx].sum()); fp = int(hit.sum() - tp)
            result["spikes"][f"x{mult}"] = {"injected": int(len(idx)), "recall": tp / len(idx),
                                            "precision_lower_bound": tp / (tp + fp) if tp + fp else None}

    drop_pool = np.flatnonzero(pts["pattern"].isin(DROP_PATTERNS).to_numpy() & (pts["p50"].to_numpy() >= 5))
    if len(drop_pool):
        idx = rng.choice(drop_pool, size=min(n, len(drop_pool)), replace=False)
        test = pts.copy()
        test.loc[idx, "actual"] = 0
        hit = test.index.isin(weekly_anomalies(test, cfg).index)
        result["drops"] = {"injected": int(len(idx)), "recall": float(hit[idx].mean())}

    # How the busy-week multiple trades recall against false alarms (5x planted spikes).
    if len(spike_pool):
        idx = rng.choice(spike_pool, size=min(n, len(spike_pool)), replace=False)
        sens = []
        for k in (1.0, 1.5, 2.0, 3.0):
            c = replace(cfg, anomaly_p90_multiple=k)
            base_k = len(weekly_anomalies(pts, c))
            test = pts.copy()
            test.loc[idx, "actual"] = test.loc[idx, "actual"] * 5
            hit = test.index.isin(weekly_anomalies(test, c).index)
            tp = int(hit[idx].sum()); fp = int(hit.sum() - tp)
            sens.append({"multiple": k, "flag_rate": base_k / len(pts), "recall_x5": tp / len(idx),
                         "precision_lower_bound_x5": tp / (tp + fp) if tp + fp else None, "chosen": k == cfg.anomaly_p90_multiple})
        result["threshold_sensitivity"] = sens
    return result


def group_events(weekly: pd.DataFrame, transactions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn flags into events, so that one real event raises one alarm.

    The detector judges each week on its own, which is what its evaluation measures. For review,
    though, a surge lasting four weeks is one event, not four, and a week that is unusual only
    because of one very large sale is the same event as that sale. So:

    * consecutive flagged weeks for a product, in the same direction, become one event starting
      in the first week, with units summed over the event;
    * a one-week spike that coincides with an unusually large single sale is reported as the
      sale, which says exactly what to check; within a longer surge the sale is left to the
      surge's event.
    """
    cols = ["product_id", "week_start", "actual", "expected", "score", "direction", "severity", "weeks"]
    if weekly.empty:
        return pd.DataFrame(columns=cols), transactions
    w = weekly.copy()
    w["week_start"] = pd.to_datetime(w["week_start"])
    w = w.sort_values(["product_id", "direction", "week_start"])
    new_run = (w["product_id"].ne(w["product_id"].shift()) | w["direction"].ne(w["direction"].shift())
               | (w["week_start"] - w["week_start"].shift()).ne(pd.Timedelta(weeks=1)))
    w["run"] = new_run.cumsum()
    events = w.groupby("run").agg(product_id=("product_id", "first"), week_start=("week_start", "first"),
                                  actual=("actual", "sum"), expected=("expected", "sum"),
                                  score=("score", lambda s: s.loc[s.abs().idxmax()]), direction=("direction", "first"),
                                  severity=("severity", lambda s: "high" if (s == "high").any() else "moderate"),
                                  weeks=("week_start", "size")).reset_index(drop=True)
    if transactions.empty:
        return events[cols], transactions
    tx_day = pd.to_datetime(transactions["occurred_at"]).dt.normalize()
    tx_week = (tx_day - pd.to_timedelta(tx_day.dt.weekday, unit="D")).to_numpy()
    tx_pid = transactions["product_id"].to_numpy()
    tx_keys = set(zip(tx_pid, tx_week))
    single = [i for i, e in events.iterrows() if e["direction"] == "spike" and e["weeks"] == 1
              and (e["product_id"], e["week_start"].to_datetime64()) in tx_keys]
    covered = np.zeros(len(transactions), dtype=bool)
    for e in events[(events["direction"] == "spike") & (events["weeks"] > 1)].itertuples():
        start = e.week_start.to_datetime64()
        end = (e.week_start + pd.Timedelta(weeks=int(e.weeks))).to_datetime64()
        covered |= (tx_pid == e.product_id) & (tx_week >= start) & (tx_week < end)
    return events.drop(index=single)[cols].reset_index(drop=True), transactions[~covered]
