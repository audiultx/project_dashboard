# Project Dashboard

A simple, private, self-hosted web app for writing down and tracking hobby
project ideas — both for the local AI setup and general software projects.

Multi-user with login, one container, one data volume. Everyone can see and
create projects; only the project owner (or an admin) can edit or delete one.
The first account to register becomes the admin.

## Features

- **Project ideas** with title, free-text plan/description, category,
  status (`idea → planning → in_progress → paused → done`),
  priority (`low / medium / high`), and tags
- **Dashboard** with a stats strip (counts per status, click to filter),
  status/category filters, and text search
- **Detail view** with the full plan text, **create/edit** via modal form,
  **delete** with confirmation
- **Multi-user**: login/register screen, per-project owner badge, edit/delete
  hidden for projects you don't own (admins can manage everything)
- **REST API** with automatic OpenAPI docs at `/docs`

## Quick start (Docker)

```bash
export DASHBOARD_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
docker compose up -d --build
```

(Or put `DASHBOARD_SECRET=...` in a `.env` file next to `docker-compose.yml`.)

Then open <http://localhost:8330> and create the first account — it becomes
the admin. Data is stored in the named volume `dashboard_data` (a single
SQLite file).

### Manual Docker

```bash
docker build -t project-dashboard .
docker run -d --name project-dashboard \
  -p 8000:8000 -e DASHBOARD_SECRET=change-me -v dashboard_data:/data project-dashboard
```

### Environment variables

| Variable | Purpose |
|---|---|
| `DASHBOARD_SECRET` | Signing key for session cookies. Set it for stable sessions across restarts (required by docker-compose). |
| `DASHBOARD_REQUIRE_SECRET` | `1` = refuse to start if `DASHBOARD_SECRET` is unset. |
| `DASHBOARD_ENABLE_SIGNUP` | `1` = allow open self-service signup (new accounts are non-admins). Default: after the first account exists, only admins can create accounts. |
| `DASHBOARD_DB` | SQLite file path (default `data/dashboard.db`, `/data/dashboard.db` in Docker). |

## Run locally (without Docker)

Requires Python 3.12+.

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8335
```

Then open <http://localhost:8335>.

The database is created at `data/dashboard.db` on first start
(override with the `DASHBOARD_DB` environment variable). Set
`DASHBOARD_SECRET` for stable sessions across restarts.

### In VS Code

Open the folder and press <kbd>F5</kbd> ("Dashboard: uvicorn (reload)") to
run with the debugger attached on port 8335. The `.vscode/` configs pin the
`.venv` interpreter, load `DASHBOARD_SECRET` from `.env`, and enable pytest in
the Test Explorer. Install the recommended extensions when prompted.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

75 tests cover auth (bootstrap admin, login/logout, signup gating, rate
limiting, DB migration), API tokens, CRUD round-trips, ownership (403 for non-owners),
validation (422 on bad status/priority/title), 401/404s, filters, stats,
the health endpoint, and static file serving.

## API

All routes except `/api/health` and the auth endpoints require a session
cookie (401 otherwise). Update/delete on projects are allowed only for the
owner or an admin (403 otherwise).

| Method | Path                 | Description                                  |
|--------|----------------------|----------------------------------------------|
| GET    | `/api/health`        | `{"status": "ok"}` (public)                  |
| GET    | `/api/auth/config`   | Public: whether the register link should show |
| POST   | `/api/auth/register` | Create account (open while no users exist; then admin-only or `DASHBOARD_ENABLE_SIGNUP=1`); auto-login |
| POST   | `/api/auth/login`    | Sets the session cookie                      |
| POST   | `/api/auth/logout`   | Clears the session cookie                    |
| GET    | `/api/auth/me`       | Current user                                 |
| GET    | `/api/projects`      | List (all users see all); supports `?status=`, `?category=`, `?q=` |
| POST   | `/api/projects`      | Create (owned by the current user)           |
| GET    | `/api/projects/{id}` | Single (includes `owner_id`/`owner_username`) |
| PUT    | `/api/projects/{id}` | Update (partial or full); owner/admin only   |
| DELETE | `/api/projects/{id}` | Delete; owner/admin only                     |
| GET    | `/api/stats`         | Counts per status + `by_owner` per-user counts |

Interactive docs: <http://localhost:8000/docs>.

## Project structure

```
project_dashboard/
├── app/
│   ├── main.py          # FastAPI app, API routes, static mount
│   ├── db.py            # SQLite connection + schema bootstrap + in-place migration
│   ├── models.py        # pydantic request/response schemas
│   ├── crud.py          # data access (users + projects)
│   ├── auth.py          # password hashing, session cookies, current-user dependency
│   └── static/          # index.html + app.js + style.css
├── tests/               # pytest suite
├── data/                # SQLite db (gitignored; Docker volume mount point)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Upgrading from the single-user version

Existing databases migrate automatically on next start: the `users` table is
created and `projects.owner_id` is added. When the first user registers, all
pre-existing projects are assigned to that (bootstrap admin) user — no manual
step needed. Set `DASHBOARD_SECRET` before starting so sessions survive
restarts.

## Backups

Everything is one SQLite file. Back it up with:

```bash
docker cp project-dashboard:/data/dashboard.db ./dashboard-backup.db
```
