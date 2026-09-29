"""Loading data into the ledger.

* import_file      - Online Retail II or the generic template, via analytics.ingest
* create_scenario  - simulated opening stock for datasets without stock data (clearly flagged)
* migrate_sims     - brings users, items and transactions over from the old SIMS database
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.ingest import clean_generic, clean_online_retail, read_generic, read_online_retail


def _category_ids(db, user_id: int, names) -> dict:
    ids = {}
    for name in sorted({n for n in names if isinstance(n, str) and n.strip()}):
        db.execute("INSERT OR IGNORE INTO categories (user_id, name) VALUES (?, ?)", (user_id, name.strip()))
        ids[name] = db.execute("SELECT category_id FROM categories WHERE user_id=? AND name=?",
                               (user_id, name.strip())).fetchone()[0]
    return ids


def _upsert_products(db, user_id: int, products: pd.DataFrame) -> dict:
    cats = _category_ids(db, user_id, products.get("category", pd.Series(dtype=object)).tolist())
    for r in products.to_dict("records"):
        cat = cats.get(r.get("category"))
        cost = r.get("cost_price"); lead = r.get("lead_time_days"); price = r.get("selling_price")
        clean = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else v
        db.execute(
            """INSERT INTO products (user_id, sku, name, category_id, cost_price, selling_price, lead_time_days)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, sku) DO UPDATE SET
                 name=excluded.name,
                 category_id=COALESCE(excluded.category_id, products.category_id),
                 cost_price=COALESCE(excluded.cost_price, products.cost_price),
                 selling_price=COALESCE(excluded.selling_price, products.selling_price),
                 lead_time_days=COALESCE(excluded.lead_time_days, products.lead_time_days)""",
            (user_id, str(r["sku"]), str(r["name"])[:200], cat, clean(cost), clean(price),
             None if clean(lead) is None else int(lead)))
    return dict(db.execute("SELECT sku, product_id FROM products WHERE user_id=?", (user_id,)).fetchall())


def import_file(db: sqlite3.Connection, user_id: int, path: str | Path, fmt: str, synthetic: bool = False,
                file_name: str | None = None, progress=lambda m: None) -> dict:
    progress("Reading the file")
    if fmt == "online_retail":
        movements, products, report = clean_online_retail(read_online_retail(path))
    elif fmt == "generic":
        movements, products, report = clean_generic(read_generic(path))
    else:
        raise ValueError(f"Unknown format: {fmt}")
    if movements.empty:
        raise ValueError("No valid rows were found in the file.")

    progress(f"Saving {len(products):,} products and {len(movements):,} movements")
    cur = db.execute(
        """INSERT INTO import_batches (user_id, file_name, format, rows_read, rows_imported, rows_rejected, report_json, is_synthetic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, file_name or Path(path).name, fmt, report["rows_read"], len(movements), report["rows_rejected"],
         json.dumps(report), int(synthetic)))
    batch_id = cur.lastrowid
    sku_to_id = _upsert_products(db, user_id, products)
    mv = movements.assign(product_id=movements["sku"].map(sku_to_id))
    mv["occurred_at"] = pd.to_datetime(mv["occurred_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    mv["unit_price"] = mv["unit_price"].astype(object).where(mv["unit_price"].notna(), None)
    db.executemany(
        """INSERT INTO stock_movements (user_id, product_id, type, quantity, unit_price, occurred_at, source, batch_id)
           VALUES (?, ?, ?, ?, ?, ?, 'import', ?)""",
        ((user_id, int(p), t, float(q), u, o, batch_id) for p, t, q, u, o in
         mv[["product_id", "type", "quantity", "unit_price", "occurred_at"]].itertuples(index=False)))
    if fmt == "online_retail":
        # The dataset is a UK retailer trading in pounds: align the holiday calendar and currency.
        db.execute("UPDATE user_settings SET holiday_country='GB', currency='GBP' WHERE user_id=?", (user_id,))
    db.commit()
    report["batch_id"] = batch_id
    return report


def create_scenario(db: sqlite3.Connection, user_id: int, seed: int = 7) -> dict:
    """Simulated stock levels for a dataset that has none (for example Online Retail II).

    Each product sold in the last year gets a stock count equal to its recent
    average weekly sales times a random 1-16 weeks (rounded up, so at least one
    unit), recorded as a STOCKTAKE just after the last movement. The batch is
    flagged as a scenario and the interface says so.
    """
    mv = pd.read_sql_query("SELECT product_id, type, quantity, occurred_at FROM stock_movements WHERE user_id=?",
                           db, params=(user_id,))
    if mv.empty:
        raise ValueError("Import sales data before creating a stock scenario.")
    mv["occurred_at"] = pd.to_datetime(mv["occurred_at"], format="mixed")
    end = mv["occurred_at"].max()
    sales = mv[mv["type"] == "SALE"]
    recent = sales[sales["occurred_at"] > end - pd.Timedelta(weeks=12)].groupby("product_id")["quantity"].sum() / 12
    year = sales[sales["occurred_at"] > end - pd.Timedelta(weeks=52)].groupby("product_id")["quantity"].sum() / 52
    rate = recent.reindex(year.index).fillna(0).where(lambda s: s > 0, year * 0.5)
    rng = np.random.default_rng(seed)
    weeks = rng.uniform(1, 16, size=len(rate))
    level = np.ceil(rate.to_numpy() * weeks)
    when = (end + pd.Timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    cur = db.execute(
        """INSERT INTO import_batches (user_id, file_name, format, rows_read, rows_imported, rows_rejected, report_json, is_scenario)
           VALUES (?, 'simulated stock levels', 'scenario', ?, ?, 0, ?, 1)""",
        (user_id, len(rate), len(rate), json.dumps({"method": "recent weekly sales x U(1, 16) weeks, rounded up", "seed": seed})))
    db.executemany(
        """INSERT INTO stock_movements (user_id, product_id, type, quantity, occurred_at, source, batch_id, note)
           VALUES (?, ?, 'STOCKTAKE', ?, ?, 'scenario', ?, 'Simulated stock level')""",
        [(user_id, int(pid), float(q), when, cur.lastrowid) for pid, q in zip(rate.index, level)])
    db.commit()
    return {"products": int(len(rate)), "batch_id": cur.lastrowid}


def migrate_sims(db: sqlite3.Connection, sims_path: str | Path) -> dict:
    """Bring users, items and transactions across from a SIMS inventory.db.

    Assumptions (documented): SIMS 'OUT' becomes SALE and 'IN' becomes RESTOCK;
    each item's current quantity becomes a STOCKTAKE at migration time so the
    ledger reproduces the stock SIMS showed.
    """
    old = sqlite3.connect(sims_path)
    old.row_factory = sqlite3.Row
    counts = {"users": 0, "products": 0, "movements": 0}
    user_map = {}
    for u in old.execute("SELECT * FROM users"):
        existing = db.execute("SELECT id FROM users WHERE username=?", (u["username"],)).fetchone()
        if existing:
            user_map[u["id"]] = existing[0]
            continue
        cur = db.execute("INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                         (u["username"], u["email"], u["password"]))      # werkzeug hashes carry over
        db.execute("INSERT INTO user_settings (user_id) VALUES (?)", (cur.lastrowid,))
        user_map[u["id"]] = cur.lastrowid
        counts["users"] += 1
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    for uid_old, uid in user_map.items():
        items = pd.read_sql_query("SELECT * FROM items WHERE user_id=?", old, params=(uid_old,))
        if items.empty:
            continue
        cur = db.execute(
            """INSERT INTO import_batches (user_id, file_name, format, rows_read, rows_imported, rows_rejected, report_json)
               VALUES (?, ?, 'sims', ?, ?, 0, ?)""",
            (uid, Path(sims_path).name, len(items), len(items),
             json.dumps({"assumptions": "OUT -> SALE, IN -> RESTOCK, current quantity -> STOCKTAKE"})))
        batch = cur.lastrowid
        prods = pd.DataFrame({"sku": "SIMS-" + items["item_id"].astype(str), "name": items["item_name"],
                              "category": items["category"], "selling_price": items["unit_price"]})
        sku_to_id = _upsert_products(db, uid, prods)
        counts["products"] += len(prods)
        price = dict(zip(items["item_id"], items["unit_price"]))
        for t in old.execute("SELECT * FROM transactions WHERE user_id=?", (uid_old,)):
            pid = sku_to_id.get(f"SIMS-{t['item_id']}")
            if pid is None:
                continue
            db.execute(
                """INSERT INTO stock_movements (user_id, product_id, type, quantity, unit_price, occurred_at, source, batch_id)
                   VALUES (?, ?, ?, ?, ?, ?, 'migration', ?)""",
                (uid, pid, "SALE" if t["type"] == "OUT" else "RESTOCK", float(t["quantity"]),
                 price.get(t["item_id"]), t["date"], batch))
            counts["movements"] += 1
        for it in items.to_dict("records"):
            db.execute(
                """INSERT INTO stock_movements (user_id, product_id, type, quantity, occurred_at, source, batch_id, note)
                   VALUES (?, ?, 'STOCKTAKE', ?, ?, 'migration', ?, 'Quantity in SIMS at migration')""",
                (uid, sku_to_id[f"SIMS-{it['item_id']}"], float(it["quantity"]), now, batch))
            counts["movements"] += 1
    db.commit()
    old.close()
    return counts


def delete_batch(db: sqlite3.Connection, user_id: int, batch_id: int) -> dict:
    """Remove one import and everything that came with it.

    The batch's movements go with it (the schema cascades), then any product
    left with no movements at all is removed, and the stored analysis results
    are cleared because they were computed from data that no longer exists.
    """
    batch = db.execute("SELECT * FROM import_batches WHERE batch_id=? AND user_id=?", (batch_id, user_id)).fetchone()
    if batch is None:
        raise ValueError("That import was not found.")
    moved = db.execute("SELECT COUNT(*) FROM stock_movements WHERE batch_id=? AND user_id=?",
                       (batch_id, user_id)).fetchone()[0]
    db.execute("DELETE FROM import_batches WHERE batch_id=? AND user_id=?", (batch_id, user_id))
    orphans = db.execute(
        """DELETE FROM products WHERE user_id=? AND product_id NOT IN
           (SELECT DISTINCT product_id FROM stock_movements WHERE user_id=?)""", (user_id, user_id)).rowcount
    runs = _clear_results(db, user_id)
    db.commit()
    return {"file_name": batch["file_name"], "movements": moved, "products_removed": orphans, "runs_cleared": runs}


def clear_all_data(db: sqlite3.Connection, user_id: int) -> dict:
    """Empty this account completely: products, ledger, imports and results. Settings are kept."""
    counts = {
        "movements": db.execute("DELETE FROM stock_movements WHERE user_id=?", (user_id,)).rowcount,
        "products": db.execute("DELETE FROM products WHERE user_id=?", (user_id,)).rowcount,
        "imports": db.execute("DELETE FROM import_batches WHERE user_id=?", (user_id,)).rowcount,
    }
    db.execute("DELETE FROM categories WHERE user_id=?", (user_id,))
    counts["runs"] = _clear_results(db, user_id)
    db.commit()
    return counts


def _clear_results(db: sqlite3.Connection, user_id: int) -> int:
    """Analysis results describe a particular ledger; once it changes they are removed."""
    db.execute("DELETE FROM anomalies WHERE user_id=?", (user_id,))
    return db.execute("DELETE FROM model_runs WHERE user_id=?", (user_id,)).rowcount
