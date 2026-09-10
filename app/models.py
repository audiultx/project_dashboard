"""Pydantic request/response schemas for the projects API."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

STATUSES = ("idea", "planning", "in_progress", "paused", "done")
PRIORITIES = ("low", "medium", "high")


def _validate_status(v: str) -> str:
    if v not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    return v


def _validate_priority(v: str) -> str:
    if v not in PRIORITIES:
        raise ValueError(f"priority must be one of: {', '.join(PRIORITIES)}")
    return v


def _validate_title(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("title must not be blank")
    return v


def _validate_username(v: str) -> str:
    v = v.strip().lower()
    if not v:
        raise ValueError("username must not be blank")
    if any(c.isspace() for c in v):
        raise ValueError("username must not contain whitespace")
    return v


class ProjectBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    category: str = "Other"
    status: str = "idea"
    priority: str = "medium"
    tags: str = ""

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        return _validate_title(v)

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        return _validate_status(v)

    @field_validator("priority")
    @classmethod
    def _check_priority(cls, v: str) -> str:
        return _validate_priority(v)


class ProjectCreate(ProjectBase):
    """Payload for POST /api/projects."""


class ProjectUpdate(BaseModel):
    """Payload for PUT /api/projects/{id} — all fields optional (partial update)."""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    category: str | None = None
    status: str | None = None
    priority: str | None = None
    tags: str | None = None

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str | None) -> str | None:
        return _validate_title(v) if v is not None else v

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str | None) -> str | None:
        return _validate_status(v) if v is not None else v

    @field_validator("priority")
    @classmethod
    def _check_priority(cls, v: str | None) -> str | None:
        return _validate_priority(v) if v is not None else v


class ProjectOut(ProjectBase):
    """Full project representation returned by the API."""

    id: int
    owner_id: int | None
    owner_username: str | None
    updated_by: int | None
    updated_by_username: str | None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------- auth schemas

class UserCreate(BaseModel):
    """Payload for POST /api/auth/register."""

    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=8, max_length=200)
    display_name: str = Field("", max_length=64)

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        return _validate_username(v)


class Login(BaseModel):
    """Payload for POST /api/auth/login."""

    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=200)

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        return _validate_username(v)


class UserOut(BaseModel):
    """Public user representation (never includes the password hash)."""

    id: int
    username: str
    display_name: str
    is_admin: bool
    created_at: str


# ---------------------------------------------------------------- token schemas

def _validate_token_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("name must not be blank")
    return v


def _normalize_expires_at(v: str | None) -> str | None:
    """Normalize an ISO-8601 datetime to a UTC 'YYYY-MM-DD HH:MM:SS' string.

    Naive datetimes are treated as UTC. The result is comparable lexicographically
    against SQLite's ``datetime('now')`` values, so expiry checks can be plain
    string comparisons.
    """
    if v is None:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError as exc:
        raise ValueError("expires_at must be an ISO-8601 datetime") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class TokenCreate(BaseModel):
    """Payload for POST /api/auth/tokens.

    ``name`` is a human label; ``expires_at`` (optional) sets an automatic
    expiry — a token whose expiry is in the past authenticates as 401.
    """

    name: str = Field(..., min_length=1, max_length=64)
    expires_at: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _validate_token_name(v)

    @field_validator("expires_at")
    @classmethod
    def _check_expires_at(cls, v: str | None) -> str | None:
        return _normalize_expires_at(v)


class TokenOut(BaseModel):
    """Public token metadata. Never includes the plaintext or the hash."""

    id: int
    name: str
    user_id: int
    owner_username: str | None
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    revoked_at: str | None


class TokenCreateOut(TokenOut):
    """Response for POST /api/auth/tokens.

    ``token`` is the plaintext secret, returned exactly once; after this
    response only the SHA-256 hash is retained server-side.
    """

    token: str
