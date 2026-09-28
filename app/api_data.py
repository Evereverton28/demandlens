"""JSON API for data management: products, ledger entries, imports, settings and jobs.

The ledger is append-only: mistakes are corrected with an ADJUSTMENT or a
STOCKTAKE, never by editing history, so the analysis always sees what happened.
"""
import os
import uuid
from datetime import datetime

import pandas as pd
from flask import Blueprint, Response, current_app, g, jsonify, request
from werkzeug.utils import secure_filename

from analytics.preprocess import stock_on_hand

from .auth import api_login_required
from .db import get_db, rows
from .importer import create_scenario, import_file
from .jobs import start_job

bp = Blueprint("api_data", __name__, url_prefix="/api")
TYPES = ("SALE", "RETURN", "RESTOCK", "ADJUSTMENT", "STOCKTAKE")


def _uid() -> int:
    return g.user["id"]


def _num(value, name, minimum=None, integer=False, required=False):
    if value in (None, ""):
        if required:
            raise ValueError(f"{name} is required.")
        return None
    try:
        v = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.")
    if minimum is not None and v < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return v


def _bad(msg, code=400):
    return jsonify(error=msg), code


def _stock_for(db, uid: int, product_ids: list[int]) -> dict:
    if not product_ids:
        return {}
    q = ",".join("?" * len(product_ids))
    mv = pd.read_sql_query(f"SELECT movement_id, product_id, type, quantity, occurred_at FROM stock_movements "
                           f"WHERE user_id=? AND product_id IN ({q})", db, params=(uid, *product_ids))
    if mv.empty:
        return {}
    s = stock_on_hand(mv)
    return {int(k): (None if pd.isna(v) else float(v)) for k, v in s.items()}


def _category_id(db, uid, name):
    if not name:
        return None
    db.execute("INSERT OR IGNORE INTO categories (user_id, name) VALUES (?, ?)", (uid, name.strip()))
    return db.execute("SELECT category_id FROM categories WHERE user_id=? AND name=?", (uid, name.strip())).fetchone()[0]


