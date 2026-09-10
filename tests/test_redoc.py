"""Self-hosted ReDoc tests: docs page, vendored bundle, OpenAPI completeness."""

import os
from pathlib import Path

import pytest

from app.main import STATIC_DIR


# ---------------------------------------------------------------- docs page

def test_redoc_page_serves_local_html(client):
    res = client.get("/redoc")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "<redoc" in res.text
    assert 'spec-url="/openapi.json"' in res.text
    assert "redoc.standalone.js" in res.text


def test_redoc_page_has_no_remote_references(client):
    # Self-host guarantee: the page must not reference any CDN or remote URL.
    res = client.get("/redoc")
    assert res.status_code == 200
    assert "http://" not in res.text
    assert "https://" not in res.text


def test_redoc_is_not_shadowed_by_static_mount(client):
    # The static mount is registered last with html=True, so /redoc must hit the
    # explicit route and return the ReDoc HTML, not index.html.
    redoc_res = client.get("/redoc")
    root_res = client.get("/")
    assert redoc_res.status_code == 200
    assert root_res.status_code == 200
    assert "<redoc" in redoc_res.text
    assert "<redoc" not in root_res.text
    assert redoc_res.text != root_res.text


# ---------------------------------------------------------------- vendored bundle

def test_redoc_bundle_is_served(client):
    res = client.get("/redoc.standalone.js")
    assert res.status_code == 200
    assert "javascript" in res.headers["content-type"]
    # Non-trivial body: the pinned 2.5.4 bundle is ~1 MB minified.
    assert len(res.content) > 100_000


def test_redoc_bundle_missing_returns_404(client, tmp_path):
    # Adversarial: if the vendored JS is missing, the static mount must 404 so
    # the deployment problem is obvious (rather than silently serving nothing).
    bundle_path = Path(STATIC_DIR) / "redoc.standalone.js"
    if not bundle_path.exists():
        pytest.skip("vendored bundle not present in this environment")
    moved = tmp_path / "redoc.standalone.js.bak"
    try:
        os.rename(bundle_path, moved)
        res = client.get("/redoc.standalone.js")
        assert res.status_code == 404
    finally:
        os.rename(moved, bundle_path)


# ---------------------------------------------------------------- openapi completeness

def test_redoc_absent_from_openapi_paths(client):
    res = client.get("/openapi.json")
    assert res.status_code == 200
    schema = res.json()
    assert "/redoc" not in schema["paths"]


def test_openapi_operations_have_summaries(client):
    res = client.get("/openapi.json")
    assert res.status_code == 200
    schema = res.json()
    missing = [
        f"{method.upper()} {path}"
        for path, ops in schema["paths"].items()
        for method, op in ops.items()
        if not (isinstance(op, dict) and op.get("summary"))
    ]
    assert not missing, f"Operations without a summary: {missing}"


def test_openapi_protected_paths_document_401(client):
    res = client.get("/openapi.json")
    schema = res.json()
    public = {"/api/health", "/api/auth/config", "/api/auth/login", "/api/auth/register"}
    protected = [p for p in schema["paths"] if p not in public]
    assert protected, "sanity: protected paths expected"
    for path in protected:
        for method, op in schema["paths"][path].items():
            assert "401" in op["responses"], f"missing 401 response on {method.upper()} {path}"


def test_openapi_components_include_new_schemas(client):
    res = client.get("/openapi.json")
    schema = res.json()
    names = set(schema.get("components", {}).get("schemas", {}))
    for expected in ("HealthOut", "AuthConfigOut", "StatsOut", "ErrorOut"):
        assert expected in names, f"missing schema {expected}"
