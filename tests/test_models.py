import pytest
import math
"""Feature leakage, forecast coherence, risk arithmetic, anomalies and recommendations."""
import numpy as np
import pandas as pd

from analytics.anomalies import transaction_anomalies, weekly_anomalies
from analytics.config import AnalysisConfig
from analytics.features import DesignBuilder
from analytics.forecasting import coherent_mean
from analytics.preprocess import build_panel
from analytics.recommend import recommend
from analytics.risk import assess, days_until, demand_over
from analytics.synthetic import generic


def synthetic_panel():
    df, truth = generic(n_products=8, n_weeks=40, seed=2)
    mv = df.rename(columns={"date": "occurred_at"}).assign(occurred_at=lambda d: pd.to_datetime(d["occurred_at"]))
    mv["product_id"] = mv["sku"].astype(int)
    mv["unit_price"] = mv["unit_price"].astype(float)
    return build_panel(mv), truth


def test_features_use_only_the_past():
    panel, _ = synthetic_panel()
    cfg = AnalysisConfig()
    t = 30
    before = DesignBuilder(panel, cfg).rows(t, t, require_target=True)
    panel.units[:, t + 1:] = 999.0                                       # change the future
    after = DesignBuilder(panel, cfg).rows(t, t, require_target=True)
    feature_cols = [c for c in before.columns if not c.startswith("_") and c != "y_last_year"]
    pd.testing.assert_frame_equal(before[feature_cols], after[feature_cols])
    assert (after["_y"] == 999).all()                                    # only the targets changed


def test_mean_is_made_coherent_with_quantiles():
    m = coherent_mean(np.array([2.0, 10.0]), np.array([200.0, 12.0]), np.array([1.0, 9.0]))
    assert m.tolist() == [20.0, 9.0]


def test_runout_and_reorder_arithmetic():
    weekly = np.array([7.0, 7.0, 14.0, 14.0])                             # 1, 1, 2, 2 units a day
    assert days_until(10, weekly) == 7 + 3
    assert demand_over(35, weekly) == 7 + 7 + 14 + 14 + 7 * (42 / 28)    # beyond the horizon: average rate
    assert days_until(5, np.zeros(4)) is None
    r = assess(20, weekly, weekly * 2, lead_time_days=7, review_days=7, overstock_weeks=12,
               cost_price=2.0, selling_price=3.0)
    assert r["runout_expected_days"] == 7 + 7 + 3                                           # 14 used by day 14, then 2/day
    # Busy period: P90 of total demand = sum of means + sqrt(sum of squared (P90 - mean)).
    # Week 1: 7 + 7 = 14. By week 2: 14 + sqrt(7^2 + 7^2) = 14 + 9.90 = 23.90, so week 2 adds 9.90.
    assert r["runout_worst_days"] == pytest.approx(7 + 6 / (np.sqrt(98) / 7))           # 6 more units at 9.90/7 a day
    assert r["reorder_qty"] == math.ceil(14 + np.sqrt(98) - 20) and r["overstock_units"] is None   # 23.90 - 20 -> 4


def test_busy_period_is_not_every_week_busy():
    from analytics.risk import busy_period
    mean, p90 = np.full(4, 30.0), np.full(4, 60.0)
    steps = np.cumsum(busy_period(mean, p90))
    assert steps[0] == pytest.approx(60)                          # one week: exactly that week's P90
    assert steps[2] == pytest.approx(90 + 30 * np.sqrt(3))        # three weeks: square-root rule, not 3 x 60
    assert np.cumsum(mean)[2] < steps[2] < p90[:3].sum()          # between "average" and "every week busy"


def test_weekly_spike_and_drop_rules():
    cfg = AnalysisConfig()
    rng = np.random.default_rng(0)
    rows = []
    for pid, pattern in ((1, "smooth"), (2, "lumpy")):
        for w in range(20):
            actual = 20 + rng.integers(-3, 4)
            rows.append((pid, w, float(actual), 20.0, 25.0, pattern))
    pts = pd.DataFrame(rows, columns=["product_id", "week_start", "actual", "p50", "p90", "pattern"])
    pts.loc[5, "actual"] = 200          # smooth spike
    pts.loc[10, "actual"] = 0           # smooth drop
    pts.loc[30, "actual"] = 0           # lumpy "drop": empty weeks are normal for lumpy demand
    flagged = weekly_anomalies(pts, cfg)
    assert set(flagged.index) == {5, 10}
    assert flagged.loc[5, "direction"] == "spike" and flagged.loc[5, "severity"] == "high"


