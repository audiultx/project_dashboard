# Project Dashboard — Implementation Plan

## 1. Goal

A simple, private, self-hosted web app for writing down and tracking hobby project
ideas — both for the local AI setup and general software projects.

- Web-based dashboard, single user, no login required (runs locally).
- Docker-ready: one container, one data volume.
- Verified end-to-end: unit tests + running the actual Docker container.

## 2. Tech stack (and why)

| Layer      | Choice                          | Rationale |
|------------|---------------------------------|-----------|
| Backend    | Python 3.12 + FastAPI + uvicorn | Small, fast to build, built-in OpenAPI docs, trivial test client |
| Database   | SQLite (stdlib `sqlite3`)       | Zero-config, single file, perfect for one user; file lives on a Docker volume |
| Frontend   | Vanilla HTML/CSS/JS (no build)  | Keeps the image tiny, no Node toolchain, one page + one JS file |
| Packaging  | Dockerfile + docker-compose.yml | Slim image, non-root user, named volume for data, healthcheck |
| Tests      | pytest + FastAPI TestClient     | Covers API CRUD, validation, and filtering |

## 3. Features (v1 — deliberately small)

**Project idea** fields:
- `title` (required)
- `description` / plan text (free text, multi-line)
- `category` — e.g. `Local AI`, `Software`, `Other` (free text, kept simple)
- `status` — `idea` → `planning` → `in_progress` → `paused` → `done`
- `priority` — `low` / `medium` / `high`
- `tags` — comma-separated string
- `created_at` / `updated_at` — automatic

**Dashboard UI:**
- List of all projects as cards/rows, newest first
- Filters: status, category, text search
- Create + edit via modal form; delete with confirmation
- Click a project → detail view with full plan text
- Small stats strip: counts per status

**API:**
```
GET    /api/health                 → {"status": "ok"}
GET    /api/projects               → list (supports ?status= &category= &q=)
POST   /api/projects               → create
GET    /api/projects/{id}          → single
PUT    /api/projects/{id}          → update (partial or full)
DELETE /api/projects/{id}          → delete
GET    /api/stats                  → counts per status
```
Static frontend served at `/` (index.html + app.js + style.css).

## 4. Project structure

```
project_dashboard/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app, static mount, startup init
│   ├── db.py            # sqlite connection + schema bootstrap
│   ├── models.py        # pydantic request/response schemas
│   ├── crud.py          # data access (list/create/get/update/delete/stats)
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── style.css
├── tests/
│   ├── conftest.py      # temp-DB test client fixture
│   └── test_api.py      # CRUD, validation, filters, stats
├── data/                # sqlite db (gitignored; Docker volume mount point)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .dockerignore
├── .gitignore
├── README.md
└── PLAN.md
```

**DB schema (single table):**
```sql
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'Other',
    status      TEXT NOT NULL DEFAULT 'idea'
                CHECK (status IN ('idea','planning','in_progress','paused','done')),
    priority    TEXT NOT NULL DEFAULT 'medium'
                CHECK (priority IN ('low','medium','high')),
    tags        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## 5. Build phases

1. **Scaffold** — folder structure, `requirements.txt`, `db.py`, `models.py`.
2. **API** — `crud.py` + `main.py` with all endpoints, validation errors (400/404/422).
3. **Frontend** — single-page dashboard: list, filters, search, create/edit modal,
   detail view, delete, stats strip. Clean dark-ish minimal styling.
4. **Tests** — pytest suite covering: create/read/update/delete round-trip,
   invalid status/priority rejected, 404 on missing id, filters (status/category/q),
   stats counts, health endpoint.
5. **Docker** — `Dockerfile` (python:3.12-slim, non-root, `VOLUME /data`,
   `HEALTHCHECK`), `docker-compose.yml` (port 8000, named volume `dashboard_data`),
   `.dockerignore`.
6. **Verification**
   - `pytest` locally (all green).
   - `docker build -t project-dashboard .`
   - `docker compose up -d` (in this sandbox: daemon runs with `--bridge=none`,
     so the container is verified with host networking; on a normal machine the
     compose port mapping works as-is).
   - curl the container: health → create → list → update → delete → stats.
   - Restart container → confirm data persisted in the volume.
   - Open the UI in a browser and screenshot it.
7. **Docs** — README with local + Docker run instructions.

## 6. Known environment notes (this sandbox)

- Docker daemon had to be started manually: `dockerd --iptables=false --bridge=none`
  (no NET_ADMIN capability). Containers must use `--network=host` here.
- The compose file targets a normal Docker host (standard port mapping) — that is
  what will be shipped in the repo.

## 7. Out of scope (v1)

- Authentication / multi-user
- Markdown rendering, file attachments, images
- Backup/restore tooling (single SQLite file = trivially backed up)
- Mobile-specific layout (responsive enough, but not a focus)
