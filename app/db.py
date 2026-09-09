"""SQLite connection handling and schema bootstrap.

The database file location is resolved at call time from the DASHBOARD_DB
environment variable (default: <project root>/data/dashboard.db). Resolving
lazily lets tests point the app at a temporary file via monkeypatch.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'Other',
    status      TEXT NOT NULL DEFAULT 'idea'
                CHECK (status IN ('idea','planning','in_progress','paused','done')),
    priority    TEXT NOT NULL DEFAULT 'medium'
                CHECK (priority IN ('low','medium','high')),
    tags        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_db_path() -> str:
    """Return the current database file path (env-overridable)."""
    return os.environ.get("DASHBOARD_DB") or os.path.join(_PROJECT_ROOT, "data", "dashboard.db")


def init_db(path: str | None = None) -> None:
    """Create the data directory (if needed) and ensure the schema exists."""
    db_path = path or get_db_path()
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_connection(path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a sqlite3 connection with Row factory; commit/rollback/close."""
    conn = sqlite3.connect(path or get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
