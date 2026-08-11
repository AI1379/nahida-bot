"""Browser WebUI password login and in-memory session management."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request, Response

COOKIE_NAME = "nahida_webui_session"
_PBKDF2_SCHEME = "pbkdf2_sha256"
_PBKDF2_DEFAULT_ITERATIONS = 210_000


@dataclass
class WebUISession:
    session_id: str
    created_at: datetime
    expires_at: datetime
    client_host: str
    user_agent: str


@dataclass
class LoginAttemptWindow:
    started_at: datetime
    count: int = 0


def hash_password_pbkdf2(
    password: str,
    *,
    salt: str | None = None,
    iterations: int = _PBKDF2_DEFAULT_ITERATIONS,
) -> str:
    """Return a config-ready PBKDF2-SHA256 password hash string."""
    salt_value = salt or secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_value.encode("utf-8"),
        iterations,
    ).hex()
    return f"{_PBKDF2_SCHEME}${iterations}${salt_value}${digest}"


class WebUIAuthService:
    """Password login service for browser sessions.

    Sessions are intentionally process-local. Restarting the bot logs out WebUI
    browsers, which matches the current single-process local deployment model.
    """

    def __init__(self, auth_config: Any) -> None:
        self._config = auth_config
        self._sessions: dict[str, WebUISession] = {}
        self._login_attempts: dict[str, LoginAttemptWindow] = {}

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", True))

    @property
    def password_configured(self) -> bool:
        return self.enabled and bool(getattr(self._config, "admin_password_hash", ""))

    @property
    def mode(self) -> str:
        return "password" if self.password_configured else "none"

    @property
    def session_ttl_seconds(self) -> int:
        return int(getattr(self._config, "session_ttl_seconds", 3600))

    @property
    def login_rate_per_minute(self) -> int:
        return int(getattr(self._config, "login_rate_per_minute", 5))

    @property
    def bind_session_to_ip(self) -> bool:
        return bool(getattr(self._config, "bind_session_to_ip", True))

    def verify_password(self, password: str) -> bool:
        if not self.password_configured:
            return False

        configured_hash = str(getattr(self._config, "admin_password_hash", ""))
        return _verify_pbkdf2_hash(password, configured_hash)

    def is_login_allowed(self, request: Request) -> bool:
        limit = self.login_rate_per_minute
        if limit <= 0:
            return True

        key = _client_host(request)
        now = datetime.now(UTC)
        window = self._login_attempts.get(key)
        if window is None or now - window.started_at >= timedelta(minutes=1):
            self._login_attempts[key] = LoginAttemptWindow(started_at=now, count=0)
            return True
        return window.count < limit

    def record_login_failure(self, request: Request) -> None:
        limit = self.login_rate_per_minute
        if limit <= 0:
            return

        key = _client_host(request)
        now = datetime.now(UTC)
        window = self._login_attempts.get(key)
        if window is None or now - window.started_at >= timedelta(minutes=1):
            self._login_attempts[key] = LoginAttemptWindow(started_at=now, count=1)
            return
        window.count += 1

    def record_login_success(self, request: Request) -> None:
        self._login_attempts.pop(_client_host(request), None)

    def create_session(self, request: Request) -> WebUISession:
        self._prune_expired()
        session_id = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        session = WebUISession(
            session_id=session_id,
            created_at=now,
            expires_at=now + timedelta(seconds=self.session_ttl_seconds),
            client_host=_client_host(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        self._sessions[session_id] = session
        return session

    def get_request_session(self, request: Request) -> WebUISession | None:
        self._prune_expired()
        session_id = request.cookies.get(COOKIE_NAME, "")
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= datetime.now(UTC):
            self._sessions.pop(session_id, None)
            return None
        if self.bind_session_to_ip and session.client_host != _client_host(request):
            self._sessions.pop(session_id, None)
            return None
        return session

    def destroy_request_session(self, request: Request) -> None:
        session_id = request.cookies.get(COOKIE_NAME, "")
        if session_id:
            self._sessions.pop(session_id, None)

    def set_session_cookie(
        self,
        response: Response,
        request: Request,
        session: WebUISession,
    ) -> None:
        response.set_cookie(
            key=COOKIE_NAME,
            value=session.session_id,
            max_age=self.session_ttl_seconds,
            expires=int(session.expires_at.timestamp()),
            path="/",
            secure=_is_secure_request(request),
            httponly=True,
            samesite="strict",
        )

    def clear_session_cookie(self, response: Response, request: Request) -> None:
        response.delete_cookie(
            key=COOKIE_NAME,
            path="/",
            secure=_is_secure_request(request),
            httponly=True,
            samesite="strict",
        )

    def _prune_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)


def _is_secure_request(request: Request) -> bool:
    return request.url.scheme == "https"


def _client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def _verify_pbkdf2_hash(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_raw, salt, expected = stored_hash.split("$", 3)
        if scheme != _PBKDF2_SCHEME:
            return False
        iterations = int(iterations_raw)
    except ValueError:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(expected, digest)
