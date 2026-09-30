"""Saves: keep several datasets, each with its own analysis, and switch between them.

Every data table is scoped by user_id. A save is a hidden data owner (a users row that cannot sign
in) holding one dataset. The account's first save uses the account's own id, so data that existed
before saves were added simply becomes the first save. Requests from the signed-in account are
pointed at the loaded save's data owner, so:

* loading a save changes one flag: it is instant and nothing is re-processed;
* a save is not used up by loading it: later changes are kept in that save, as with a game save;
* deleting a save deletes its data owner, and ON DELETE CASCADE removes everything it held.
"""
from __future__ import annotations

import secrets
import shutil
import sqlite3
from pathlib import Path

HIDDEN_PREFIX = "__save_"
FIRST_SAVE_NAME = "My first save"


class SaveError(ValueError):
    pass


def _ensure(db: sqlite3.Connection, owner_id: int) -> None:
    if db.execute("SELECT 1 FROM saves WHERE owner_id=?", (owner_id,)).fetchone() is None:
        db.execute("INSERT INTO saves (owner_id, data_user_id, name, is_active) VALUES (?, ?, ?, 1)",
                   (owner_id, owner_id, FIRST_SAVE_NAME))
        db.commit()
    elif db.execute("SELECT 1 FROM saves WHERE owner_id=? AND is_active=1", (owner_id,)).fetchone() is None:
        db.execute("""UPDATE saves SET is_active=1 WHERE save_id=(SELECT save_id FROM saves WHERE owner_id=?
                      ORDER BY opened_at DESC, save_id DESC LIMIT 1)""", (owner_id,))
        db.commit()


def active(db: sqlite3.Connection, owner_id: int) -> sqlite3.Row:
    _ensure(db, owner_id)
    return db.execute("SELECT * FROM saves WHERE owner_id=? AND is_active=1", (owner_id,)).fetchone()


def data_uid(db: sqlite3.Connection, owner_id: int) -> int:
    """The user id that the account's data queries should use: the loaded save's data owner."""
    return active(db, owner_id)["data_user_id"]


def _get(db, owner_id, save_id) -> sqlite3.Row:
    row = db.execute("SELECT * FROM saves WHERE save_id=? AND owner_id=?", (save_id, owner_id)).fetchone()
    if row is None:
        raise SaveError("That save was not found.")
    return row


def _clean_name(db, owner_id, name, exclude=None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise SaveError("Give the save a name.")
    if len(name) > 60:
        raise SaveError("Keep the name under 60 characters.")
    clash = db.execute("SELECT 1 FROM saves WHERE owner_id=? AND lower(name)=lower(?) AND save_id IS NOT ?",
                       (owner_id, name, exclude)).fetchone()
    if clash:
        raise SaveError("You already have a save with that name.")
    return name


def _busy(db, data_user_id) -> bool:
    return db.execute("SELECT 1 FROM jobs WHERE user_id=? AND status='running'", (data_user_id,)).fetchone() is not None


def _refuse_if_busy(db, owner_id):
    if _busy(db, active(db, owner_id)["data_user_id"]):
        raise SaveError("An analysis or import is still running. Wait for it to finish first.")


def list_saves(db: sqlite3.Connection, owner_id: int) -> list[dict]:
    _ensure(db, owner_id)
    rows = db.execute("""
        SELECT s.save_id, s.name, s.is_active, s.created_at, s.opened_at,
               (SELECT COUNT(*) FROM products p WHERE p.user_id=s.data_user_id AND p.is_active=1) AS products,
               (SELECT COUNT(*) FROM stock_movements m WHERE m.user_id=s.data_user_id) AS entries,
               (SELECT group_concat(file_name, ', ') FROM import_batches b WHERE b.user_id=s.data_user_id) AS files,
               (SELECT MAX(trained_at) FROM model_runs r WHERE r.user_id=s.data_user_id AND r.status='completed') AS analysed_at,
               (SELECT COUNT(*) FROM model_runs r WHERE r.user_id=s.data_user_id AND r.status='completed') AS runs
        FROM saves s WHERE s.owner_id=? ORDER BY s.is_active DESC, s.opened_at DESC, s.save_id DESC""", (owner_id,)).fetchall()
    return [dict(r) for r in rows]


def create(db: sqlite3.Connection, owner_id: int, name: str) -> dict:
    """Start a new, empty save and load it. The save that was loaded keeps all its data."""
    _refuse_if_busy(db, owner_id)
    name = _clean_name(db, owner_id, name)
    current = active(db, owner_id)
    token = secrets.token_hex(8)
    uid = db.execute("INSERT INTO users (username, email, password) VALUES (?, ?, '!')",
                     (HIDDEN_PREFIX + token, f"{HIDDEN_PREFIX}{token}@saves.invalid")).lastrowid
    settings = db.execute("SELECT * FROM user_settings WHERE user_id=?", (current["data_user_id"],)).fetchone()
    if settings is not None:       # carry over currency, lead time and thresholds
        cols = [c for c in settings.keys() if c != "user_id"]
        db.execute(f"INSERT INTO user_settings (user_id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
                   (uid, *[settings[c] for c in cols]))
    db.execute("UPDATE saves SET is_active=0 WHERE owner_id=?", (owner_id,))
    save_id = db.execute("INSERT INTO saves (owner_id, data_user_id, name, is_active) VALUES (?, ?, ?, 1)",
                         (owner_id, uid, name)).lastrowid
    db.commit()
    return {"save_id": save_id, "name": name}


def load(db: sqlite3.Connection, owner_id: int, save_id: int) -> dict:
    save = _get(db, owner_id, save_id)
    if not save["is_active"]:
        _refuse_if_busy(db, owner_id)
        db.execute("UPDATE saves SET is_active=0 WHERE owner_id=?", (owner_id,))
        db.execute("UPDATE saves SET is_active=1, opened_at=CURRENT_TIMESTAMP WHERE save_id=?", (save_id,))
        db.commit()
    return {"save_id": save_id, "name": save["name"]}


def rename(db: sqlite3.Connection, owner_id: int, save_id: int, name: str) -> dict:
    _get(db, owner_id, save_id)
    name = _clean_name(db, owner_id, name, exclude=save_id)
    db.execute("UPDATE saves SET name=? WHERE save_id=?", (name, save_id))
    db.commit()
    return {"save_id": save_id, "name": name}


def delete(db: sqlite3.Connection, owner_id: int, save_id: int, model_dir: str | None = None) -> dict:
    """Delete a save and everything in it. The loaded save cannot be deleted: load another first."""
    save = _get(db, owner_id, save_id)
    if save["is_active"]:
        raise SaveError("This save is loaded. Load a different save before deleting it.")
    uid = save["data_user_id"]
    if _busy(db, uid):
        raise SaveError("An analysis is still running in this save. Wait for it to finish first.")
    if uid == owner_id:
        # the account's own id holds its first save: empty it rather than delete the account
        from .importer import clear_all_data
        clear_all_data(db, uid)
        db.execute("DELETE FROM saves WHERE save_id=?", (save_id,))
    else:
        db.execute("DELETE FROM users WHERE id=?", (uid,))       # cascades to every table, and to the save row
    db.commit()
    if model_dir:
        base = Path(model_dir)
        for f in list(base.glob(f"user{uid}_run*.joblib")) + [base / f"tuned_user{uid}.json"]:
            f.unlink(missing_ok=True)
        shutil.rmtree(base / "cache" / f"user{uid}", ignore_errors=True)
    return {"save_id": save_id, "name": save["name"]}
