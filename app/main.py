"""FastAPI application: API routes + static frontend."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from . import crud, db
from .models import PRIORITIES, STATUSES, ProjectCreate, ProjectOut, ProjectUpdate

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Project Dashboard", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------- API routes

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects(
    status: str | None = Query(None),
    category: str | None = Query(None),
    q: str | None = Query(None, description="Text search over title/description/tags"),
) -> list[dict]:
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(STATUSES)}")
    return crud.list_projects(status=status, category=category, q=q)


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate) -> dict:
    return crud.create_project(payload.model_dump())


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int) -> dict:
    project = crud.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.put("/api/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectUpdate) -> dict:
    project = crud.update_project(project_id, payload.model_dump(exclude_unset=True))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: int) -> None:
    if not crud.delete_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


@app.get("/api/stats")
def stats() -> dict[str, int]:
    return crud.stats()


# ------------------------------------------------------------- static frontend
# Mounted last so /api/* routes take precedence; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
