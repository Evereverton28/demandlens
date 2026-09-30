"""SQLite access. WAL mode lets the dashboard read while an analysis job writes."""
import sqlite3
from pathlib import Path

from flask import current_app, g


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60, detect_types=0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    schema = (Path(__file__).parent / "schema.sql").read_text()
    conn.executescript(schema)
    # columns added after the first release: add them to databases created before
    if "weeks" not in {r[1] for r in conn.execute("PRAGMA table_info(anomalies)")}:
        conn.execute("ALTER TABLE anomalies ADD COLUMN weeks INTEGER NOT NULL DEFAULT 1")
    conn.commit()
    conn.close()


def rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]
