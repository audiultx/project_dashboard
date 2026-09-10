"""Data access for users and projects: list/create/get/update/delete/stats.

All SQL lives here; routes call these functions and never touch the DB directly.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from . import db

_FIELDS = ("title", "description", "category", "status", "priority", "tags")

# Projects are always read joined to their owner and last editor so responses
# can expose owner_username / updated_by_username without extra queries.
_PROJECT_SELECT = (
    "SELECT p.*, u.username AS owner_username, ub.username AS updated_by_username "
    "FROM projects p "
    "LEFT JOIN users u ON u.id = p.owner_id "
    "LEFT JOIN users ub ON ub.id = p.updated_by"
)

_USER_FIELDS = ("id", "username", "display_name", "is_admin", "created_at", "last_login_at")


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------- users

def create_user(username: str, password_hash: str, display_name: str = "", is_admin: bool = False) -> dict[str, Any]:
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, ?)",
            (username, password_hash, display_name, int(is_admin)),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def create_first_or_regular_user(
    username: str, password_hash: str, display_name: str = ""
) -> tuple[dict[str, Any], bool]:
    """Create a user, atomically deciding whether this is the bootstrap (first) user.

    The bootstrap decision is made solely by the in-transaction user count:
    ``count == 0`` (no users exist yet) makes this user the admin, everyone
    else is created as a regular user. The count and the INSERT run inside a
    single IMMEDIATE transaction, so concurrent first-registrations serialize:
    the loser blocks, then sees a non-zero count and is created as a regular
    user. The UNIQUE username constraint cannot arbitrate this race because the
    racers use different usernames. Returns ``(user_row, was_bootstrap)``.
    """
    with db.get_connection() as conn:
        # BEGIN IMMEDIATE takes the write lock up front; a concurrent first
        # registration blocks here until this transaction commits.
        conn.execute("BEGIN IMMEDIATE")
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, ?)",
            (username, password_hash, display_name, int(count == 0)),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row), count == 0


def get_user(user_id: int) -> dict[str, Any] | None:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return _row_to_dict(row) if row else None


def count_users() -> int:
    with db.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def update_last_login(user_id: int) -> None:
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?", (user_id,))


def backfill_project_owners(owner_id: int) -> int:
    """Assign ownerless projects to the given user (one-time migration step).

    Also fills `updated_by` for rows that predate the column (the bootstrap
    admin is the best guess for the last editor of legacy data).
    """
    with db.get_connection() as conn:
        conn.execute("UPDATE projects SET updated_by = ? WHERE updated_by IS NULL", (owner_id,))
        cur = conn.execute("UPDATE projects SET owner_id = ? WHERE owner_id IS NULL", (owner_id,))
    return cur.rowcount


# ---------------------------------------------------------------- projects

def list_projects(
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """List projects, newest first, with optional status/category/text filters.

    Shared workspace: every authenticated user sees every project.
    """
    sql = _PROJECT_SELECT + " WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND p.status = ?"
        params.append(status)
    if category is not None:
        sql += " AND p.category = ?"
        params.append(category)
    if q is not None:
        sql += " AND (p.title LIKE ? OR p.description LIKE ? OR p.tags LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY p.created_at DESC, p.id DESC"
    with db.get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def create_project(data: dict[str, Any], owner_id: int) -> dict[str, Any]:
    fields = [f for f in _FIELDS if f in data]
    cols = ", ".join(fields + ["owner_id", "updated_by"])
    sql = f"INSERT INTO projects ({cols}) VALUES ({', '.join('?' for _ in fields + ['owner_id', 'updated_by'])})"
    with db.get_connection() as conn:
        cur = conn.execute(sql, [data[f] for f in fields] + [owner_id, owner_id])
        row = conn.execute(_PROJECT_SELECT + " WHERE p.id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_project(project_id: int) -> dict[str, Any] | None:
    with db.get_connection() as conn:
        row = conn.execute(_PROJECT_SELECT + " WHERE p.id = ?", (project_id,)).fetchone()
    return _row_to_dict(row) if row else None


def update_project(project_id: int, data: dict[str, Any], updated_by: int) -> dict[str, Any] | None:
    """Apply a partial update; returns the updated project or None if missing.

    Ownership is enforced by the caller (route layer) — this function is
    owner-agnostic. `updated_by` records who made the edit.
    """
    fields = [f for f in _FIELDS if f in data and data[f] is not None]
    with db.get_connection() as conn:
        if fields:
            sets = ", ".join(f"{f} = ?" for f in fields)
            conn.execute(
                f"UPDATE projects SET {sets}, updated_by = ?, updated_at = datetime('now') WHERE id = ?",
                [data[f] for f in fields] + [updated_by, project_id],
            )
        row = conn.execute(_PROJECT_SELECT + " WHERE p.id = ?", (project_id,)).fetchone()
    return _row_to_dict(row) if row else None


def delete_project(project_id: int) -> bool:
    with db.get_connection() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return cur.rowcount > 0


def stats() -> dict[str, Any]:
    """Counts per status (zero-filled, existing keys) plus per-owner counts.

    The top-level status keys are preserved for existing consumers; per-user
    counts are nested under "by_owner" keyed by username.
    """
    from .models import STATUSES

    with db.get_connection() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM projects GROUP BY status").fetchall()
        owner_rows = conn.execute(
            "SELECT COALESCE(u.username, 'unassigned') AS owner, COUNT(*) AS n "
            "FROM projects p LEFT JOIN users u ON u.id = p.owner_id GROUP BY owner"
        ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    return {**{s: counts.get(s, 0) for s in STATUSES}, "by_owner": {row["owner"]: row["n"] for row in owner_rows}}


# ---------------------------------------------------------------- api tokens
#
# Personal API tokens (GitHub-PAT model): any authenticated user mints tokens
# that always act as their own identity. Only the SHA-256 hash is persisted;
# the plaintext is returned exactly once at creation time.

TOKEN_PREFIX = "pdt_"
_TOKEN_SELECT = (
    "SELECT t.*, u.username AS owner_username "
    "FROM api_tokens t "
    "LEFT JOIN users u ON u.id = t.user_id"
)


def token_hash(token: str) -> str:
    """SHA-256 of the plaintext token.

    256-bit random secrets have no brute-force surface, so a fast hash is
    correct here (in contrast to passwords, where scrypt's cost is deliberate).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(user_id: int, name: str, expires_at: str | None = None) -> tuple[str, dict[str, Any]]:
    """Mint a new token for the given user.

    Returns ``(plaintext, token_row)`` — the plaintext is the only time it
    exists; only its hash is stored.
    """
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO api_tokens (name, user_id, token_hash, expires_at) VALUES (?, ?, ?, ?)",
            (name, user_id, token_hash(token), expires_at),
        )
        row = conn.execute(_TOKEN_SELECT + " WHERE t.id = ?", (cur.lastrowid,)).fetchone()
    return token, _row_to_dict(row)


