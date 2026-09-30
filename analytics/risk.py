"""Stock risk: days of cover, run-out dates, overstock and reorder quantities.

Weekly forecasts are turned into a daily demand rate that is constant within
each forecast week. Beyond the 4-week horizon the average rate of the four
forecast weeks is assumed to continue (a stated assumption, shown in the
interface) - carrying the last week forward would extrapolate a one-week
seasonal dip, such as a holiday closure, across months.
"""
from __future__ import annotations

import math

import numpy as np


def _daily_rates(weekly: np.ndarray) -> np.ndarray:
    return np.asarray(weekly, dtype=float) / 7.0


def demand_over(days: float, weekly: np.ndarray) -> float:
    """Expected demand over the next ``days`` days."""
    rates = _daily_rates(weekly)
    total, remaining = 0.0, float(days)
    for r in rates:
        d = min(7.0, remaining)
        total += r * d
        remaining -= d
        if remaining <= 0:
            return total
    return total + rates.mean() * remaining


def days_until(stock: float, weekly: np.ndarray, cap_days: int = 365) -> float | None:
    """Days until cumulative demand reaches ``stock``; None if not within ``cap_days``."""
    if stock is None or np.isnan(stock):
        return None
    if stock <= 0:
        return 0.0
    rates = _daily_rates(weekly)
    cum = 0.0
    for k, r in enumerate(rates):
        if r > 0 and cum + 7 * r >= stock:
            return k * 7 + (stock - cum) / r
        cum += 7 * r
    r = rates.mean()
    if r <= 0:
        return None
    d = len(rates) * 7 + (stock - cum) / r
    return d if d <= cap_days else None


def busy_period(mean: np.ndarray, p90: np.ndarray, weeks: int = 53) -> np.ndarray:
    """Weekly steps of the 90th percentile of *cumulative* demand.

    Adding up each week's P90 assumes every week is a busy week, which over a
    three-week lead time is far rarer than one time in ten, so it overstates
    risk and order sizes. Treating weeks as independent, the spread of total
    demand grows with the square root of the number of weeks (the square-root
    rule behind safety stock; Silver, Pyke & Thomas, 2017):

        P90 of total demand to week k  =  sum of means  +  sqrt(sum of (P90 - mean)^2)

    Week 1 is exactly that week's P90. Beyond the forecast horizon the average
    week is assumed to continue, as for the mean forecast. The result is
    returned as weekly steps so that ``days_until`` and ``demand_over`` apply.
    """
    mean = np.asarray(mean, dtype=float)
    gap = np.maximum(np.asarray(p90, dtype=float) - mean, 0.0)
    if len(mean) < weeks:
        extra = weeks - len(mean)
        mean = np.concatenate([mean, np.full(extra, mean.mean())])
        gap = np.concatenate([gap, np.full(extra, float(np.sqrt(np.mean(gap ** 2))))])
    cumulative = np.cumsum(mean) + np.sqrt(np.cumsum(gap ** 2))
    return np.diff(cumulative, prepend=0.0)


def assess(stock: float, mean: np.ndarray, p90: np.ndarray, lead_time_days: int, review_days: int,
           overstock_weeks: int, cost_price: float | None, selling_price: float | None,
           cap_days: int = 365) -> dict:
    """Risk metrics for one product. ``stock`` may be NaN when no stock data exists.

    Cumulative quantities (cover, expected run-out, overstock) use the mean
    forecast; worst-case run-out and the reorder quantity use a busy period
    (the 90th percentile of cumulative demand, see ``busy_period``).
    """
    out = dict(stock_on_hand=None, days_of_cover=None, runout_expected_days=None, runout_worst_days=None,
               overstock_units=None, overstock_value=None, overstock_basis=None, reorder_qty=None)
    if stock is None or np.isnan(stock):
        return out
    stock = max(float(stock), 0.0)
    out["stock_on_hand"] = stock
    p90 = np.maximum(p90, mean)
    mean_daily = float(np.mean(mean)) / 7.0
    out["days_of_cover"] = stock / mean_daily if mean_daily > 0 else None
    out["runout_expected_days"] = days_until(stock, mean, cap_days)
    busy = busy_period(mean, p90)
    out["runout_worst_days"] = days_until(stock, busy, cap_days)

    # Order-up-to: cover a busy period (P90 of total demand) over lead time + review period.
    need = demand_over(lead_time_days + review_days, busy)
    out["reorder_qty"] = float(max(0, math.ceil(need - stock)))

    # Overstock: stock beyond expected demand over the overstock horizon.
    excess = stock - demand_over(overstock_weeks * 7, mean)
    if excess > 0:
        price, basis = (cost_price, "cost") if cost_price else (selling_price, "selling price")
        out["overstock_units"] = float(excess)
        out["overstock_value"] = float(excess * price) if price else None
        out["overstock_basis"] = basis if price else None
    return out
