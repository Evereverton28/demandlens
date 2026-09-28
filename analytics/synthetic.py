"""Synthetic sales data for testing and demonstrations.

Everything produced here is fictional and must be flagged as synthetic when
imported. It exists so that (a) the automated tests run without the real
dataset, and (b) the pipeline can be checked against a known truth: each
product has a known demand pattern and trend, and anomalies are planted at
known weeks.

Two layouts: ``online_retail`` (including realistic data-quality problems:
cancellations, postage lines, zero prices, duplicates) and ``generic``
(the DemandLens template, with deliveries and stock counts).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KINDS = ("smooth", "seasonal", "growing", "declining", "intermittent", "lumpy")


def weekly_demand(n_products: int = 40, n_weeks: int = 80, seed: int = 0, n_anomalies: int = 6):
    """Return (weekly units [products x weeks], product table, planted anomalies)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_weeks)
    rows, truth = [], []
    for i in range(n_products):
        kind = KINDS[i % len(KINDS)]
        base = rng.uniform(8, 40)
        season = 1 + 0.35 * np.sin(2 * np.pi * (t - 38) / 52)          # peaks near the end of the year
        if kind == "smooth":
            lam = base * np.ones(n_weeks)
        elif kind == "seasonal":
            lam = base * season
        elif kind == "growing":
            lam = base * (0.5 + t / n_weeks * 1.2)
        elif kind == "declining":
            lam = base * (1.6 - t / n_weeks * 1.3)
        if kind in ("smooth", "seasonal", "growing", "declining"):
            y = rng.poisson(np.clip(lam, 0.1, None))
        elif kind == "intermittent":
            y = rng.binomial(1, 0.3, n_weeks) * rng.poisson(4, n_weeks)
        else:  # lumpy: occasional large orders
            y = rng.binomial(1, 0.2, n_weeks) * rng.negative_binomial(2, 0.05, n_weeks)
        start = int(rng.integers(0, 12)) if i % 5 == 0 else 0            # some products launch later
        y = y.astype(float)
        y[:start] = np.nan
        rows.append(y)
        truth.append({"sku": f"{10000 + i:05d}", "name": f"Test product {i + 1} ({kind})", "kind": kind,
                      "price": round(float(rng.uniform(0.5, 12)), 2), "start_week": start})
    Y = np.vstack(rows)
    products = pd.DataFrame(truth)

    planted = []
    candidates = [i for i, p in enumerate(truth) if p["kind"] in ("smooth", "seasonal")]
    for k in range(n_anomalies):
        i = candidates[k % len(candidates)]
        w = n_weeks - 2 - 3 * k                                          # recent weeks, inside the backtest window
        Y[i, w] = np.nan_to_num(Y[i, w]) * 6 + 30
        planted.append({"sku": truth[i]["sku"], "week_index": w})
    return Y, products, pd.DataFrame(planted)


def _explode(Y, products, start: pd.Timestamp, rng):
    """Spread weekly units over individual daily orders."""
    out = []
    P, T = Y.shape
    for i in range(P):
        for w in range(T):
            units = Y[i, w]
            if np.isnan(units) or units <= 0:
                continue
            n_orders = max(1, min(int(units), int(rng.integers(1, 4))))
            split = rng.multinomial(int(units), np.ones(n_orders) / n_orders)
            for q in split[split > 0]:
                day = start + pd.Timedelta(weeks=w, days=int(rng.integers(0, 6)), hours=int(rng.integers(8, 18)))
                out.append((products.loc[i, "sku"], int(q), day, products.loc[i, "price"], products.loc[i, "name"]))
    return pd.DataFrame(out, columns=["sku", "qty", "when", "price", "name"])


