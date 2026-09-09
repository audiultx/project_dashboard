# Project Dashboard

A simple, private, self-hosted web app for writing down and tracking hobby
project ideas — both for the local AI setup and general software projects.

Single user, no login, one container, one data volume.

## Features

- **Project ideas** with title, free-text plan/description, category,
  status (`idea → planning → in_progress → paused → done`),
  priority (`low / medium / high`), and tags
- **Dashboard** with a stats strip (counts per status, click to filter),
  status/category filters, and text search
- **Detail view** with the full plan text, **create/edit** via modal form,
  **delete** with confirmation
- **REST API** with automatic OpenAPI docs at `/docs`

## Quick start (Docker)

```bash
docker compose up -d --build
```

Then open <http://localhost:8000>. Data is stored in the named volume
`dashboard_data` (a single SQLite file).

### Manual Docker

```bash
docker build -t project-dashboard .
docker run -d --name project-dashboard \
  -p 8000:8000 -v dashboard_data:/data project-dashboard
```

## Run locally (without Docker)

Requires Python 3.12+.

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The database is created at `data/dashboard.db` on first start
(override with the `DASHBOARD_DB` environment variable).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

28 tests cover CRUD round-trips, validation (422 on bad status/priority/title),
404s, filters, stats, the health endpoint, and static file serving.

## API

| Method | Path                 | Description                                  |
|--------|----------------------|----------------------------------------------|
| GET    | `/api/health`        | `{"status": "ok"}`                           |
| GET    | `/api/projects`      | List; supports `?status=`, `?category=`, `?q=` |
| POST   | `/api/projects`      | Create                                       |
| GET    | `/api/projects/{id}` | Single                                       |
| PUT    | `/api/projects/{id}` | Update (partial or full)                     |
| DELETE | `/api/projects/{id}` | Delete                                       |
| GET    | `/api/stats`         | Counts per status                            |

Interactive docs: <http://localhost:8000/docs>.

## Project structure

```
project_dashboard/
├── app/
│   ├── main.py          # FastAPI app, API routes, static mount
│   ├── db.py            # SQLite connection + schema bootstrap
│   ├── models.py        # pydantic request/response schemas
│   ├── crud.py          # data access
│   └── static/          # index.html + app.js + style.css
├── tests/               # pytest suite
├── data/                # SQLite db (gitignored; Docker volume mount point)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Backups

Everything is one SQLite file. Back it up with:

```bash
docker cp project-dashboard:/data/dashboard.db ./dashboard-backup.db
```
