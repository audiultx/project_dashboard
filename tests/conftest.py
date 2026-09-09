"""Shared fixtures: TestClient backed by a temporary SQLite database."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make the project root importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with the app pointed at a fresh temp database."""
    db_file = tmp_path / "test_dashboard.db"
    monkeypatch.setenv("DASHBOARD_DB", str(db_file))
    db.init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def sample_project(client):
    """Create one project and return its JSON."""
    res = client.post(
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