def online_retail(n_products=40, n_weeks=80, seed=0, start="2024-01-01"):
    """Online Retail II layout with typical data-quality problems. Returns (DataFrame, truth dict)."""
    rng = np.random.default_rng(seed)
    Y, products, planted = weekly_demand(n_products, n_weeks, seed)
    orders = _explode(Y, products, pd.Timestamp(start), rng)
    orders["Invoice"] = [str(500000 + k) for k in range(len(orders))]
    orders["Customer ID"] = rng.integers(12000, 12300, len(orders)).astype(float)
    df = pd.DataFrame({"Invoice": orders["Invoice"], "StockCode": orders["sku"], "Description": orders["name"],
                       "Quantity": orders["qty"], "InvoiceDate": orders["when"], "Price": orders["price"],
                       "Customer ID": orders["Customer ID"], "Country": "United Kingdom"})
    extra = []
    # a large order entered then cancelled (should be voided, not counted as demand)
    big = df.iloc[len(df) // 2].copy(); big["Quantity"] = 5000; big["Invoice"] = "599990"
    cancel = big.copy(); cancel["Invoice"] = "C599990"; cancel["Quantity"] = -5000
    cancel["InvoiceDate"] = big["InvoiceDate"] + pd.Timedelta(minutes=20)
    extra += [big, cancel]
    # an ordinary customer return (should become a RETURN movement)
    ret = df.iloc[10].copy(); ret["Invoice"] = "C599991"; ret["Quantity"] = -1; ret["Customer ID"] = 99999.0
    extra.append(ret)
    # postage line, zero-price line, stock adjustment
    post = df.iloc[5].copy(); post["StockCode"] = "POST"; post["Description"] = "POSTAGE"; post["Invoice"] = "599992"
    zero = df.iloc[6].copy(); zero["Price"] = 0.0; zero["Invoice"] = "599993"
    adj = df.iloc[7].copy(); adj["Quantity"] = -3; adj["Price"] = 0.0; adj["Invoice"] = "599994"; adj["Description"] = "damaged"
    extra += [post, zero, adj]
    df = pd.concat([df, pd.DataFrame(extra), df.iloc[:5]], ignore_index=True)     # last part: exact duplicates
    df.loc[df.sample(frac=0.1, random_state=seed).index, "Customer ID"] = np.nan
    df = df.sort_values("InvoiceDate", kind="stable").reset_index(drop=True)
    return df, {"weekly": Y, "products": products, "planted": planted, "start": pd.Timestamp(start)}


def generic(n_products=25, n_weeks=70, seed=0, start="2025-01-06", categories=("Stationery", "Snacks", "Household", "Drinks")):
    """DemandLens template layout with deliveries and periodic stock counts."""
    rng = np.random.default_rng(seed)
    Y, products, planted = weekly_demand(n_products, n_weeks, seed)
    orders = _explode(Y, products, pd.Timestamp(start), rng)
    cat = {s: categories[i % len(categories)] for i, s in enumerate(products["sku"])}
    rows = [{"date": o.when, "sku": o.sku, "name": o.name, "category": cat[o.sku], "type": "SALE",
             "quantity": o.qty, "unit_price": o.price, "cost_price": round(o.price * 0.7, 2), "lead_time_days": 7}
            for o in orders.itertuples()]
    for p in products.itertuples():
        first = pd.Timestamp(start) + pd.Timedelta(weeks=p.start_week)
        rows.append({"date": first - pd.Timedelta(days=1), "sku": p.sku, "name": p.name, "category": cat[p.sku],
                     "type": "STOCKTAKE", "quantity": int(np.nanmean(Y[p.Index]) * 6) + 5, "unit_price": None, "cost_price": round(p.price * 0.7, 2),
                     "lead_time_days": 7})
        last = n_weeks - 10 if p.Index % 4 == 0 else n_weeks              # every fourth product misses recent deliveries
        for w in range(p.start_week + 4, last, 4):                       # a delivery every four weeks
            rows.append({"date": pd.Timestamp(start) + pd.Timedelta(weeks=w), "sku": p.sku, "name": p.name,
                         "category": cat[p.sku], "type": "RESTOCK",
                         "quantity": int(np.nanmean(Y[p.Index]) * 4 * rng.uniform(0.85, 1.15)) + 3,
                         "unit_price": round(p.price * 0.7, 2), "cost_price": round(p.price * 0.7, 2), "lead_time_days": 7})
    df = pd.DataFrame(rows).sort_values("date", kind="stable").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d %H:%M")
    return df, {"weekly": Y, "products": products, "planted": planted, "start": pd.Timestamp(start)}
