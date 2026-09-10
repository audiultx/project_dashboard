"""API token tests: mint, Bearer auth, permissions, revocation, expiry, listing."""

import hashlib
import os
import sqlite3

from fastapi.testclient import TestClient

from app.main import app


def _mint(client, name="ci-token", **extra):
    res = client.post("/api/auth/tokens", json={"name": name, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _register_third_user(admin_client, username="bob", password="bob-password-1"):
    res = admin_client.post(
        "/api/auth/register", json={"username": username, "password": password, "display_name": username}
    )
    assert res.status_code == 201, res.text
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return c


# ---------------------------------------------------------------- creation

def test_create_token_returns_plaintext_once(admin_client):
    data = _mint(admin_client, name="ci")
    assert data["token"].startswith("pdt_")
    assert data["user_id"] == 1
    assert data["owner_username"] == "admin"
    assert data["last_used_at"] is None
    assert data["expires_at"] is None
    assert data["revoked_at"] is None
    # The list response never repeats the plaintext
    listing = admin_client.get("/api/auth/tokens").json()
    assert len(listing) == 1
    assert listing[0]["id"] == data["id"]
    assert "token" not in listing[0]
    assert listing[0]["name"] == "ci"


def test_create_token_stores_only_hash(admin_client):
    data = _mint(admin_client)
    conn = sqlite3.connect(os.environ["DASHBOARD_DB"])
    row = conn.execute("SELECT token_hash FROM api_tokens WHERE id = ?", (data["id"],)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == hashlib.sha256(data["token"].encode("utf-8")).hexdigest()


def test_create_token_unauthenticated_401(client):
    assert client.post("/api/auth/tokens", json={"name": "x"}).status_code == 401
    assert client.get("/api/auth/tokens").status_code == 401


def test_create_token_validation(admin_client):
    assert admin_client.post("/api/auth/tokens", json={"name": "   "}).status_code == 422
    assert admin_client.post("/api/auth/tokens", json={}).status_code == 422
    assert admin_client.post("/api/auth/tokens", json={"name": "ok", "expires_at": "not-a-date"}).status_code == 422


# ---------------------------------------------------------------- bearer auth

def test_bearer_authenticates_without_cookie(admin_client):
    data = _mint(admin_client, name="bearer")
    anon = TestClient(app)
    assert anon.get("/api/projects", headers=_bearer(data["token"])).status_code == 200
    res = anon.post(
        "/api/projects",
        json={"title": "Bearer project", "description": "created with a token"},
        headers=_bearer(data["token"]),
    )
    assert res.status_code == 201
    assert res.json()["owner_username"] == "admin"


def test_invalid_bearer_401(admin_client):
    anon = TestClient(app)
    assert anon.get("/api/projects", headers=_bearer("pdt_garbage")).status_code == 401
    assert anon.get("/api/projects", headers={"Authorization": "Bearer"}).status_code == 401
    assert anon.get("/api/projects", headers={"Authorization": "Basic pdt_garbage"}).status_code == 401


def test_bearer_takes_precedence_and_invalid_bearer_401(admin_client, user_client):
    alice = _mint(user_client, name="alice-token")
    admin_tok = _mint(admin_client, name="admin-token")
    anon = TestClient(app)
    # Bearer header present but invalid: cookie (if any) must not rescue it
    res = anon.get("/api/projects", headers=_bearer("pdt_invalid"))
    assert res.status_code == 401
    # Valid Bearer from alice works even on a fresh client
    assert anon.get("/api/projects", headers=_bearer(alice["token"])).status_code == 200


def test_bearer_inherits_creator_permissions(admin_client, user_client, sample_project):
    alice = _mint(user_client, name="alice-token")
    admin_tok = _mint(admin_client, name="admin-token")
    anon = TestClient(app)
    # Non-admin token gets 403 on someone else's project
    res = anon.put(
        f"/api/projects/{sample_project['id']}", json={"title": "hax"}, headers=_bearer(alice["token"])
    )
    assert res.status_code == 403
    # Admin token can write any project
    res = anon.put(
        f"/api/projects/{sample_project['id']}", json={"title": "Renamed"}, headers=_bearer(admin_tok["token"])
    )
    assert res.status_code == 200
    assert res.json()["updated_by_username"] == "admin"
    # Non-admin token can delete its own projects
    created = anon.post(
        "/api/projects", json={"title": "alice's project"}, headers=_bearer(alice["token"])
    ).json()
    assert anon.delete(f"/api/projects/{created['id']}", headers=_bearer(alice["token"])).status_code == 204


def test_token_last_used_updated(admin_client):
    data = _mint(admin_client, name="tracked")
    anon = TestClient(app)
    anon.get("/api/projects", headers=_bearer(data["token"]))
    listing = admin_client.get("/api/auth/tokens").json()
    assert listing[0]["last_used_at"] is not None


# ---------------------------------------------------------------- revocation

def test_revoked_token_401(admin_client):
    data = _mint(admin_client, name="doomed")
    anon = TestClient(app)
    assert anon.get("/api/projects", headers=_bearer(data["token"])).status_code == 200
    assert admin_client.delete(f"/api/auth/tokens/{data['id']}").status_code == 204
    assert anon.get("/api/projects", headers=_bearer(data["token"])).status_code == 401
    # Revoking a revoked token is 404
    assert admin_client.delete(f"/api/auth/tokens/{data['id']}").status_code == 404


def test_revoke_owner_or_admin(admin_client, user_client):
    alice = _mint(user_client, name="alice-a")
    bob = _register_third_user(admin_client)
    # Non-owner, non-admin: 403
    assert bob.delete(f"/api/auth/tokens/{alice['id']}").status_code == 403
    # Unknown id: 404
    assert user_client.delete("/api/auth/tokens/99999").status_code == 404
    # Owner revokes own
    assert user_client.delete(f"/api/auth/tokens/{alice['id']}").status_code == 204


def test_admin_can_revoke_anyones(admin_client, user_client):
    alice = _mint(user_client, name="alice-a")
    assert admin_client.delete(f"/api/auth/tokens/{alice['id']}").status_code == 204
    anon = TestClient(app)
    assert anon.get("/api/projects", headers=_bearer(alice["token"])).status_code == 401


# ---------------------------------------------------------------- listing

def test_list_only_own_tokens(admin_client, user_client):
    _mint(admin_client, name="admin-a")
    _mint(admin_client, name="admin-b")
    _mint(user_client, name="alice-a")
    listing = user_client.get("/api/auth/tokens").json()
    assert [t["name"] for t in listing] == ["alice-a"]
    assert listing[0]["owner_username"] == "alice"


def test_list_all_admin_only(admin_client, user_client):
    _mint(admin_client, name="admin-a")
    _mint(user_client, name="alice-a")
    assert user_client.get("/api/auth/tokens?all=1").status_code == 403
    listing = admin_client.get("/api/auth/tokens?all=1").json()
    assert {t["name"] for t in listing} == {"admin-a", "alice-a"}
    assert {t["owner_username"] for t in listing} == {"admin", "alice"}


# ---------------------------------------------------------------- expiry

def test_expired_token_401(admin_client):
    data = _mint(admin_client, name="old", expires_at="2000-01-01T00:00:00Z")
    anon = TestClient(app)
    assert anon.get("/api/projects", headers=_bearer(data["token"])).status_code == 401
    listing = admin_client.get("/api/auth/tokens").json()
    assert listing[0]["expires_at"] == "2000-01-01 00:00:00"


def test_future_expiry_kept(admin_client):
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    data = _mint(admin_client, name="future", expires_at=future)
    anon = TestClient(app)
    assert anon.get("/api/projects", headers=_bearer(data["token"])).status_code == 200
    assert data["expires_at"] is not None
    # Naive datetimes are treated as UTC
    data2 = _mint(admin_client, name="naive", expires_at="2999-01-01 00:00:00")
    assert anon.get("/api/projects", headers=_bearer(data2["token"])).status_code == 200
