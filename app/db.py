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
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

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
    owner_id    INTEGER REFERENCES users(id),
    updated_by  INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    expires_at   TEXT,
    revoked_at   TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent in-place migration for databases created before multi-user support.

    Existing DBs already have a `projects` table, so the `CREATE TABLE IF NOT EXISTS`
    above is a no-op for them. Add the `owner_id` column if missing (nullable —
    `ALTER TABLE ADD COLUMN` cannot add NOT NULL to a populated table without a
    default; the application always sets it on create). Backfill any orphaned rows
    to the bootstrap admin if one already exists; otherwise the backfill happens
    when the first user registers (see app.main.register).
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER REFERENCES users(id)")
    added_updated_by = "updated_by" not in cols
    if added_updated_by:
        conn.execute("ALTER TABLE projects ADD COLUMN updated_by INTEGER REFERENCES users(id)")
    # Backfill owner_id first (to the bootstrap admin, if one exists) so the
    # updated_by backfill below copies a populated owner_id rather than NULL.
    admin = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
    if admin is not None:
        conn.execute("UPDATE projects SET owner_id = ? WHERE owner_id IS NULL", (admin["id"],))
    if added_updated_by:
        # Best-effort backfill: the owner is the most likely last editor.
        conn.execute("UPDATE projects SET updated_by = owner_id WHERE updated_by IS NULL")


def get_db_path() -> str:
    """Return the current database file path (env-overridable)."""
    return os.environ.get("DASHBOARD_DB") or os.path.join(_PROJECT_ROOT, "data", "dashboard.db")


def init_db(path: str | None = None) -> None:
    """Create the data directory (if needed) and ensure the schema exists."""
    db_path = path or get_db_path()
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


@contextmanager
def get_connection(path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a sqlite3 connection with Row factory; commit/rollback/close."""
    conn = sqlite3.connect(path or get_db_path())
    conn.row_factory = sqlite3.Row
    # Without this, REFERENCES clauses are decorative and orphaned owner_ids slip through.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