def test_transaction_outlier_rule():
    cfg = AnalysisConfig()
    t = pd.date_range("2024-01-01", periods=30, freq="D")
    sales = pd.DataFrame({"movement_id": range(30), "product_id": 1, "quantity": [4.0] * 29 + [60.0], "occurred_at": t})
    out = transaction_anomalies(sales, t[-5], cfg)
    assert out["movement_id"].tolist() == [29] and out["score"].iloc[0] == 15


def test_recommendation_rules_explain_themselves():
    cfg = AnalysisConfig()
    settings = {"default_lead_time_days": 7, "overstock_weeks": 12, "currency": "KES"}
    urgent = recommend(dict(abc_class="A", runout_worst_days=3, runout_expected_days=6, reorder_qty=40, velocity_12w=10,
                            stock_on_hand=8, lead_time_days=7), settings, None, cfg)
    assert urgent[0]["action"] == "REORDER_URGENT" and urgent[0]["priority"] == 1 and "40 units" in urgent[0]["reason"]
    over = recommend(dict(abc_class="B", trend="declining", overstock_units=120, overstock_value=6000, stock_on_hand=300,
                          velocity_12w=2, movement_class="slow", weeks_since_sale=1), settings, None, cfg)
    assert over[0]["action"] == "REDUCE" and "KES 6,000" in over[0]["reason"]


def test_model_ignores_features_that_never_vary():
    """Short histories leave last year's sales empty; fixed prices make relative price constant.
    scikit-learn 1.9 raises on such columns, so the model must leave them out."""
    from analytics.features import CATEGORICAL, FEATURES
    from analytics.forecasting import QuantileGBM
    rng = np.random.default_rng(0)
    n = 400
    X = pd.DataFrame({c: rng.integers(0, 4, n).astype(float) if c in CATEGORICAL else rng.normal(size=n) for c in FEATURES})
    X["y_last_year"] = np.nan          # no history a year back
    X["rel_price"] = 1.0               # prices never changed
    X["category"] = np.nan             # no categories given
    y = rng.poisson(5, n).astype(float)
    m = QuantileGBM({"max_iter": 20}, with_mean=True).fit(X, y)
    assert set(m.dropped) == {"y_last_year", "rel_price", "category"}
    p50, p90, mean = m.predict(X)
    assert len(p50) == n and (p90 >= p50).all()


def test_one_event_raises_one_alarm():
    from analytics.anomalies import group_events
    wk = lambda pid, day, actual: (pid, pd.Timestamp(day), actual, 10.0, 6.0, "spike", "moderate")
    weekly = pd.DataFrame([wk(1, "2026-05-04", 40), wk(1, "2026-05-11", 45), wk(1, "2026-05-18", 38), wk(1, "2026-05-25", 42),
                           wk(2, "2026-06-08", 90),                              # one week, explained by one big sale
                           wk(3, "2026-07-06", 50), wk(3, "2026-07-20", 55)],    # not consecutive: two events
                          columns=["product_id", "week_start", "actual", "expected", "score", "direction", "severity"])
    tx = pd.DataFrame({"product_id": [2, 1], "movement_id": [7, 8], "actual": [80.0, 30.0], "expected": [3.0, 2.0], "score": [27.0, 15.0],
                       "occurred_at": pd.to_datetime(["2026-06-10 11:00", "2026-05-13 09:00"])})
    events, sales = group_events(weekly, tx)
    surge = events[events.product_id == 1].iloc[0]
    assert len(events[events.product_id == 1]) == 1 and surge.weeks == 4 and surge.actual == 165
    assert 2 not in set(events.product_id)                          # reported as the sale instead
    assert len(events[events.product_id == 3]) == 2
    assert list(sales.movement_id) == [7]                           # the sale inside the surge is part of the surge


def test_reorder_now_versus_soon():
    cfg = AnalysisConfig()
    settings = {"default_lead_time_days": 14, "overstock_weeks": 12, "currency": "KES"}
    base = dict(abc_class="B", reorder_qty=6, velocity_12w=0.6, stock_on_hand=5, lead_time_days=21)
    soon = recommend(dict(base, runout_worst_days=13, runout_expected_days=66), settings, None, cfg)
    assert soon[0]["action"] == "REORDER_SOON" and "66 days" in soon[0]["reason"]      # lasts, unless sales are busy
    now = recommend(dict(base, runout_worst_days=5, runout_expected_days=12), settings, None, cfg)
    assert now[0]["action"] == "REORDER_URGENT"                                        # runs out before a delivery
