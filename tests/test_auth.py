"""Auth tests: bootstrap, login/logout, signup gating, rate limiting, migration."""

import os
import sqlite3
import threading

from fastapi.testclient import TestClient

import pytest

from app import auth, crud, db
from app.main import app


# ---------------------------------------------------------------- bootstrap

def test_first_register_becomes_admin(client):
    res = client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    assert res.status_code == 201
    data = res.json()
    assert data["username"] == "admin"
    assert data["is_admin"] is True
    assert "password_hash" not in data
    # registration auto-logs-in: session cookie is set
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "admin"


def test_register_requires_min_password(client):
    assert client.post("/api/auth/register", json={"username": "x", "password": "short"}).status_code == 422


def test_register_blank_username_rejected(client):
    assert client.post("/api/auth/register", json={"username": "   ", "password": "password-123"}).status_code == 422


def test_username_stored_lowercase(client):
    client.post("/api/auth/register", json={"username": "MiXeD", "password": "password-123"})
    assert client.get("/api/auth/me").json()["username"] == "mixed"


def test_concurrent_first_registrations_yield_single_admin(client):
    """Racers with different usernames all pass the zero-user policy check, so
    the atomic count+insert in create_first_or_regular_user is what must let
    exactly one of them become the bootstrap admin."""
    n = 4
    barrier = threading.Barrier(n)
    outcomes: list = [None] * n
    errors: list[Exception] = []

    def worker(i):
        try:
            barrier.wait()
            outcomes[i] = crud.create_first_or_regular_user(
                f"racer{i}", auth.hash_password("password-123")
            )
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert sum(was for _, was in outcomes) == 1
    winner = next(u for u, was in outcomes if was)
    assert winner["is_admin"]
    assert all(not u["is_admin"] for u, was in outcomes if not was)


def test_create_first_or_regular_user_rejects_when_closed(client):
    """With users already present, allow_regular=False must raise without inserting."""
    crud.create_first_or_regular_user("first", auth.hash_password("password-123"))
    with pytest.raises(crud.SignupClosedError):
        crud.create_first_or_regular_user(
            "second", auth.hash_password("password-123"), allow_regular=False
        )
    assert crud.get_user_by_username("second") is None


