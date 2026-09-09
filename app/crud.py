"""Data access for projects: list/create/get/update/delete/stats."""

from __future__ import annotations

from typing import Any

from . import db

_FIELDS = ("title", "description", "category", "status", "priority", "tags")


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row)


def list_projects(
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """List projects, newest first, with optional status/category/text filters."""
    sql = "SELECT * FROM projects WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if category is not None:
        sql += " AND category = ?"
        params.append(category)
    if q is not None:
        sql += " AND (title LIKE ? OR description LIKE ? OR tags LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY created_at DESC, id DESC"
    with db.get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def create_project(data: dict[str, Any]) -> dict[str, Any]:
    fields = [f for f in _FIELDS if f in data]
    sql = (
        f"INSERT INTO projects ({', '.join(fields)}) "
        f"VALUES ({', '.join('?' for _ in fields)})"
    )
    with db.get_connection() as conn:
        cur = conn.execute(sql, [data[f] for f in fields])
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_project(project_id: int) -> dict[str, Any] | None:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _row_to_dict(row) if row else None


def update_project(project_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a partial update; returns the updated project or None if missing."""
    fields = [f for f in _FIELDS if f in data and data[f] is not None]
    with db.get_connection() as conn:
        if fields:
            sets = ", ".join(f"{f} = ?" for f in fields)
            conn.execute(
                f"UPDATE projects SET {sets}, updated_at = datetime('now') WHERE id = ?",
                [data[f] for f in fields] + [project_id],
            )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _row_to_dict(row) if row else None


def delete_project(project_id: int) -> bool:
    with db.get_connection() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return cur.rowcount > 0


def stats() -> dict[str, int]:
    """Counts per status (all statuses always present, zero-filled)."""
    from .models import STATUSES

    with db.get_connection() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM projects GROUP BY status").fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    return {s: counts.get(s, 0) for s in STATUSES}
