"""API tests: CRUD round-trip, validation, 404s, filters, stats, health, authz."""


# ---------------------------------------------------------------- health

def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


# ---------------------------------------------------------------- auth required

def test_unauthenticated_project_routes_401(client):
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/projects", json={"title": "x"}).status_code == 401
    assert client.get("/api/projects/1").status_code == 401
    assert client.put("/api/projects/1", json={"title": "x"}).status_code == 401
    assert client.delete("/api/projects/1").status_code == 401
    assert client.get("/api/stats").status_code == 401


# ---------------------------------------------------------------- create / read

def test_create_returns_full_project(admin_client):
    res = admin_client.post("/api/projects", json={"title": "My idea"})
    assert res.status_code == 201
    data = res.json()
    assert data["title"] == "My idea"
    assert data["id"] >= 1
    # Defaults applied
    assert data["description"] == ""
    assert data["category"] == "Other"
    assert data["status"] == "idea"
    assert data["priority"] == "medium"
    assert data["tags"] == ""
    assert data["created_at"] and data["updated_at"]
    # Ownership is server-managed: set to the current user
    me = admin_client.get("/api/auth/me").json()
    assert data["owner_id"] == me["id"]
    assert data["owner_username"] == "admin"
    assert data["updated_by"] == me["id"]
    assert data["updated_by_username"] == "admin"


def test_create_and_get_round_trip(admin_client, sample_project):
    res = admin_client.get(f"/api/projects/{sample_project['id']}")
    assert res.status_code == 200
    assert res.json() == sample_project


def test_list_returns_newest_first(admin_client):
    admin_client.post("/api/projects", json={"title": "first"})
    admin_client.post("/api/projects", json={"title": "second"})
    res = admin_client.get("/api/projects")
    assert res.status_code == 200
    titles = [p["title"] for p in res.json()]
    assert titles == ["second", "first"]


def test_list_is_shared_workspace(admin_client, user_client, sample_project):
    # alice (non-owner) can still read admin's project
    res = user_client.get(f"/api/projects/{sample_project['id']}")
    assert res.status_code == 200
    assert res.json()["owner_username"] == "admin"
    assert user_client.get("/api/projects").json()  # sees all projects


# ---------------------------------------------------------------- update / delete

