# AGENTS.md

Guidance for AI coding agents working in this repository. Human docs live in
[README.md](README.md); this file is the operational contract for agents.

## What this is

A small, self-hosted **project-idea tracker**. FastAPI backend + a static
vanilla-JS frontend, backed by a single SQLite file. Multi-user with simple
session-cookie auth **or personal API tokens** (Bearer), and owner-gated
writes (shared workspace — everyone can read everything; only the owner or
an admin can update/delete). Keep it
simple — resist adding frameworks, ORMs, build steps, or a database server.
Design rationale: `docs/multi-user-support-issue.md`.

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
  db.py       # sqlite3 connection ctx manager + schema bootstrap (init_db) + in-place migration
  models.py   # pydantic schemas + STATUSES/PRIORITIES tuples + validators
  crud.py     # all SQL / data access lives here (users + projects + API tokens)
  auth.py     # scrypt password hashing, signed session cookies, Bearer token auth, get_current_user, login rate limit
  static/     # index.html, app.js, style.css — the frontend (incl. login/register screen)
tests/        # pytest suite (conftest.py points the app at a temp DB; auth fixtures)
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

**Auth:** every route below requires **either** a session cookie **or**
`Authorization: Bearer <api-token>` **except** `/api/health`,
`/api/auth/login`, `/api/auth/register`, and `/api/auth/config`.
Unauthenticated requests get 401. When a Bearer header is present it takes
precedence and the cookie is ignored. `register` is open only while zero
users exist (first user becomes admin); afterwards it is admin-only unless
`DASHBOARD_ENABLE_SIGNUP=1`.

| Method | Path                 | Notes                                        |
|--------|----------------------|----------------------------------------------|
| GET    | `/api/health`        | `{"status": "ok"}` (public)                  |
| GET    | `/api/auth/config`   | Public: `{bootstrap, signup_open}` for the login screen |
| POST   | `/api/auth/register` | 201 + auto-login cookie; 409 duplicate username; gated as above |
| POST   | `/api/auth/login`    | Sets session cookie; 401 bad credentials; 429 rate-limited |
| POST   | `/api/auth/logout`   | 204; clears the cookie                       |
| GET    | `/api/auth/me`       | Current user (no password hash)              |
| POST   | `/api/auth/tokens`   | 201; mint a self-scoped API token; plaintext returned **once**; optional `expires_at` |
| GET    | `/api/auth/tokens`   | Own tokens (metadata only); `?all=1` is admin-only |
| DELETE | `/api/auth/tokens/{id}` | 204; revoke (sets `revoked_at`); 404 if missing/revoked; 403 if neither owner nor admin |
| GET    | `/api/projects`      | Filters: `?status=`, `?category=`, `?q=`; everyone sees all |
| POST   | `/api/projects`      | 201; body = `ProjectCreate`; `owner_id` = current user (never client-set) |
| GET    | `/api/projects/{id}` | 404 if missing; response includes `owner_id`/`owner_username` |
| PUT    | `/api/projects/{id}` | Partial update (`exclude_unset`); 404 if missing; 403 if neither owner nor admin |
| DELETE | `/api/projects/{id}` | 204; 404 if missing; 403 if neither owner nor admin |
| GET    | `/api/stats`         | Count per status (zero-filled, legacy top-level keys) + `by_owner` per-user counts |

## Data model & invariants

- A **user** has: `username` (unique, stored lowercased, `COLLATE NOCASE`),
  `password_hash` (self-describing `scrypt$n=..$r=..$p=..$salt$hash` string —
  see `auth.py`), `display_name`, `is_admin`, `created_at`, `last_login_at`.
- A project has: `title` (required, non-blank, ≤200 chars), `description`,
  `category`, `status`, `priority`, `tags` (free-text CSV string), plus
  server-managed `id`, `owner_id` (FK → users, set to the creating user),
   `updated_by` (FK → users, set to whoever last edited), `created_at`,
   `updated_at`.
- An **API token** (`api_tokens`) has: `name` (label), `user_id` (FK → users,
  the identity the token acts as — always its creator), `token_hash`
  (`sha256(plaintext)`, `UNIQUE`), `created_at`, `last_used_at`, `expires_at`
  (nullable; past → 401), `revoked_at` (nullable; `NULL` = active). The
  plaintext is shown exactly once at `POST` time and never stored/returned
  again. Tokens are the credential for non-browser clients (curl/Postman/CI).
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
  call crud functions, not touch the DB directly. Ownership checks (owner or
  admin, 403) live in the route layer via `_assert_can_write` in `main.py`.
- **Auth:** use the `auth.get_current_user` dependency for protected routes;
  it accepts a signed session cookie **or** a personal API token via
  `Authorization: Bearer <token>` (bearer takes precedence when present).
  Never log or return `password_hash` or a token's plaintext/hash; return
  users via `_user_out()` and tokens via `_token_out()` (metadata only).
  Session cookies are signed with `DASHBOARD_SECRET` (`itsdangerous`);
  never hand-roll new signing schemes. Token plaintexts are
  `pdt_` + `secrets.token_urlsafe(32)` and only their SHA-256 hash is
  persisted — see the `api_tokens` table above.
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
- `tests/conftest.py` points the app at a temporary SQLite DB, sets a test
  `DASHBOARD_SECRET`, and provides `client` (unauthenticated), `admin_client`
  (bootstrap admin session), and `user_client` (second non-admin user, separate
  cookies) fixtures. Never write against the real `data/dashboard.db` in tests.
- When adding an endpoint or changing behavior, add/adjust tests in
  `tests/test_api.py` / `tests/test_auth.py`. Cover the happy path plus
  validation (422), 401s, 403s, and 404s, matching the existing patterns.

## Things to be careful about

- **Auth is minimal by design.** Session cookies are signed but stateless
  (logout cannot revoke an issued cookie), and login rate limiting is
  per-process. Don't expose this beyond a private network without adding
  HTTPS and reviewing the threat model.
- **`DASHBOARD_SECRET`** must be set for stable sessions (docker-compose
  requires it); `DASHBOARD_REQUIRE_SECRET=1` refuses to boot without it.
  `DASHBOARD_ENABLE_SIGNUP=1` reopens public self-signup (non-admins).
- **CORS is not configured** — fine for Postman/curl and the same-origin
  frontend, but a cross-origin browser client needs `CORSMiddleware` added.
- **API tokens are long-lived** — revocation (or `expires_at`) is the primary
  control. They intentionally bypass the cookie's `httpOnly`/`SameSite`
  protections, and the login rate limiter does not cover them (256-bit tokens
  are not brute-forceable). Never log or persist a token's plaintext.
- Data is a single SQLite file on the `/data` volume; back up by copying
  `dashboard.db`. Don't introduce a migration framework — if the schema
  changes, evolve the `SCHEMA` string and note any manual migration needed.
  Pre-existing DBs are upgraded in-place by `db._migrate()` (idempotent
  `PRAGMA table_info` check + `ALTER TABLE`); the `projects.owner_id`
  backfill to the bootstrap admin happens automatically on first register.
- Keep runtime dependencies minimal (`requirements.txt`). Prefer the stdlib.