def test_concurrent_anonymous_first_registrations_only_one_succeeds(client):
    """Two anonymous signups racing on an empty DB: the winner becomes the
    bootstrap admin (201), the loser is rejected with 401 because the
    in-transaction count shows signup already closed."""
    n = 2
    barrier = threading.Barrier(n)
    outcomes: list = [None] * n

    def worker(i):
        anon = TestClient(app)
        try:
            barrier.wait()
            outcomes[i] = anon.post(
                "/api/auth/register",
                json={"username": f"racer{i}", "password": "password-123"},
            ).status_code
        except Exception as exc:  # pragma: no cover
            outcomes[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not any(isinstance(o, Exception) for o in outcomes), outcomes
    assert sorted(outcomes) == [201, 401], outcomes
    # exactly one account exists, and it is the admin
    winners = []
    for i in range(n):
        c = TestClient(app)
        res = c.post(
            "/api/auth/login",
            json={"username": f"racer{i}", "password": "password-123"},
        )
        if res.status_code == 200:
            winners.append(res.json()["is_admin"])
    assert winners == [True]


# ---------------------------------------------------------------- register via API token (Bearer)

def _mint_token(client, name="ci"):
    res = client.post("/api/auth/tokens", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["token"]


def test_admin_bearer_token_can_register(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    token = _mint_token(client)
    anon = TestClient(app)
    res = anon.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password-123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    assert res.json()["username"] == "bob"
    assert res.json()["is_admin"] is False
    # the admin's own identity/session is unchanged, and the new user did not
    # steal the caller's cookie (admin-created accounts don't auto-login)
    assert client.get("/api/auth/me").json()["username"] == "admin"
    assert auth.SESSION_COOKIE not in anon.cookies


def test_non_admin_bearer_token_cannot_register(admin_client, user_client):
    token = _mint_token(user_client, name="alice-ci")
    anon = TestClient(app)
    res = anon.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password-123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


def test_invalid_bearer_does_not_fall_back_to_cookie(admin_client):
    # A Bearer header, once present, is the only credential considered: even
    # with a valid admin session cookie on the same request, an invalid token
    # must be rejected rather than silently authorized by the cookie.
    res = admin_client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password-123"},
        headers={"Authorization": "Bearer pdt_invalid"},
    )
    assert res.status_code == 401


# ---------------------------------------------------------------- signup gating

def test_register_open_only_while_empty(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    # fresh client, no session: gated
    anon = TestClient(app)
    res = anon.post("/api/auth/register", json={"username": "bob", "password": "password-123"})
    assert res.status_code == 401  # unauthenticated
    # authenticated non-admin: still gated
    res = client.post("/api/auth/register", json={"username": "bob", "password": "password-123"})
    # admin client can create accounts (admin gets is_admin False)
    assert res.status_code == 201
    assert res.json()["is_admin"] is False


def test_non_admin_cannot_register_users(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    # create alice via admin, then log in as alice in a separate client
    client.post("/api/auth/register", json={"username": "alice", "password": "password-123"})
    alice = TestClient(app)
    assert alice.post("/api/auth/login", json={"username": "alice", "password": "password-123"}).status_code == 200
    res = alice.post("/api/auth/register", json={"username": "bob", "password": "password-123"})
    assert res.status_code == 403


def test_open_signup_env_reopens_register(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_ENABLE_SIGNUP", "1")
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    anon = TestClient(app)
    res = anon.post("/api/auth/register", json={"username": "bob", "password": "password-123"})
    assert res.status_code == 201
    assert res.json()["is_admin"] is False  # open signup never creates admins


def test_auth_config_flags(client):
    assert client.get("/api/auth/config").json() == {"bootstrap": True, "signup_open": False}
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    assert client.get("/api/auth/config").json() == {"bootstrap": False, "signup_open": False}


# ---------------------------------------------------------------- duplicate usernames

def test_duplicate_username_409(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    res = client.post("/api/auth/register", json={"username": "admin", "password": "other-password-1"})
    assert res.status_code == 409


def test_duplicate_username_case_insensitive(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    res = client.post("/api/auth/register", json={"username": "ADMIN", "password": "other-password-1"})
    assert res.status_code == 409


# ---------------------------------------------------------------- login / logout

def test_login_success_sets_session(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    fresh = TestClient(app)
    assert fresh.get("/api/auth/me").status_code == 401
    res = fresh.post("/api/auth/login", json={"username": "admin", "password": "password-123"})
    assert res.status_code == 200
    assert res.json()["username"] == "admin"
    assert fresh.get("/api/auth/me").status_code == 200


def test_login_wrong_password_401(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    res = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password-1"})
    assert res.status_code == 401


def test_login_unknown_user_401(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    assert client.post("/api/auth/login", json={"username": "ghost", "password": "whatever-123"}).status_code == 401


def test_login_username_case_insensitive(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    assert client.post("/api/auth/login", json={"username": "Admin", "password": "password-123"}).status_code == 200


def test_logout_clears_session(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_logout_requires_auth(client):
    assert client.post("/api/auth/logout").status_code == 401


def test_tampered_session_cookie_rejected(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    client.cookies.set(auth.SESSION_COOKIE, "forged-value")
    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------- rate limiting

def test_login_rate_limited_after_repeated_failures(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    for _ in range(auth._LOGIN_MAX_FAILURES):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password-1"}).status_code == 401
    # even the correct password is now rejected for a while
    res = client.post("/api/auth/login", json={"username": "admin", "password": "password-123"})
    assert res.status_code == 429


def test_successful_login_resets_failure_counter(client):
    client.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    for _ in range(auth._LOGIN_MAX_FAILURES - 1):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password-1"})
    assert client.post("/api/auth/login", json={"username": "admin", "password": "password-123"}).status_code == 200
    # counter reset: a few more failures should not trip the limiter yet
    for _ in range(auth._LOGIN_MAX_FAILURES - 1):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password-1"}).status_code == 401


def test_failure_map_stays_bounded_under_unique_username_spray(monkeypatch):
    """A spray of unique usernames (each with a fresh, un-aged failure) must not
    grow the tracking map past the cap: aging-out reclaims nothing here, so the
    oldest-inserted eviction is what has to hold the bound."""
    monkeypatch.setattr(auth, "_LOGIN_MAX_TRACKED", 8)
    auth.reset_login_rate_limits()
    for i in range(auth._LOGIN_MAX_TRACKED * 4):
        auth._record_login_failure(f"attacker-{i}")
        assert len(auth._login_failures) <= auth._LOGIN_MAX_TRACKED
    # The most recently sprayed usernames survive; the oldest were evicted.
    assert len(auth._login_failures) == auth._LOGIN_MAX_TRACKED
    assert "attacker-0" not in auth._login_failures
    auth.reset_login_rate_limits()


# ---------------------------------------------------------------- migration

def test_existing_db_gains_owner_column_and_backfills(tmp_path, monkeypatch):
    """A pre-multi-user database (no users table, no owner_id) migrates on init
    and its projects are backfilled to the bootstrap admin on first register."""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        """CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'Other',
            status TEXT NOT NULL DEFAULT 'idea',
            priority TEXT NOT NULL DEFAULT 'medium',
            tags TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute("INSERT INTO projects (title) VALUES ('Legacy idea')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("DASHBOARD_DB", str(db_file))
    monkeypatch.setenv("DASHBOARD_SECRET", "test-secret")
    db.init_db()

    cols = {r[1] for r in sqlite3.connect(db_file).execute("PRAGMA table_info(projects)")}
    assert "owner_id" in cols
    assert "updated_by" in cols

    with TestClient(app) as c:
        assert c.get("/api/projects").status_code == 401
        res = c.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
        assert res.status_code == 201
        projects = c.get("/api/projects").json()
        assert len(projects) == 1
        assert projects[0]["owner_id"] == res.json()["id"]
        assert projects[0]["owner_username"] == "admin"
        assert projects[0]["updated_by"] == res.json()["id"]
        assert projects[0]["updated_by_username"] == "admin"


def test_foreign_keys_enforced(tmp_path, monkeypatch):
    db_file = tmp_path / "fk.db"
    monkeypatch.setenv("DASHBOARD_DB", str(db_file))
    monkeypatch.setenv("DASHBOARD_SECRET", "test-secret")
    db.init_db()
    with TestClient(app) as c:
        c.post("/api/auth/register", json={"username": "admin", "password": "password-123"})
    # Deleting a user with owned projects must fail the FK constraint
    with db.get_connection() as conn:
        conn.execute("DELETE FROM projects")  # clear projects first
    with db.get_connection() as conn:
        try:
            conn.execute("INSERT INTO projects (title, owner_id) VALUES ('orphan', 999)")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("FK on projects.owner_id was not enforced")
