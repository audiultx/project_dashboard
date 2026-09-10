"""Authentication: password hashing, signed session cookies, current-user
dependency, and a simple in-memory login rate limiter.

Password hashes use stdlib hashlib.scrypt with a per-user random salt, stored
as a self-describing string:  scrypt$n=32768$r=8$p=1$<salt-b64>$<hash-b64>.
The format is versioned ("scrypt") so a future swap to bcrypt can be a
verify-and-rehash-on-login migration.

Session cookies carry only the user id, signed with itsdangerous using
DASHBOARD_SECRET. There is no server-side session store: logout clears the
cookie but cannot revoke an already-issued one (acceptable for this app).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from base64 import b64decode, b64encode
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import crud

SESSION_COOKIE = "dashboard_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _DKLEN = 32768, 8, 1, 32
# 128 * N * r = 32 MiB, exactly OpenSSL's default maxmem ceiling, so the cap is
# raised to 64 MiB to avoid scrypt raising on tight memory layouts.
_SCRYPT_MAXMEM = 64 * 1024 * 1024

# ---------------------------------------------------------------- password hashing

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return (
        f"scrypt$n={_SCRYPT_N}$r={_SCRYPT_R}$p={_SCRYPT_P}$"
        f"{b64encode(salt).decode('ascii')}${b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = b64decode(salt_b64)
        expected = b64decode(hash_b64)
        # The stored string carries its own parameters, so verify with exactly
        # what was used at hash time (maxmem sized to that N so a high-N
        # hash cannot trip the default memory cap).
        n, r, p = int(n_s[2:]), int(r_s[2:]), int(p_s[2:])
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected),
            maxmem=max(_SCRYPT_MAXMEM, 128 * n * r * 2),
        )
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- session secret

_fallback_secret: bytes | None = None


def _get_secret() -> bytes:
    """Resolve DASHBOARD_SECRET at call time (tests override the env var).

    When unset, a per-process random secret is used so the app still works in
    development; sessions simply do not survive a restart.
    """
    global _fallback_secret
    env = os.environ.get("DASHBOARD_SECRET")
    if env:
        return env.encode("utf-8")
    if _fallback_secret is None:
        _fallback_secret = secrets.token_bytes(32)
        print(
            "WARNING: DASHBOARD_SECRET is not set; using a random per-process secret. "
            "Sessions will not survive restarts. Set DASHBOARD_SECRET (and consider "
            "DASHBOARD_REQUIRE_SECRET=1 in production).",
            flush=True,
        )
    return _fallback_secret


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_get_secret(), salt="project-dashboard-session")


def encode_session(user_id: int) -> str:
    return _serializer().dumps({"user_id": user_id})


def decode_session(value: str | None) -> int | None:
    """Return the user id from a session cookie value, or None if invalid/expired."""
    if not value:
        return None
    try:
        data = _serializer().loads(value, max_age=SESSION_MAX_AGE)
        return int(data["user_id"])
    except (BadSignature, SignatureExpired, TypeError, ValueError, KeyError):
        return None


# ---------------------------------------------------------------- login rate limiting
#
# Deliberately simple and per-process: this is a mitigation against casual
# brute force, not a guarantee (it resets on restart and is not shared across
# uvicorn workers).

_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 300
# Hard cap on distinct usernames tracked at once. Past it we first sweep out
# entries whose failures have all aged out, then, if still over, evict the
# oldest-inserted buckets so attacker-supplied unique usernames cannot grow the
# map without bound (a plain dict preserves insertion order).
_LOGIN_MAX_TRACKED = 4096

_rate_lock = threading.Lock()
_login_failures: dict[str, list[float]] = {}


def _norm_username(username: str) -> str:
    """Normalize a username for rate-limit bucketing.

    Usernames are compared case-insensitively (COLLATE NOCASE in SQLite), so the
    buckets must be too — otherwise "admin" / "Admin" / "ADMIN" are separate
    buckets that all target the same account, defeating the per-username limit.
    """
    return username.lower()


def _sweep_expired_locked(now: float) -> None:
    stale = [u for u, ts in _login_failures.items() if all(now - t >= _LOGIN_WINDOW_SECONDS for t in ts)]
    for u in stale:
        del _login_failures[u]


def _login_rate_limited(username: str) -> bool:
    username = _norm_username(username)
    now = time.monotonic()
    with _rate_lock:
        recent = [t for t in _login_failures.get(username, []) if now - t < _LOGIN_WINDOW_SECONDS]
        if recent:
            _login_failures[username] = recent
        else:
            _login_failures.pop(username, None)
        return len(recent) >= _LOGIN_MAX_FAILURES


def _record_login_failure(username: str) -> None:
    username = _norm_username(username)
    now = time.monotonic()
    with _rate_lock:
        _login_failures.setdefault(username, []).append(now)
        if len(_login_failures) > _LOGIN_MAX_TRACKED:
            _sweep_expired_locked(now)
            # The sweep only reclaims fully-aged buckets; under an active spray
            # of unique usernames every bucket holds a recent failure and none
            # age out, so hard-evict oldest-inserted buckets to keep the cap a
            # real bound. Evicting a bucket merely resets that username's
            # counter (it can re-accumulate up to the per-username limit).
            while len(_login_failures) > _LOGIN_MAX_TRACKED:
                del _login_failures[next(iter(_login_failures))]


def _clear_login_failures(username: str) -> None:
    username = _norm_username(username)
    with _rate_lock:
        _login_failures.pop(username, None)


def reset_login_rate_limits() -> None:
    """Test helper: clear all recorded failures."""
    with _rate_lock:
        _login_failures.clear()


# ---------------------------------------------------------------- API tokens
#
# Personal API tokens (GitHub-PAT model): minted by any authenticated user,
# they always act as their creator. Only the SHA-256 hash is persisted; the
# plaintext is returned once at creation. Bearer auth is checked first; when
# a Bearer header is present the session cookie is ignored.

def _token_is_active(row: dict) -> bool:
    """Active = not revoked and (no expiry, or expiry in the future, UTC)."""
    if row["revoked_at"] is not None:
        return False
    expires_at = row["expires_at"]
    if expires_at is not None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if expires_at < now:
            return False
    return True


def authenticate_bearer(token: str) -> dict | None:
    """Resolve a plaintext Bearer token to a user row, or None.

    Bumps the token's last_used_at when the lookup succeeds.
    """
    row = crud.get_token_by_hash(crud.token_hash(token))
    if row is None or not _token_is_active(row):
        return None
    user = crud.get_user(row["user_id"])
    if user is None:
        return None
    crud.touch_token(row["id"])
    return user


# ---------------------------------------------------------------- FastAPI dependency

def get_current_user(request: Request) -> dict:
    """Return the authenticated user's row, or raise 401.

    Accepts either `Authorization: Bearer <api-token>` (non-browser clients)
    or the signed session cookie (browser). The Bearer header, when present,
    takes precedence and is the only credential considered.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        user = authenticate_bearer(header[7:].strip())
        if user is not None:
            return user
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = decode_session(request.cookies.get(SESSION_COOKIE))
    user = crud.get_user(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
