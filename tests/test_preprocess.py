"""Weekly panel construction and stock-on-hand from the ledger."""
import numpy as np
import pandas as pd

from analytics.preprocess import build_panel, last_complete_week, stock_on_hand


def mv(rows):
    return pd.DataFrame(rows, columns=["movement_id", "product_id", "type", "quantity", "unit_price", "occurred_at"]).assign(
        occurred_at=lambda d: pd.to_datetime(d["occurred_at"]))


def test_panel_zero_fill_and_first_sale():
    ledger = mv([
        (1, 1, "SALE", 5, 1.0, "2024-01-01"),      # Monday, week 1
        (2, 2, "SALE", 3, 1.0, "2024-01-15"),      # product 2 starts in week 3
        (3, 1, "SALE", 2, 1.0, "2024-01-22"),
        (4, 1, "RETURN", 4, 1.0, "2024-01-23"),    # more returned than sold that week -> 0, not negative
        (5, 1, "SALE", 9, 1.0, "2024-01-31"),      # Wednesday of an incomplete week: excluded
    ])
    p = build_panel(ledger)
    assert list(p.weeks.strftime("%Y-%m-%d")) == ["2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22"]
    np.testing.assert_array_equal(p.units[0], [5, 0, 0, 0])                 # zero-filled after the first sale
    assert np.isnan(p.units[1, 0]) and np.isnan(p.units[1, 1])              # not filled before launch
    assert p.units[1, 2] == 3


def test_last_complete_week():
    assert last_complete_week(pd.Timestamp("2024-01-31 12:00")) == pd.Timestamp("2024-01-22")
    assert last_complete_week(pd.Timestamp("2024-01-28 23:30")) == pd.Timestamp("2024-01-22")


def test_stock_on_hand_uses_latest_stocktake():
    ledger = mv([
        (1, 1, "STOCKTAKE", 50, None, "2024-01-01"),
        (2, 1, "SALE", 10, 1.0, "2024-01-02"),
        (3, 1, "STOCKTAKE", 30, None, "2024-01-03"),   # counted level replaces the running total
        (4, 1, "SALE", 4, 1.0, "2024-01-04"),
        (5, 1, "RESTOCK", 20, 1.0, "2024-01-05"),
        (6, 1, "ADJUSTMENT", -1, None, "2024-01-06"),
        (7, 2, "SALE", 5, 1.0, "2024-01-02"),          # sales only: stock unknown
    ])
    s = stock_on_hand(ledger)
    assert s[1] == 45 and np.isnan(s[2])
