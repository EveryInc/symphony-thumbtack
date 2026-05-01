"""SQLite storage. Single-file, no migrations — tables are created on first use."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DB_PATH_ENV = "PROMATCH_DB"
DEFAULT_DB_PATH = os.path.expanduser("~/.promatch/promatch.db")


def db_path() -> str:
    return os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH


def _ensure_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category_slug TEXT NOT NULL REFERENCES categories(slug),
    zip TEXT NOT NULL,
    rating REAL NOT NULL DEFAULT 4.5,
    base_rate_cents INTEGER NOT NULL DEFAULT 8000
);

CREATE INDEX IF NOT EXISTS idx_pros_category ON pros(category_slug);
CREATE INDEX IF NOT EXISTS idx_pros_zip ON pros(zip);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    category_slug TEXT NOT NULL REFERENCES categories(slug),
    zip TEXT NOT NULL,
    budget_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',  -- open|matched|booked|cancelled
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    pro_id INTEGER NOT NULL REFERENCES pros(id),
    price_cents INTEGER NOT NULL,
    eta_hours INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending', -- pending|accepted|declined
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_quotes_request ON quotes(request_id);
CREATE INDEX IF NOT EXISTS idx_quotes_status ON quotes(status);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    _ensure_dir(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    """Drop and recreate everything. Used by `promatch reset`."""
    path = db_path()
    if os.path.exists(path):
        os.remove(path)
    init_db()