def test_update_partial(admin_client, sample_project):
    res = admin_client.put(f"/api/projects/{sample_project['id']}", json={"status": "in_progress"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "in_progress"
    # Untouched fields preserved
    assert data["title"] == sample_project["title"]
    assert data["priority"] == "high"


def test_update_full(admin_client, sample_project):
    res = admin_client.put(
        f"/api/projects/{sample_project['id']}",
        json={
            "title": "Renamed",
            "description": "New plan",
            "category": "Local AI",
            "status": "done",
            "priority": "low",
            "tags": "a, b",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "Renamed"
    assert data["description"] == "New plan"
    assert data["category"] == "Local AI"
    assert data["status"] == "done"
    assert data["priority"] == "low"
    assert data["tags"] == "a, b"


def test_update_empty_body_is_noop(admin_client, sample_project):
    res = admin_client.put(f"/api/projects/{sample_project['id']}", json={})
    assert res.status_code == 200
    assert res.json() == sample_project


def test_delete(admin_client, sample_project):
    res = admin_client.delete(f"/api/projects/{sample_project['id']}")
    assert res.status_code == 204
    assert admin_client.get(f"/api/projects/{sample_project['id']}").status_code == 404
    assert admin_client.get("/api/projects").json() == []


# ---------------------------------------------------------------- ownership (403)

def test_owner_can_update_and_delete(admin_client, user_client):
    # alice owns her project
    res = user_client.post("/api/projects", json={"title": "Alice's idea"})
    assert res.status_code == 201
    pid = res.json()["id"]
    assert res.json()["owner_username"] == "alice"
    assert user_client.put(f"/api/projects/{pid}", json={"status": "done"}).status_code == 200
    assert user_client.delete(f"/api/projects/{pid}").status_code == 204


def test_non_owner_update_403(admin_client, user_client, sample_project):
    res = user_client.put(f"/api/projects/{sample_project['id']}", json={"status": "done"})
    assert res.status_code == 403


def test_non_owner_delete_403(admin_client, user_client, sample_project):
    res = user_client.delete(f"/api/projects/{sample_project['id']}")
    assert res.status_code == 403
    # still there
    assert admin_client.get(f"/api/projects/{sample_project['id']}").status_code == 200


def test_updated_by_records_last_editor(admin_client, user_client):
    res = user_client.post("/api/projects", json={"title": "Alice's idea"})
    pid = res.json()["id"]
    assert res.json()["updated_by_username"] == "alice"
    # admin edits it → updated_by follows the editor, owner stays alice
    res = admin_client.put(f"/api/projects/{pid}", json={"status": "in_progress"})
    assert res.status_code == 200
    assert res.json()["updated_by_username"] == "admin"
    assert res.json()["owner_username"] == "alice"


def test_admin_can_update_and_delete_any(admin_client, user_client):
    res = user_client.post("/api/projects", json={"title": "Alice's idea"})
    pid = res.json()["id"]
    assert admin_client.put(f"/api/projects/{pid}", json={"status": "paused"}).status_code == 200
    assert admin_client.delete(f"/api/projects/{pid}").status_code == 204


def test_missing_project_404_before_403(user_client):
    # 404 (missing) takes precedence over 403 (not owner)
    assert user_client.put("/api/projects/99999", json={"title": "ghost"}).status_code == 404
    assert user_client.delete("/api/projects/99999").status_code == 404


# ---------------------------------------------------------------- validation

def test_create_missing_title_rejected(admin_client):
    res = admin_client.post("/api/projects", json={"description": "no title"})
    assert res.status_code == 422


def test_create_empty_title_rejected(admin_client):
    res = admin_client.post("/api/projects", json={"title": "   "})
    assert res.status_code == 422


def test_create_invalid_status_rejected(admin_client):
    res = admin_client.post("/api/projects", json={"title": "x", "status": "nonsense"})
    assert res.status_code == 422


def test_create_invalid_priority_rejected(admin_client):
    res = admin_client.post("/api/projects", json={"title": "x", "priority": "urgent"})
    assert res.status_code == 422


def test_update_invalid_status_rejected(admin_client, sample_project):
    res = admin_client.put(f"/api/projects/{sample_project['id']}", json={"status": "bogus"})
    assert res.status_code == 422


def test_update_invalid_priority_rejected(admin_client, sample_project):
    res = admin_client.put(f"/api/projects/{sample_project['id']}", json={"priority": "max"})
    assert res.status_code == 422


def test_update_missing_title_rejected(admin_client, sample_project):
    res = admin_client.put(f"/api/projects/{sample_project['id']}", json={"title": ""})
    assert res.status_code == 422


# ---------------------------------------------------------------- 404s

def test_get_missing_id_404(admin_client):
    assert admin_client.get("/api/projects/99999").status_code == 404


def test_update_missing_id_404(admin_client):
    res = admin_client.put("/api/projects/99999", json={"title": "ghost"})
    assert res.status_code == 404


def test_delete_missing_id_404(admin_client):
    assert admin_client.delete("/api/projects/99999").status_code == 404


# ---------------------------------------------------------------- filters

def _seed(client):
    client.post("/api/projects", json={
        "title": "Local LLM server", "description": "ollama on a mini pc",
        "category": "Local AI", "status": "in_progress", "priority": "high",
        "tags": "llm, ollama",
    })
    client.post("/api/projects", json={
        "title": "Retro RTS game", "description": "isometric grid rts",
        "category": "Software", "status": "planning", "priority": "medium",
        "tags": "game, rts",
    })
    client.post("/api/projects", json={
        "title": "Home lab rack", "description": "10u rack for homelab",
        "category": "Other", "status": "idea", "priority": "low",
        "tags": "homelab",
    })


def test_filter_by_status(admin_client):
    _seed(admin_client)
    res = admin_client.get("/api/projects", params={"status": "in_progress"})
    assert res.status_code == 200
    titles = [p["title"] for p in res.json()]
    assert titles == ["Local LLM server"]


def test_filter_by_category(admin_client):
    _seed(admin_client)
    res = admin_client.get("/api/projects", params={"category": "Software"})
    assert [p["title"] for p in res.json()] == ["Retro RTS game"]


def test_filter_by_search(admin_client):
    _seed(admin_client)
    # matches title
    assert [p["title"] for p in admin_client.get("/api/projects", params={"q": "RTS"}).json()] == ["Retro RTS game"]
    # matches description
    assert [p["title"] for p in admin_client.get("/api/projects", params={"q": "ollama"}).json()] == ["Local LLM server"]
    # matches tags
    assert [p["title"] for p in admin_client.get("/api/projects", params={"q": "homelab"}).json()] == ["Home lab rack"]
    # no match
    assert admin_client.get("/api/projects", params={"q": "zzz-nope"}).json() == []


def test_combined_filters(admin_client):
    _seed(admin_client)
    res = admin_client.get("/api/projects", params={"status": "planning", "q": "isometric"})
    assert [p["title"] for p in res.json()] == ["Retro RTS game"]
    res = admin_client.get("/api/projects", params={"status": "planning", "q": "ollama"})
    assert res.json() == []


def test_invalid_status_filter_400(admin_client):
    assert admin_client.get("/api/projects", params={"status": "bogus"}).status_code == 400


# ---------------------------------------------------------------- stats

def test_stats_counts(admin_client):
    _seed(admin_client)
    res = admin_client.get("/api/stats")
    assert res.status_code == 200
    assert res.json() == {
        "idea": 1,
        "planning": 1,
        "in_progress": 1,
        "paused": 0,
        "done": 0,
        "by_owner": {"admin": 3},
    }


def test_stats_empty_db(admin_client):
    res = admin_client.get("/api/stats")
    data = res.json()
    assert {k: v for k, v in data.items() if k != "by_owner"} == {
        s: 0 for s in ("idea", "planning", "in_progress", "paused", "done")
    }
    assert data["by_owner"] == {}


def test_stats_by_owner_per_user(admin_client, user_client):
    admin_client.post("/api/projects", json={"title": "admin one"})
    admin_client.post("/api/projects", json={"title": "admin two"})
    user_client.post("/api/projects", json={"title": "alice one"})
    data = admin_client.get("/api/stats").json()
    assert data["by_owner"] == {"admin": 2, "alice": 1}


def test_stats_reflect_deletion(admin_client, sample_project):
    before = admin_client.get("/api/stats").json()
    admin_client.delete(f"/api/projects/{sample_project['id']}")
    after = admin_client.get("/api/stats").json()
    assert after["planning"] == before["planning"] - 1
    assert after["by_owner"].get("admin", 0) == before["by_owner"]["admin"] - 1


# ---------------------------------------------------------------- static frontend

def test_index_served_at_root(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Project Dashboard" in res.text
    assert res.headers["content-type"].startswith("text/html")


def test_static_js_and_css_served(client):
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
