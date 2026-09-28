"""Cleaning rules for imported sales files."""
import pandas as pd

from analytics.ingest import clean_generic, clean_online_retail, parse_dates


def raw(rows):
    return pd.DataFrame(rows, columns=["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"])


def test_online_retail_rules():
    df = raw([
        ["1", "10001", "Mug", 5, "2024-01-01 10:00", 2.0, 1.0, "UK"],
        ["1", "10001", "Mug", 5, "2024-01-01 10:00", 2.0, 1.0, "UK"],        # exact duplicate
        ["2", "POST", "Postage", 1, "2024-01-01 11:00", 5.0, 1.0, "UK"],     # not a product
        ["3", "10002", "Plate", 4, "2024-01-02 10:00", 0.0, 2.0, "UK"],      # zero price
        ["4", "10002", "Plate", 900, "2024-01-03 10:00", 1.0, 3.0, "UK"],    # entered ...
        ["C4", "10002", "Plate", -900, "2024-01-03 10:30", 1.0, 3.0, "UK"],  # ... then cancelled: void both
        ["C5", "10001", "Mug", -2, "2024-01-04 10:00", 2.0, 9.0, "UK"],      # unmatched: a return
        ["6", "10001", "damaged", -3, "2024-01-05 10:00", 0.0, None, "UK"],  # stock adjustment
    ])
    mv, products, rep = clean_online_retail(df)
    assert rep["exact_duplicates"] == 1
    assert rep["non_product_codes"] == 1
    assert rep["zero_price_sales"] == 1
    assert rep["voided_orders"] == 1
    assert list(mv["type"].value_counts().sort_index().items()) == [("ADJUSTMENT", 1), ("RETURN", 1), ("SALE", 1)]
    assert mv.loc[mv["type"] == "RETURN", "quantity"].iloc[0] == 2          # stored as a positive amount
    assert set(products["sku"]) == {"10001"}                                  # the voided product has no movements


def test_dates_iso_and_day_first():
    d = parse_dates(pd.Series(["2025-01-13", "13/01/2025", "05/02/2025"]))
    assert list(d.dt.strftime("%Y-%m-%d")) == ["2025-01-13", "2025-01-13", "2025-02-05"]


def test_generic_rejects_bad_rows():
    df = pd.DataFrame({"date": ["2025-01-06", "not a date", "2025-01-07", "2025-01-07"],
                       "sku": ["A", "A", "A", "A"], "name": ["Pen"] * 4,
                       "type": ["sale", "SALE", "GIFT", "SALE"], "quantity": [3, 1, 1, -2], "unit_price": [20] * 4})
    mv, products, rep = clean_generic(df)
    assert len(mv) == 1 and rep["invalid_date"] == 1 and rep["invalid_type"] == 1 and rep["invalid_quantity"] == 1
