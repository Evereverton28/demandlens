"""Recommendations: an explicit rule layer over the statistical and model outputs.

The rules are deliberately hand-written rather than learned, so every
recommendation can state exactly why it was made.
"""
from __future__ import annotations

import math

from .config import AnalysisConfig

LABELS = {
    "REORDER_URGENT": "Reorder now",
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

    if urgent:
        if (m.get("stock_on_hand") or 0) <= 0:
            when = "It is out of stock"
        elif round(p90_days) < 1:
            when = "Stock could run out within a day if demand is high"
        else:
            exp = (f"about {_days(p50_days)} at the expected rate" if p50_days is not None
                   else "later at the expected rate")
            when = f"Stock could run out in {_days(p90_days)} if demand is high ({exp})"
        recs.append(dict(action="REORDER_URGENT", quantity=m["reorder_qty"], priority=1 if abc in ("A", "B") else 2,
                         reason=f"{when}; a restock takes {_days(lead)}. Order {m['reorder_qty']:.0f} units to "
                                f"cover high demand until the next review."))

    cover = m.get("days_of_cover")
    if (not urgent and abc in ("A", "B") and growing and cover is not None
            and cover < cfg.increase_cover_weeks * 7):
        recs.append(dict(action="INCREASE_STOCK", quantity=m.get("reorder_qty") or None, priority=2,
                         reason=f"Sales are clearly rising, by about {(m.get('trend_slope') or 0):.1f} more units each week, "
                                f"and current stock covers only {_days(cover)}."))

    if recent_anomaly:
        a = recent_anomaly
        what = "far above" if a["direction"] == "spike" else "far below"
        recs.append(dict(action="INVESTIGATE", quantity=None, priority=2,
                         reason=f"Sales in the week of {a['week_start']} were {a['actual']:.0f} units, {what} the "
                                f"expected {a['expected']:.0f}. Check for a recording error, a one-off bulk order "
                                f"or a stock problem before reordering."))

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
