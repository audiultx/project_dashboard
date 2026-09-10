"""Shared fixtures: TestClient backed by a temporary SQLite database."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make the project root importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_PASSWORD = "admin-password-1"
USER_PASSWORD = "alice-password-1"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Unauthenticated TestClient with the app pointed at a fresh temp database."""
    db_file = tmp_path / "test_dashboard.db"
    monkeypatch.setenv("DASHBOARD_DB", str(db_file))
    monkeypatch.setenv("DASHBOARD_SECRET", "test-secret")
    monkeypatch.delenv("DASHBOARD_ENABLE_SIGNUP", raising=False)
    auth.reset_login_rate_limits()
    db.init_db()
    with TestClient(app) as c:
        yield c


def _register(client, username, password, display_name=None):
    res = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "display_name": display_name or username},
    )
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture()
def admin_client(client):
    """Client with a session for the first (bootstrap, admin) user."""
    _register(client, "admin", ADMIN_PASSWORD, display_name="Admin")
    return client


@pytest.fixture()
def user_client(client, admin_client):
    """Client with a session for a second, non-admin user (alice).

    Uses a separate TestClient so the two users hold separate session cookies,
    while sharing the same temp database.
    """
    # Register alice through a throwaway client carrying the admin's session so
    # the admin client's own cookie is not replaced by alice's auto-login cookie.
    registrar = TestClient(app)
    registrar.cookies.update(admin_client.cookies)
    res = registrar.post(
        "/api/auth/register", json={"username": "alice", "password": USER_PASSWORD, "display_name": "Alice"}
    )
    assert res.status_code == 201, res.text
    c = TestClient(app)
    res = c.post("/api/auth/login", json={"username": "alice", "password": USER_PASSWORD})
    assert res.status_code == 200, res.text
    return c


@pytest.fixture()
def sample_project(admin_client):
    """Create one project (owned by the admin user) and return its JSON."""
    res = admin_client.post(
        "/api/projects",
        json={
            "title": "Retro RTS game",
            "description": "Isometric grid RTS with Tiberium-style harvesting.",
            "category": "Software",
            "status": "planning",
            "priority": "high",
            "tags": "game, rts, typescript",
        },
    )
    assert res.status_code == 201
    return res.json()
