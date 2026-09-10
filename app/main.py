"""FastAPI application: API routes + static frontend."""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles

from . import auth, crud, db
from .models import (
    PRIORITIES,
    STATUSES,
    Login,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    TokenCreate,
    TokenCreateOut,
    TokenOut,
    UserCreate,
    UserOut,
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _user_out(user: dict) -> dict:
    """Project a user row down to its public fields (never the password hash)."""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "is_admin": bool(user["is_admin"]),
        "created_at": user["created_at"],
    }


def _set_session_cookie(response: Response, user_id: int, request: Request) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.encode_session(user_id),
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",  # Secure only when served over HTTPS
    )


def _signup_open() -> bool:
    return os.environ.get("DASHBOARD_ENABLE_SIGNUP") == "1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.environ.get("DASHBOARD_REQUIRE_SECRET") == "1" and not os.environ.get("DASHBOARD_SECRET"):
        raise RuntimeError("DASHBOARD_REQUIRE_SECRET=1 but DASHBOARD_SECRET is not set; refusing to start.")
    db.init_db()
    yield


app = FastAPI(title="Project Dashboard", version="1.1.0", lifespan=lifespan)


# ---------------------------------------------------------------- API routes

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------- auth routes

@app.get("/api/auth/config")
def auth_config() -> dict[str, bool]:
    """Public (unauthenticated) flag for the login screen: whether a register
    link should be shown. bootstrap = no users yet (first user becomes admin)."""
    return {"bootstrap": crud.count_users() == 0, "signup_open": _signup_open()}


@app.post("/api/auth/register", response_model=UserOut, status_code=201)
def register(payload: UserCreate, request: Request, response: Response):
    # Signup policy: open only while zero users exist (first user becomes admin);
    # afterwards admin-only, unless DASHBOARD_ENABLE_SIGNUP=1 reopens it.
    # Is the caller already authenticated (session cookie or Bearer token)? The
    # caller is optional here — bootstrap signup is anonymous — but an admin
    # creating an account for someone else must keep their own session
    # (see the cookie note below).
    caller = auth.resolve_optional_user(request)
    # allow_regular is the authoritative gate, re-checked atomically inside the
    # insert transaction; the pre-check below is only a fast reject path.
    allow_regular = _signup_open() or (caller is not None and caller["is_admin"])
    if crud.count_users() > 0 and not allow_regular:
        if caller is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        raise HTTPException(status_code=403, detail="Only an admin can create accounts")

    if crud.get_user_by_username(payload.username) is not None:
        raise HTTPException(status_code=409, detail="Username is already taken")

    try:
        # The bootstrap decision (first user becomes admin) and the signup-gate
        # decision are both made atomically inside the insert transaction, so
        # concurrent first-registrations cannot both win.
        new_user, won_bootstrap = crud.create_first_or_regular_user(
            username=payload.username,
            password_hash=auth.hash_password(payload.password),
            display_name=payload.display_name.strip(),
            allow_regular=allow_regular,
        )
    except crud.SignupClosedError:
        # Passed the pre-check while the DB was still empty, but a concurrent
        # bootstrap committed while we waited on the write lock.
        if caller is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        raise HTTPException(status_code=403, detail="Only an admin can create accounts")
    except sqlite3.IntegrityError:
        # Lost the race against a concurrent register for the same username; the
        # UNIQUE constraint is the real arbiter (the check above is a fast path).
        raise HTTPException(status_code=409, detail="Username is already taken")
    if won_bootstrap:
        # One-time migration: existing (pre-multi-user) projects become owned by
        # the first user, who is the bootstrap admin.
        crud.backfill_project_owners(new_user["id"])
    # Auto-login only on genuine self-signup. When an already-authenticated admin
    # creates an account, do not overwrite their session cookie with the new user.
    if caller is None:
        _set_session_cookie(response, new_user["id"], request)
    return _user_out(new_user)


@app.post("/api/auth/login", response_model=UserOut)
def login(payload: Login, request: Request, response: Response) -> dict:
    username = payload.username
    if auth._login_rate_limited(username):
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again in a few minutes.")
    user = crud.get_user_by_username(username)
    if user is None or not auth.verify_password(payload.password, user["password_hash"]):
        auth._record_login_failure(username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    auth._clear_login_failures(username)
    crud.update_last_login(user["id"])
    _set_session_cookie(response, user["id"], request)
    return _user_out(user)


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response, user: dict = Depends(auth.get_current_user)) -> None:
    response.delete_cookie(auth.SESSION_COOKIE, samesite="lax", secure=request.url.scheme == "https")