def get_token_by_hash(token_hash_value: str) -> dict[str, Any] | None:
    with db.get_connection() as conn:
        row = conn.execute(_TOKEN_SELECT + " WHERE t.token_hash = ?", (token_hash_value,)).fetchone()
    return _row_to_dict(row) if row else None


def get_token(token_id: int) -> dict[str, Any] | None:
    with db.get_connection() as conn:
        row = conn.execute(_TOKEN_SELECT + " WHERE t.id = ?", (token_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_tokens(user_id: int | None = None) -> list[dict[str, Any]]:
    """List tokens; by user when user_id is set, otherwise all (admin view)."""
    if user_id is not None:
        sql = _TOKEN_SELECT + " WHERE t.user_id = ? ORDER BY t.created_at DESC, t.id DESC"
        params: tuple = (user_id,)
    else:
        sql = _TOKEN_SELECT + " ORDER BY t.user_id, t.created_at DESC, t.id DESC"
        params = ()
    with db.get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def revoke_token(token_id: int) -> bool:
    """Set revoked_at; returns False when the token is missing or already revoked."""
    with db.get_connection() as conn:
        cur = conn.execute(
            "UPDATE api_tokens SET revoked_at = datetime('now') WHERE id = ? AND revoked_at IS NULL",
            (token_id,),
        )
    return cur.rowcount > 0


def touch_token(token_id: int) -> None:
    with db.get_connection() as conn:
        conn.execute("UPDATE api_tokens SET last_used_at = datetime('now') WHERE id = ?", (token_id,))
