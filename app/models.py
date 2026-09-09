"""Pydantic request/response schemas for the projects API."""

from __future__ import annotations

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
    created_at: str
    updated_at: str
