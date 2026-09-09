# AGENTS.md

Guidance for AI coding agents working in this repository. Human docs live in
[README.md](README.md); this file is the operational contract for agents.

## What this is

A small, single-user, self-hosted **project-idea tracker**. FastAPI backend +
a static vanilla-JS frontend, backed by a single SQLite file. No auth, no
users table, one container, one data volume. Keep it simple — resist adding
frameworks, ORMs, build steps, or a database server.

## Tech stack

- **Python 3.12+**, [FastAPI](https://fastapi.tiangolo.com/) + `uvicorn[standard]`
- **SQLite** via the stdlib `sqlite3` module (no ORM)
- **Pydantic v2** for request/response schemas and validation
- Frontend: plain `index.html` + `app.js` + `style.css` (no bundler, no npm)
- Tests: `pytest` + FastAPI's `TestClient` (needs `httpx`)

## Layout

```
app/
  main.py     # FastAPI app, API routes, static mount (routes registered before "/" mount)
  db.py       # sqlite3 connection ctx manager + schema bootstrap (init_db)
  models.py   # pydantic schemas + STATUSES/PRIORITIES tuples + validators
  crud.py     # all SQL / data access lives here
  static/     # index.html, app.js, style.css — the frontend
tests/        # pytest suite (conftest.py points the app at a temp DB)
data/         # SQLite file (gitignored; Docker volume mount point at /data)
Dockerfile
docker-compose.yml
```

## Setup & commands

```bash
# Install (runtime + dev deps)
pip install -r requirements-dev.txt

# Run locally
uvicorn app.main:app --host 0.0.0.0 --port 8000   # -> http://localhost:8000

# Run the test suite (do this before declaring any change done)
pytest

# Docker
docker compose up -d --build                      # host 8330 -> container 8000
```

There is no linter/formatter config committed. Match the existing style rather
than reformatting files wholesale.

## API surface

All API routes are prefixed `/api`; the static frontend is mounted at `/`.
FastAPI auto-serves `/docs`, `/redoc`, and `/openapi.json`.

| Method | Path                 | Notes                                        |
|--------|----------------------|----------------------------------------------|
| GET    | `/api/health`        | `{"status": "ok"}`                           |
| GET    | `/api/projects`      | Filters: `?status=`, `?category=`, `?q=`     |
| POST   | `/api/projects`      | 201; body = `ProjectCreate`                  |
| GET    | `/api/projects/{id}` | 404 if missing                               |
| PUT    | `/api/projects/{id}` | Partial update (`exclude_unset`); 404 if missing |
| DELETE | `/api/projects/{id}` | 204; 404 if missing                          |
| GET    | `/api/stats`         | Count per status (all statuses zero-filled)  |

## Data model & invariants

- A project has: `title` (required, non-blank, ≤200 chars), `description`,
  `category`, `status`, `priority`, `tags` (free-text CSV string), plus
  server-managed `id`, `created_at`, `updated_at`.
- **`status` ∈ `idea, planning, in_progress, paused, done`**
- **`priority` ∈ `low, medium, high`**
- These enums are enforced in **three** places that must stay in sync:
  1. `STATUSES` / `PRIORITIES` tuples in [app/models.py](app/models.py)
  2. Pydantic `field_validator`s in the same file (return 422 on bad input)
  3. `CHECK` constraints in the `SCHEMA` string in [app/db.py](app/db.py)

  If you add/rename a status or priority, update **all three** (and the
  frontend `app.js`/`index.html`, and `crud.stats()` relies on `STATUSES`).

## Conventions & guardrails

- **All SQL stays in [app/crud.py](app/crud.py).** Routes in `main.py` should
  call crud functions, not touch the DB directly.
- **Always use parameterized queries** (`?` placeholders) — never f-string user
  input into SQL. The existing code does this; keep it that way.
- Get connections via the `db.get_connection()` context manager, which handles
  commit/rollback/close. Don't open raw `sqlite3.connect` elsewhere.
- The DB path is resolved lazily from the `DASHBOARD_DB` env var at call time —
  do not cache it at import time, since tests rely on overriding it.
- New API routes must be registered **before** the `app.mount("/", ...)` static
  mount in `main.py`, or they'll be shadowed by the frontend.
- Return 404 via `HTTPException` for missing resources; let Pydantic produce
  422 for validation failures (don't hand-roll validation in routes).

## Testing expectations

- Run `pytest` and keep it green before finishing.
- `tests/conftest.py` points the app at a temporary SQLite DB — never write
  against the real `data/dashboard.db` in tests.
- When adding an endpoint or changing behavior, add/adjust tests in
  `tests/test_api.py`. Cover the happy path plus validation (422) and 404s,
  matching the existing test patterns.

## Things to be careful about

- **No auth exists.** Every endpoint is open. Don't assume a request is
  trusted, and flag it if asked to expose this beyond a private network.
- **CORS is not configured** — fine for Postman/curl and the same-origin
  frontend, but a cross-origin browser client needs `CORSMiddleware` added.
- Data is a single SQLite file on the `/data` volume; back up by copying
  `dashboard.db`. Don't introduce a migration framework — if the schema
  changes, evolve the `SCHEMA` string and note any manual migration needed.
- Keep runtime dependencies minimal (`requirements.txt`). Prefer the stdlib.
