"""Recommendations: an explicit rule layer over the statistical and model outputs.

The rules are deliberately hand-written rather than learned, so every
recommendation can state exactly why it was made.
"""
from __future__ import annotations

import math

from .config import AnalysisConfig

LABELS = {
    "REORDER_URGENT": "Reorder now",
    "REORDER_SOON": "Reorder soon",
    "INCREASE_STOCK": "Stock more",
    "INVESTIGATE": "Check unusual sales",
    "REDUCE": "Reduce or clear stock",
    "PAUSE_REORDER": "Pause reordering",
    "REVIEW_RANGE": "Review whether to keep stocking",
}


def _d(x) -> str:
    return f"{x:.0f}" if x is not None else "?"


def _days(x) -> str:
    n = round(x)
    return "1 day" if n == 1 else f"{n} days"


def recommend(m: dict, settings: dict, recent_anomaly: dict | None, cfg: AnalysisConfig) -> list[dict]:
    """m: merged product metrics. Returns a list of {action, quantity, priority, reason}."""
    recs = []
    lead = m.get("lead_time_days") or settings["default_lead_time_days"]
    cur = settings.get("currency", "")
    abc = m.get("abc_class")
    growing = m.get("trend") == "growing"
    declining = m.get("trend") == "declining"
    p90_days, p50_days = m.get("runout_worst_days"), m.get("runout_expected_days")
    urgent = (p90_days is not None and p90_days <= lead and (m.get("reorder_qty") or 0) > 0
              and (m.get("velocity_12w") or 0) > 0)

    # Two levels: "now" if stock runs out before a delivery could arrive even at the usual rate;
    # "soon" if only a busy spell would empty it first.
    out_of_stock = (m.get("stock_on_hand") or 0) <= 0
    must = urgent and (out_of_stock or (p50_days is not None and p50_days <= lead))
    order = f"Order {m.get('reorder_qty') or 0:.0f} units to cover a busy spell until the next review."
    if must:
        when = ("It is out of stock" if out_of_stock else
                "At the usual rate the stock runs out within a day" if round(p50_days) < 1 else
                f"At the usual rate the stock runs out in about {_days(p50_days)}")
        recs.append(dict(action="REORDER_URGENT", quantity=m["reorder_qty"], priority=1 if abc in ("A", "B") else 2,
                         reason=f"{when}, and a restock takes {_days(lead)}. {order}"))
    elif urgent:
        usual = f"about {_days(p50_days)}" if p50_days is not None else "more than a year"
        recs.append(dict(action="REORDER_SOON", quantity=m["reorder_qty"], priority=2 if abc in ("A", "B") else 3,
                         reason=f"At the usual rate the stock lasts {usual}, longer than the {_days(lead)} a restock "
                                f"takes, but a busy spell could empty it in {_days(p90_days)}. {order}"))

    cover = m.get("days_of_cover")
    if (not urgent and abc in ("A", "B") and growing and cover is not None
            and cover < cfg.increase_cover_weeks * 7):
        recs.append(dict(action="INCREASE_STOCK", quantity=m.get("reorder_qty") or None, priority=2,
                         reason=f"Sales are clearly rising, by about {(m.get('trend_slope') or 0):.1f} more units each week, "
                                f"and current stock covers only {_days(cover)}."))

    if recent_anomaly:
        a = recent_anomaly
        what = "far above" if a["direction"] == "spike" else "far below"
        if a.get("kind") == "transaction":
            said = (f"One sale in the week of {a['week_start']} was {a['actual']:.0f} units, far more than the usual "
                    f"{a['expected']:.0f} per sale.")
        elif (a.get("weeks") or 1) > 1:
            said = (f"Over {a['weeks']} weeks from {a['week_start']}, {a['actual']:.0f} units sold, {what} the "
                    f"{a['expected']:.0f} expected.")
        else:
            said = f"Sales in the week of {a['week_start']} were {a['actual']:.0f} units, {what} the expected {a['expected']:.0f}."
        recs.append(dict(action="INVESTIGATE", quantity=None, priority=2,
                         reason=said + " Check for a recording error, a one-off bulk order or a stock problem before reordering."))

    over = m.get("overstock_units")
    if over:
        value = m.get("overstock_value")
        tied = f" About {cur} {value:,.0f} is tied up in the excess." if value else ""
        if declining:
            recs.append(dict(action="REDUCE", quantity=math.floor(over), priority=3,
                             reason=f"Stock exceeds {settings['overstock_weeks']} weeks of expected demand by "
                                    f"{over:.0f} units and sales are falling.{tied}"))
        elif m.get("movement_class") != "dormant":
            recs.append(dict(action="PAUSE_REORDER", quantity=math.floor(over), priority=4,
                             reason=f"Stock exceeds {settings['overstock_weeks']} weeks of expected demand by "
                                    f"{over:.0f} units.{tied}"))

    wss = m.get("weeks_since_sale") or 0
    if abc == "C" and wss >= cfg.review_no_sale_weeks and (m.get("stock_on_hand") is None or m["stock_on_hand"] > 0):
        recs.append(dict(action="REVIEW_RANGE", quantity=None, priority=4,
                         reason=f"No sale in {wss} weeks, and the product is in the group that brings in the "
                                f"last 5% of revenue."))
    return recs