# ---------------------------------------------------------------- products
@bp.get("/catalogue")
@api_login_required
def catalogue():
    db, uid = get_db(), _uid()
    search = f"%{request.args.get('search', '').strip()}%"
    archived = request.args.get("archived") == "1"
    page = max(int(request.args.get("page", 1)), 1)
    size = 50
    where = "p.user_id=? AND p.is_active=? AND (p.name LIKE ? OR p.sku LIKE ?)"
    args = (uid, 0 if archived else 1, search, search)
    total = db.execute(f"SELECT COUNT(*) FROM products p WHERE {where}", args).fetchone()[0]
    items = rows(db.execute(
        f"""SELECT p.product_id, p.sku, p.name, c.name AS category, p.cost_price, p.selling_price, p.lead_time_days, p.is_active
            FROM products p LEFT JOIN categories c ON c.category_id=p.category_id
            WHERE {where} ORDER BY p.name LIMIT ? OFFSET ?""", (*args, size, (page - 1) * size)))
    stock = _stock_for(db, uid, [i["product_id"] for i in items])
    for i in items:
        i["stock_on_hand"] = stock.get(i["product_id"])
    return jsonify(items=items, total=total, page=page, pages=max(1, -(-total // size)))


@bp.post("/catalogue")
@api_login_required
def create_product():
    db, uid, d = get_db(), _uid(), request.get_json(force=True)
    try:
        sku = (d.get("sku") or "").strip()
        name = (d.get("name") or "").strip()
        if not sku or not name:
            raise ValueError("A product needs a code (SKU) and a name.")
        cost = _num(d.get("cost_price"), "Cost price", 0)
        price = _num(d.get("selling_price"), "Selling price", 0)
        lead = _num(d.get("lead_time_days"), "Lead time", 0, integer=True)
        opening = _num(d.get("opening_stock"), "Opening stock", 0)
    except ValueError as e:
        return _bad(str(e))
    try:
        cur = db.execute("""INSERT INTO products (user_id, sku, name, category_id, cost_price, selling_price, lead_time_days)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                         (uid, sku, name, _category_id(db, uid, d.get("category")), cost, price, lead))
    except Exception:
        return _bad(f"A product with code {sku} already exists.", 409)
    if opening is not None:
        db.execute("""INSERT INTO stock_movements (user_id, product_id, type, quantity, occurred_at, source, note)
                      VALUES (?, ?, 'STOCKTAKE', ?, ?, 'manual', 'Opening stock')""",
                   (uid, cur.lastrowid, opening, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    db.commit()
    return jsonify(product_id=cur.lastrowid), 201


@bp.put("/catalogue/<int:pid>")
@api_login_required
def update_product(pid):
    db, uid, d = get_db(), _uid(), request.get_json(force=True)
    if not db.execute("SELECT 1 FROM products WHERE product_id=? AND user_id=?", (pid, uid)).fetchone():
        return _bad("Product not found.", 404)
    try:
        name = (d.get("name") or "").strip()
        if not name:
            raise ValueError("A product needs a name.")
        vals = (name, _category_id(db, uid, d.get("category")), _num(d.get("cost_price"), "Cost price", 0),
                _num(d.get("selling_price"), "Selling price", 0), _num(d.get("lead_time_days"), "Lead time", 0, True))
    except ValueError as e:
        return _bad(str(e))
    db.execute("""UPDATE products SET name=?, category_id=?, cost_price=?, selling_price=?, lead_time_days=?
                  WHERE product_id=? AND user_id=?""", (*vals, pid, uid))
    db.commit()
    return jsonify(ok=True)


@bp.post("/catalogue/<int:pid>/archive")
@api_login_required
def archive_product(pid):
    db, uid = get_db(), _uid()
    archived = bool(request.get_json(force=True).get("archived", True))
    n = db.execute("UPDATE products SET is_active=? WHERE product_id=? AND user_id=?",
                   (0 if archived else 1, pid, uid)).rowcount
    db.commit()
    return jsonify(ok=True) if n else _bad("Product not found.", 404)


@bp.get("/categories")
@api_login_required
def categories():
    return jsonify(rows(get_db().execute("SELECT category_id, name FROM categories WHERE user_id=? ORDER BY name", (_uid(),))))


# ---------------------------------------------------------------- ledger
@bp.get("/movements")
@api_login_required
def movements():
    db, uid = get_db(), _uid()
    limit = min(int(request.args.get("limit", 50)), 500)
    pid = request.args.get("product_id")
    sql = """SELECT m.movement_id, m.product_id, p.name AS product, p.sku, m.type, m.quantity, m.unit_price,
                    m.occurred_at, m.source, m.note
             FROM stock_movements m JOIN products p ON p.product_id=m.product_id WHERE m.user_id=?"""
    args = [uid]
    if pid:
        sql += " AND m.product_id=?"
        args.append(int(pid))
    sql += " ORDER BY m.occurred_at DESC, m.movement_id DESC LIMIT ?"
    return jsonify(rows(db.execute(sql, (*args, limit))))


@bp.post("/movements")
@api_login_required
def record_movement():
    db, uid, d = get_db(), _uid(), request.get_json(force=True)
    try:
        pid = _num(d.get("product_id"), "Product", integer=True, required=True)
        mtype = (d.get("type") or "").upper()
        if mtype not in TYPES:
            raise ValueError("Choose a valid entry type.")
        qty = _num(d.get("quantity"), "Quantity", required=True)
        if mtype == "ADJUSTMENT":
            if qty == 0:
                raise ValueError("An adjustment cannot be zero.")
        elif qty <= 0 and mtype != "STOCKTAKE":
            raise ValueError("Quantity must be greater than zero.")
        elif qty < 0:
            raise ValueError("A stock count cannot be negative.")
        price = _num(d.get("unit_price"), "Unit price", 0)
        when = d.get("occurred_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        when = pd.Timestamp(when).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError) as e:
        return _bad(str(e) if str(e) else "Check the date.")
    product = db.execute("SELECT selling_price, cost_price FROM products WHERE product_id=? AND user_id=? AND is_active=1",
                         (pid, uid)).fetchone()
    if product is None:
        return _bad("Product not found.", 404)
    if price is None:
        price = product["selling_price"] if mtype in ("SALE", "RETURN") else product["cost_price"]
    if mtype == "SALE":
        stock = _stock_for(db, uid, [pid]).get(pid)
        if stock is not None and qty > stock:
            return _bad(f"Only {stock:g} in stock. Record the restock or a stock count first.")
    cur = db.execute("""INSERT INTO stock_movements (user_id, product_id, type, quantity, unit_price, occurred_at, source, note)
                        VALUES (?, ?, ?, ?, ?, ?, 'manual', ?)""",
                     (uid, pid, mtype, qty, price, when, (d.get("note") or "").strip() or None))
    db.commit()
    return jsonify(movement_id=cur.lastrowid, stock_on_hand=_stock_for(db, uid, [pid]).get(pid)), 201


# ---------------------------------------------------------------- imports
@bp.get("/imports")
@api_login_required
def imports():
    return jsonify(rows(get_db().execute(
        "SELECT * FROM import_batches WHERE user_id=? ORDER BY batch_id DESC", (_uid(),))))


@bp.post("/imports")
@api_login_required
def upload():
    f = request.files.get("file")
    fmt = request.form.get("format", "generic")
    if f is None or not f.filename:
        return _bad("Choose a file to import.")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        return _bad("Import a .csv or .xlsx file.")
    if fmt not in ("generic", "online_retail"):
        return _bad("Choose a file format.")
    os.makedirs(current_app.config["UPLOAD_DIR"], exist_ok=True)
    path = os.path.join(current_app.config["UPLOAD_DIR"], f"{uuid.uuid4().hex}{ext}")
    f.save(path)
    uid, name, synthetic = _uid(), secure_filename(f.filename), request.form.get("synthetic") == "1"

    def work(conn, progress):
        try:
            rep = import_file(conn, uid, path, fmt, synthetic, name, progress)
        finally:
            os.remove(path)
        return f"Imported {rep['movements']:,} entries for {rep['products']:,} products; {rep['rows_rejected']:,} rows were set aside."
    return jsonify(job_id=start_job(current_app._get_current_object(), get_db(), uid, "import", work)), 202


@bp.post("/scenario")
@api_login_required
def scenario():
    db, uid = get_db(), _uid()
    try:
        res = create_scenario(db, uid)
    except ValueError as e:
        return _bad(str(e))
    return jsonify(res), 201


@bp.get("/template.csv")
@api_login_required
def template():
    body = ("date,sku,name,category,type,quantity,unit_price,cost_price,lead_time_days\n"
            "2026-01-05,P001,Exercise book 96pg,Stationery,SALE,12,60,45,7\n"
            "2026-01-05,P002,Blue pen,Stationery,SALE,30,20,12,7\n"
            "2026-01-06,P001,Exercise book 96pg,Stationery,RESTOCK,100,45,45,7\n"
            "2026-01-06,P002,Blue pen,Stationery,STOCKTAKE,240,,12,7\n")
    return Response(body, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=demandlens_template.csv"})


# ---------------------------------------------------------------- settings & jobs
@bp.get("/settings")
@api_login_required
def get_settings():
    from analytics.pipeline import load_settings
    return jsonify(load_settings(get_db(), _uid()))


@bp.put("/settings")
@api_login_required
def put_settings():
    db, d = get_db(), request.get_json(force=True)
    try:
        vals = (_num(d.get("default_lead_time_days"), "Lead time", 0, True, True),
                _num(d.get("review_period_days"), "Review period", 1, True, True),
                _num(d.get("overstock_weeks"), "Overstock threshold", 1, True, True),
                (d.get("holiday_country") or "").strip().upper()[:2] or "KE",
                (d.get("currency") or "").strip().upper()[:3] or "KES")
    except ValueError as e:
        return _bad(str(e))
    db.execute("""UPDATE user_settings SET default_lead_time_days=?, review_period_days=?, overstock_weeks=?,
                  holiday_country=?, currency=? WHERE user_id=?""", (*vals, _uid()))
    db.commit()
    return jsonify(ok=True)


@bp.get("/jobs/<int:job_id>")
@api_login_required
def job(job_id):
    j = get_db().execute("SELECT * FROM jobs WHERE job_id=? AND user_id=?", (job_id, _uid())).fetchone()
    return jsonify(dict(j)) if j else _bad("Job not found.", 404)


@bp.get("/jobs/latest")
@api_login_required
def latest_job():
    j = get_db().execute("SELECT * FROM jobs WHERE user_id=? AND kind=? ORDER BY job_id DESC LIMIT 1",
                         (_uid(), request.args.get("kind", "analysis"))).fetchone()
    return jsonify(dict(j) if j else None)