@app.get("/api/auth/me", response_model=UserOut)
def me(user: dict = Depends(auth.get_current_user)) -> dict:
    return _user_out(user)


# ---------------------------------------------------------------- API tokens
#
# Personal API tokens for non-browser clients (curl, Postman, scripts, CI).
# Any authenticated user may mint tokens that act as their own identity;
# the plaintext is returned exactly once and only the SHA-256 hash is stored.

def _token_out(row: dict) -> dict:
    """Project a token row down to public metadata (never the hash or plaintext)."""
    return {
        "id": row["id"],
        "name": row["name"],
        "user_id": row["user_id"],
        "owner_username": row["owner_username"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
    }


@app.post("/api/auth/tokens", response_model=TokenCreateOut, status_code=201)
def create_api_token(payload: TokenCreate, user: dict = Depends(auth.get_current_user)) -> dict:
    """Mint a token for the calling user; returns the plaintext exactly once."""
    token, row = crud.create_token(user["id"], payload.name, payload.expires_at)
    return {**_token_out(row), "token": token}


@app.get("/api/auth/tokens", response_model=list[TokenOut])
def list_api_tokens(
    list_all: bool = Query(False, alias="all", description="Admin only: list every user's tokens"),
    user: dict = Depends(auth.get_current_user),
) -> list[dict]:
    """List the caller's tokens, or all users' tokens for admins (?all=1)."""
    if list_all:
        if not user["is_admin"]:
            raise HTTPException(status_code=403, detail="Only an admin can list all tokens")
        rows = crud.list_tokens()
    else:
        rows = crud.list_tokens(user["id"])
    return [_token_out(r) for r in rows]


@app.delete("/api/auth/tokens/{token_id}", status_code=204)
def revoke_api_token(token_id: int, user: dict = Depends(auth.get_current_user)) -> None:
    """Revoke a token: owner or admin (404-then-403 ordering, like projects)."""
    token = crud.get_token(token_id)
    if token is None or token["revoked_at"] is not None:
        raise HTTPException(status_code=404, detail="Token not found")
    if not (user["is_admin"] or token["user_id"] == user["id"]):
        raise HTTPException(status_code=403, detail="Only the token owner or an admin can revoke this token")
    crud.revoke_token(token_id)


# ---------------------------------------------------------------- projects

def _assert_can_write(user: dict, project: dict) -> None:
    """Owner or admin may update/delete; everyone else gets 403."""
    if not (user["is_admin"] or project["owner_id"] == user["id"]):
        raise HTTPException(status_code=403, detail="Only the project owner or an admin can modify this project")


@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects(
    status: str | None = Query(None),
    category: str | None = Query(None),
    q: str | None = Query(None, description="Text search over title/description/tags"),
    user: dict = Depends(auth.get_current_user),
) -> list[dict]:
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(STATUSES)}")
    return crud.list_projects(status=status, category=category, q=q)


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, user: dict = Depends(auth.get_current_user)) -> dict:
    # owner_id is server-managed: it is always the current user, never the client.
    return crud.create_project(payload.model_dump(), owner_id=user["id"])


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, user: dict = Depends(auth.get_current_user)) -> dict:
    project = crud.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.put("/api/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectUpdate, user: dict = Depends(auth.get_current_user)) -> dict:
    project = crud.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _assert_can_write(user, project)
    updated = crud.update_project(project_id, payload.model_dump(exclude_unset=True), updated_by=user["id"])
    if updated is None:
        # Raced a concurrent delete between the fetch above and the update.
        raise HTTPException(status_code=404, detail="Project not found")
    return updated


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: int, user: dict = Depends(auth.get_current_user)) -> None:
    project = crud.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _assert_can_write(user, project)
    if not crud.delete_project(project_id):
        # Raced a concurrent delete between the fetch above and the DELETE.
        raise HTTPException(status_code=404, detail="Project not found")


@app.get("/api/stats")
def stats(user: dict = Depends(auth.get_current_user)) -> dict:
    return crud.stats()


# ------------------------------------------------------------- static frontend
# Mounted last so /api/* routes take precedence; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
