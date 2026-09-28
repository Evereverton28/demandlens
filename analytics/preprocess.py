"""Preprocessing: turns ledger movements into a weekly product-level panel.

Rules (see the methodology):
* weeks run Monday to Sunday;
* weekly demand = units sold minus units returned, floored at zero;
* weeks after a product's first sale with no sales are real zeros and are
  filled; weeks before the first sale are not (the product did not exist);
* the week containing the latest movement is excluded unless it is complete,
  because a partial week would look like a sudden drop in demand.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def week_start(ts: pd.Series) -> pd.Series:
    ts = pd.to_datetime(ts)
    return (ts - pd.to_timedelta(ts.dt.weekday, unit="D")).dt.normalize()


def last_complete_week(latest: pd.Timestamp) -> pd.Timestamp:
    """Start of the last complete Monday-Sunday week on or before ``latest``."""
    latest = pd.Timestamp(latest)
    ws = (latest - pd.Timedelta(days=latest.weekday())).normalize()
    week_is_complete = latest.weekday() == 6 and latest.time() >= pd.Timestamp("23:00").time()
    return ws if week_is_complete else ws - pd.Timedelta(weeks=1)


@dataclass
class Panel:
    """Weekly demand for every product, as a long frame and as dense matrices."""
    long: pd.DataFrame              # product_id, week_start, units, revenue, avg_price
    weeks: pd.DatetimeIndex         # all weeks from the first sale to the last complete week
    product_ids: np.ndarray
    units: np.ndarray               # [products x weeks]; NaN before a product's first sale
    revenue: np.ndarray
    price: np.ndarray               # average selling price in weeks with sales, else NaN
    first_idx: np.ndarray           # index of each product's first sale week

    @property
    def last_week(self) -> pd.Timestamp:
        return self.weeks[-1]

    def row(self, product_id: int) -> int:
        return int(np.searchsorted(self.product_ids, product_id))


def build_panel(movements: pd.DataFrame, end_week: pd.Timestamp | None = None) -> Panel:
    """movements needs: product_id, type, quantity, unit_price, occurred_at."""
    mv = movements[movements["type"].isin(["SALE", "RETURN"])].copy()
    if mv.empty:
        raise ValueError("No sales have been recorded yet.")
    mv["week_start"] = week_start(mv["occurred_at"])
    if end_week is None:
        end_week = last_complete_week(movements["occurred_at"].max())
    mv = mv[mv["week_start"] <= end_week]
    sign = np.where(mv["type"] == "SALE", 1.0, -1.0)
    mv["units"] = sign * mv["quantity"]
    mv["revenue"] = sign * mv["quantity"] * mv["unit_price"].fillna(0)
    mv["sale_units"] = np.where(mv["type"] == "SALE", mv["quantity"], 0.0)
    mv["sale_revenue"] = np.where(mv["type"] == "SALE", mv["quantity"] * mv["unit_price"].fillna(0), 0.0)
    g = mv.groupby(["product_id", "week_start"])[["units", "revenue", "sale_units", "sale_revenue"]].sum()

    first_sale = (mv[mv["type"] == "SALE"].groupby("product_id")["week_start"].min())
    if first_sale.empty:
        raise ValueError("No sales fall within complete weeks yet.")
    weeks = pd.date_range(first_sale.min(), end_week, freq="7D")
    if len(weeks) == 0:
        raise ValueError("No complete week of sales is available yet.")
    pids = np.array(sorted(first_sale.index))
    P, T = len(pids), len(weeks)
    wk_pos = {w: i for i, w in enumerate(weeks)}
    pid_pos = {p: i for i, p in enumerate(pids)}

    units = np.zeros((P, T)); revenue = np.zeros((P, T))
    s_units = np.zeros((P, T)); s_rev = np.zeros((P, T))
    g = g.reset_index()
    g = g[g["product_id"].isin(pid_pos)]
    r = g["product_id"].map(pid_pos).to_numpy(); c = g["week_start"].map(wk_pos).to_numpy()
    units[r, c] = g["units"].to_numpy(); revenue[r, c] = g["revenue"].to_numpy()
    s_units[r, c] = g["sale_units"].to_numpy(); s_rev[r, c] = g["sale_revenue"].to_numpy()
    units = np.clip(units, 0, None); revenue = np.clip(revenue, 0, None)

    first_idx = np.array([wk_pos[first_sale[p]] for p in pids])
    before = np.arange(T)[None, :] < first_idx[:, None]
    units[before] = np.nan; revenue[before] = np.nan
    with np.errstate(invalid="ignore", divide="ignore"):
        price = np.where(s_units > 0, s_rev / s_units, np.nan)

    long = pd.DataFrame({
        "product_id": np.repeat(pids, T), "week_start": np.tile(weeks, P),
        "units": units.ravel(), "revenue": revenue.ravel(), "avg_price": price.ravel(),
    }).dropna(subset=["units"]).reset_index(drop=True)
    return Panel(long, weeks, pids, units, revenue, price, first_idx)


def stock_on_hand(movements: pd.DataFrame) -> pd.Series:
    """Current stock per product from the ledger.

    The latest STOCKTAKE sets the level; later movements adjust it. Without a
    stocktake the level is known only if the product has recorded restocks;
    otherwise it is unknown (NaN) - imported sales history alone says nothing
    about how much is on the shelf.
    """
    mv = movements.sort_values(["product_id", "occurred_at", "movement_id"] if "movement_id" in movements else
                               ["product_id", "occurred_at"])
    delta = pd.Series(0.0, index=mv.index)
    delta[mv["type"] == "SALE"] = -mv["quantity"]
    delta[mv["type"].isin(["RETURN", "RESTOCK"])] = mv["quantity"]
    delta[mv["type"] == "ADJUSTMENT"] = mv["quantity"]
    mv = mv.assign(delta=delta)

    out = {}
    for pid, grp in mv.groupby("product_id", sort=False):
        takes = np.flatnonzero(grp["type"].to_numpy() == "STOCKTAKE")
        if len(takes):
            k = takes[-1]
            out[pid] = grp["quantity"].iloc[k] + grp["delta"].iloc[k + 1:].sum()
        elif (grp["type"] == "RESTOCK").any():
            out[pid] = grp["delta"].sum()
        else:
            out[pid] = np.nan
    return pd.Series(out, name="stock_on_hand", dtype=float)
