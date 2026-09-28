"""Data ingestion: turns raw sales files into ledger movements.

Two formats are supported:

* ``online_retail`` - the UCI Online Retail II layout (Invoice, StockCode,
  Description, Quantity, InvoiceDate, Price, Customer ID, Country), as CSV or
  the original two-sheet Excel file.
* ``generic`` - a simple template a shop can fill in from its own records:
  date, sku, name, category, type, quantity, unit_price (+ optional
  cost_price, lead_time_days).

Every cleaning rule is counted, so the import report explains exactly what
was removed and why.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

PRODUCT_CODE = re.compile(r"^\d{5}[A-Za-z]{0,3}$")   # standard Online Retail product codes
CANCEL_MATCH_DAYS = 90
MOVEMENT_TYPES = {"SALE", "RETURN", "RESTOCK", "ADJUSTMENT", "STOCKTAKE"}

ONLINE_RETAIL_ALIASES = {
    "InvoiceNo": "Invoice", "UnitPrice": "Price", "CustomerID": "Customer ID",
}


# ---------------------------------------------------------------------------
# Online Retail II
# ---------------------------------------------------------------------------
def read_online_retail(path: str | Path) -> pd.DataFrame:
    """Read the dataset from CSV or the original Excel file (both sheets)."""
    path = Path(path)
    dtypes = {"Invoice": str, "InvoiceNo": str, "StockCode": str, "Description": str}
    if path.suffix.lower() in {".xlsx", ".xls"}:
        sheets = pd.read_excel(path, sheet_name=None, dtype=dtypes)
        raw = pd.concat(sheets.values(), ignore_index=True)
    else:
        raw = pd.read_csv(path, dtype=dtypes, encoding_errors="replace")
    raw = raw.rename(columns=ONLINE_RETAIL_ALIASES)
    missing = {"Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price"} - set(raw.columns)
    if missing:
        raise ValueError(f"File is not in Online Retail format; missing columns: {', '.join(sorted(missing))}")
    return raw


def clean_online_retail(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Apply the preprocessing rules and return (movements, products, report).

    movements: sku, type, quantity, unit_price, occurred_at
    products:  sku, name, selling_price
    """
    report: dict = {"rows_read": int(len(raw))}
    df = raw.copy()
    df["Invoice"] = df["Invoice"].astype(str).str.strip()
    df["StockCode"] = df["StockCode"].astype(str).str.strip()
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="coerce")

    n = len(df)
    df = df.dropna(subset=["StockCode", "Quantity", "InvoiceDate", "Price"])
    report["missing_fields"] = n - len(df)

    n = len(df)
    df = df[df["StockCode"].str.match(PRODUCT_CODE)]
    report["non_product_codes"] = n - len(df)          # postage, fees, manual lines, tests

    n = len(df)
    df = df.drop_duplicates()
    report["exact_duplicates"] = n - len(df)

    is_cancel = df["Invoice"].str.upper().str.startswith("C")
    sales = df[~is_cancel & (df["Quantity"] > 0)]
    cancels = df[is_cancel]
    adjustments = df[~is_cancel & (df["Quantity"] < 0)]   # damages, losses, corrections

    n = len(sales)
    sales = sales[sales["Price"] > 0]
    report["zero_price_sales"] = n - len(sales)

    # Void orders: a cancellation that exactly reverses an earlier sale to the
    # same customer (same product and quantity, within 90 days) removes both,
    # so large orders entered and cancelled do not look like demand.
    voided_sales, matched_cancels = _match_cancellations(sales, cancels)
    report["voided_orders"] = int(len(matched_cancels))
    sales = sales.drop(index=voided_sales)
    returns = cancels.drop(index=matched_cancels)
    report["unmatched_cancellations_as_returns"] = int(len(returns))
    report["stock_adjustments"] = int(len(adjustments))

    parts = [
        pd.DataFrame({"sku": sales["StockCode"], "type": "SALE", "quantity": sales["Quantity"].astype(float),
                      "unit_price": sales["Price"].astype(float), "occurred_at": sales["InvoiceDate"]}),
        pd.DataFrame({"sku": returns["StockCode"], "type": "RETURN", "quantity": returns["Quantity"].abs().astype(float),
                      "unit_price": returns["Price"].astype(float), "occurred_at": returns["InvoiceDate"]}),
        pd.DataFrame({"sku": adjustments["StockCode"], "type": "ADJUSTMENT", "quantity": adjustments["Quantity"].astype(float),
                      "unit_price": np.nan, "occurred_at": adjustments["InvoiceDate"]}),
    ]
    movements = pd.concat(parts, ignore_index=True).sort_values("occurred_at", kind="stable")

    # One name per product: the most frequent description.
    desc = (df.dropna(subset=["Description"])
              .assign(Description=lambda d: d["Description"].str.strip())
              .groupby("StockCode")["Description"].agg(lambda s: s.value_counts().index[0]))
    last_price = sales.sort_values("InvoiceDate").groupby("StockCode")["Price"].median()
    products = pd.DataFrame({"sku": sorted(movements["sku"].unique())})
    products["name"] = products["sku"].map(desc).fillna(products["sku"])
    products["selling_price"] = products["sku"].map(last_price)
    products["category"] = None

    report["customer_ids"] = "dropped (not needed for product-level analysis)"
    report["movements"] = int(len(movements))
    report["sales"] = int((movements["type"] == "SALE").sum())
    report["products"] = int(len(products))
    report["rows_rejected"] = int(report["missing_fields"] + report["non_product_codes"] + report["exact_duplicates"]
                                  + report["zero_price_sales"] + 2 * report["voided_orders"])
    return movements, products, report


