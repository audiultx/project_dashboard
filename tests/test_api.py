"""API tests: CRUD round-trip, validation, 404s, filters, stats, health."""


# ---------------------------------------------------------------- health

def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


# ---------------------------------------------------------------- create / read

def test_create_returns_full_project(client):
    res = client.post("/api/projects", json={"title": "My idea"})
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


def test_create_and_get_round_trip(client, sample_project):
    res = client.get(f"/api/projects/{sample_project['id']}")
    assert res.status_code == 200
    assert res.json() == sample_project


def test_list_returns_newest_first(client):
    client.post("/api/projects", json={"title": "first"})
    client.post("/api/projects", json={"title": "second"})
    res = client.get("/api/projects")
    assert res.status_code == 200
    titles = [p["title"] for p in res.json()]
    assert titles == ["second", "first"]


# ---------------------------------------------------------------- update / delete

def test_update_partial(client, sample_project):
    res = client.put(f"/api/projects/{sample_project['id']}", json={"status": "in_progress"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "in_progress"
    # Untouched fields preserved
    assert data["title"] == sample_project["title"]
    assert data["priority"] == "high"


def test_update_full(client, sample_project):
    res = client.put(
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


def test_update_empty_body_is_noop(client, sample_project):
    res = client.put(f"/api/projects/{sample_project['id']}", json={})
    assert res.status_code == 200
    assert res.json() == sample_project


def test_delete(client, sample_project):
    res = client.delete(f"/api/projects/{sample_project['id']}")
    assert res.status_code == 204
    assert client.get(f"/api/projects/{sample_project['id']}").status_code == 404
    assert client.get("/api/projects").json() == []


# ---------------------------------------------------------------- validation

def test_create_missing_title_rejected(client):
    res = client.post("/api/projects", json={"description": "no title"})
    assert res.status_code == 422


def test_create_empty_title_rejected(client):
    res = client.post("/api/projects", json={"title": "   "})
    assert res.status_code == 422


def test_create_invalid_status_rejected(client):
    res = client.post("/api/projects", json={"title": "x", "status": "nonsense"})
    assert res.status_code == 422


def test_create_invalid_priority_rejected(client):
    res = client.post("/api/projects", json={"title": "x", "priority": "urgent"})
    assert res.status_code == 422


def test_update_invalid_status_rejected(client, sample_project):
    res = client.put(f"/api/projects/{sample_project['id']}", json={"status": "bogus"})
    assert res.status_code == 422


def test_update_invalid_priority_rejected(client, sample_project):
    res = client.put(f"/api/projects/{sample_project['id']}", json={"priority": "max"})
    assert res.status_code == 422


def test_update_missing_title_rejected(client, sample_project):
    res = client.put(f"/api/projects/{sample_project['id']}", json={"title": ""})
    assert res.status_code == 422


# ---------------------------------------------------------------- 404s

def test_get_missing_id_404(client):
    assert client.get("/api/projects/99999").status_code == 404


def test_update_missing_id_404(client):
    res = client.put("/api/projects/99999", json={"title": "ghost"})
    assert res.status_code == 404


def test_delete_missing_id_404(client):
    assert client.delete("/api/projects/99999").status_code == 404


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


def test_filter_by_status(client):
    _seed(client)
    res = client.get("/api/projects", params={"status": "in_progress"})
    assert res.status_code == 200
    titles = [p["title"] for p in res.json()]
    assert titles == ["Local LLM server"]


def test_filter_by_category(client):
    _seed(client)
    res = client.get("/api/projects", params={"category": "Software"})
    assert [p["title"] for p in res.json()] == ["Retro RTS game"]


def test_filter_by_search(client):
    _seed(client)
    # matches title
    assert [p["title"] for p in client.get("/api/projects", params={"q": "RTS"}).json()] == ["Retro RTS game"]
    # matches description
    assert [p["title"] for p in client.get("/api/projects", params={"q": "ollama"}).json()] == ["Local LLM server"]
    # matches tags
    assert [p["title"] for p in client.get("/api/projects", params={"q": "homelab"}).json()] == ["Home lab rack"]
    # no match
    assert client.get("/api/projects", params={"q": "zzz-nope"}).json() == []


def test_combined_filters(client):
    _seed(client)
    res = client.get("/api/projects", params={"status": "planning", "q": "isometric"})
    assert [p["title"] for p in res.json()] == ["Retro RTS game"]
    res = client.get("/api/projects", params={"status": "planning", "q": "ollama"})
    assert res.json() == []


def test_invalid_status_filter_400(client):
    assert client.get("/api/projects", params={"status": "bogus"}).status_code == 400


# ---------------------------------------------------------------- stats

def test_stats_counts(client):
    _seed(client)
    res = client.get("/api/stats")
    assert res.status_code == 200
    assert res.json() == {
        "idea": 1,
        "planning": 1,
        "in_progress": 1,
        "paused": 0,
        "done": 0,
    }


def test_stats_empty_db(client):
    res = client.get("/api/stats")
    assert res.json() == {s: 0 for s in ("idea", "planning", "in_progress", "paused", "done")}


def test_stats_reflect_deletion(client, sample_project):
    before = client.get("/api/stats").json()
    client.delete(f"/api/projects/{sample_project['id']}")
    after = client.get("/api/stats").json()
    assert after["planning"] == before["planning"] - 1


# ---------------------------------------------------------------- static frontend

def test_index_served_at_root(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Project Dashboard" in res.text
    assert res.headers["content-type"].startswith("text/html")


def test_static_js_and_css_served(client):
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