def _match_cancellations(sales: pd.DataFrame, cancels: pd.DataFrame) -> tuple[list, list]:
    """Pair each cancellation with the latest earlier identical sale (same customer, product, quantity)."""
    if cancels.empty or "Customer ID" not in cancels:
        return [], []
    c = cancels.dropna(subset=["Customer ID"]).assign(qty=lambda d: -d["Quantity"], c_idx=lambda d: d.index)
    s = sales.dropna(subset=["Customer ID"]).assign(qty=lambda d: d["Quantity"], s_idx=lambda d: d.index)
    if c.empty or s.empty:
        return [], []
    keys = ["Customer ID", "StockCode", "qty"]
    c = c.sort_values("InvoiceDate")
    s = s.sort_values("InvoiceDate")
    m = pd.merge_asof(c[keys + ["InvoiceDate", "c_idx"]], s[keys + ["InvoiceDate", "s_idx"]],
                      on="InvoiceDate", by=keys, direction="backward",
                      tolerance=pd.Timedelta(days=CANCEL_MATCH_DAYS))
    m = m.dropna(subset=["s_idx"]).drop_duplicates(subset="s_idx", keep="first")
    return m["s_idx"].astype(int).tolist(), m["c_idx"].astype(int).tolist()


# ---------------------------------------------------------------------------
# Generic template
# ---------------------------------------------------------------------------
GENERIC_REQUIRED = ["date", "sku", "type", "quantity"]


def parse_dates(values: pd.Series) -> pd.Series:
    """ISO dates (2025-01-13) first; anything else is read day-first, as written in Kenya (13/01/2025)."""
    if pd.api.types.is_datetime64_any_dtype(values):
        return values
    text = values.astype(str).str.strip()
    out = pd.to_datetime(text, format="ISO8601", errors="coerce")
    rest = out.isna() & values.notna()
    if rest.any():
        out[rest] = pd.to_datetime(text[rest], dayfirst=True, format="mixed", errors="coerce")
    return out


def read_generic(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        raw = pd.read_excel(path, dtype={"sku": str})
    else:
        raw = pd.read_csv(path, dtype={"sku": str})
    raw.columns = [c.strip().lower() for c in raw.columns]
    missing = set(GENERIC_REQUIRED) - set(raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}. "
                         "Expected: date, sku, name, category, type, quantity, unit_price")
    return raw


def clean_generic(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    report: dict = {"rows_read": int(len(raw))}
    df = raw.copy()
    df["sku"] = df["sku"].astype(str).str.strip()
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["occurred_at"] = parse_dates(df["date"])
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["unit_price"] = pd.to_numeric(df.get("unit_price"), errors="coerce") if "unit_price" in df else np.nan

    bad_date = df["occurred_at"].isna()
    bad_type = ~df["type"].isin(MOVEMENT_TYPES)
    bad_qty = df["quantity"].isna() | ((df["quantity"] < 0) & (df["type"] != "ADJUSTMENT"))
    bad_sku = df["sku"].isin(["", "nan", "None"])
    report["invalid_date"] = int(bad_date.sum())
    report["invalid_type"] = int(bad_type.sum())
    report["invalid_quantity"] = int(bad_qty.sum())
    report["missing_sku"] = int(bad_sku.sum())
    ok = ~(bad_date | bad_type | bad_qty | bad_sku)
    df = df[ok]

    movements = df[["sku", "type", "quantity", "unit_price", "occurred_at"]].sort_values("occurred_at", kind="stable")

    last = df.sort_values("occurred_at").groupby("sku").last()
    products = pd.DataFrame({"sku": last.index})
    products["name"] = products["sku"].map(last["name"]) if "name" in last else products["sku"]
    products["name"] = products["name"].fillna(products["sku"])
    products["category"] = products["sku"].map(last["category"]) if "category" in last else None
    sale_price = df[df["type"] == "SALE"].groupby("sku")["unit_price"].median()
    products["selling_price"] = products["sku"].map(sale_price)
    for col in ("cost_price", "lead_time_days"):
        if col in last:
            products[col] = products["sku"].map(pd.to_numeric(last[col], errors="coerce"))

    report["movements"] = int(len(movements))
    report["sales"] = int((movements["type"] == "SALE").sum())
    report["products"] = int(len(products))
    report["rows_rejected"] = int((~ok).sum())
    return movements, products, report
