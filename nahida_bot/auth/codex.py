"""ChatGPT Codex OAuth helpers — JWT parsing, device-code login, refresh.

Mirrors the flow used by the official Codex CLI and by opencode
(``plugin/openai/codex.ts``): ChatGPT Plus/Pro subscriptions are reachable
through OAuth against ``auth.openai.com`` plus a request rewrite to the
codex backend at ``chatgpt.com/backend-api/codex/responses``.

This module is transport-only — it does not touch the database. Callers
(``CodexProvider`` for refresh, the ``auth login`` CLI for the initial
device-code flow) are responsible for persisting returned tokens via
``SQLiteCodexTokenRepository``.

Two values are hard-coded with environment-variable overrides:

- ``client_id`` — public OAuth app identifier. Users normally do not have
  their own; the default is the well-known Codex CLI / opencode value.
  ``NAHIDA_OPENAI_CLIENT_ID`` overrides it as a safety valve in case
  OpenAI revokes the default.
- ``originator`` — free-form attribution string sent on both the authorize
  URL and every API request. Defaults to ``"nahida-bot"`` so OpenAI can
  identify this client honestly. ``NAHIDA_OPENAI_ORIGINATOR`` overrides it
  for advanced users who understand the tradeoff (see docs).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass

import httpx
import structlog

from nahida_bot.db.repositories.sqlite_codex_token_repo import CodexToken
from nahida_bot.version import get_version

logger = structlog.get_logger(__name__)

DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_ORIGINATOR = "nahida-bot"
ISSUER = "https://auth.openai.com"
CODEX_API_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
DEVICE_CODE_URL = f"{ISSUER}/api/accounts/deviceauth/usercode"
DEVICE_POLL_URL = f"{ISSUER}/api/accounts/deviceauth/token"
DEVICE_VERIFICATION_URL = f"{ISSUER}/codex/device"
TOKEN_URL = f"{ISSUER}/oauth/token"
_REFRESH_SAFETY_MARGIN_SECONDS = 60
_DEVICE_POLL_SAFETY_MARGIN_SECONDS = 3


def resolve_client_id() -> str:
    return os.environ.get("NAHIDA_OPENAI_CLIENT_ID") or DEFAULT_CLIENT_ID


def resolve_originator() -> str:
    return os.environ.get("NAHIDA_OPENAI_ORIGINATOR") or DEFAULT_ORIGINATOR


def user_agent() -> str:
    return f"{resolve_originator()}/{get_version()}"


@dataclass(slots=True, frozen=True)
class DeviceChallenge:
    """Initial device-authorization response: code the user enters."""

    device_auth_id: str
    user_code: str
    interval_seconds: float


@dataclass(slots=True, frozen=True)
class TokenResponse:
    """Raw token payload returned by ``/oauth/token``."""

    access_token: str
    refresh_token: str
    id_token: str
    expires_in: int


def _parse_jwt_claims(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        result = json.loads(decoded)
        return result if isinstance(result, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def extract_account_id(tokens: TokenResponse) -> str:
    """Extract ``chatgpt_account_id`` from id_token, then access_token.

    Matches the precedence in opencode's ``codex.ts``:
    top-level ``chatgpt_account_id`` → nested
    ``https://api.openai.com/auth.chatgpt_account_id`` →
    ``organizations[0].id``.
    """
    for token in (tokens.id_token, tokens.access_token):
        if not token:
            continue
        claims = _parse_jwt_claims(token)
        top = claims.get("chatgpt_account_id")
        if isinstance(top, str) and top:
            return top
        nested = claims.get("https://api.openai.com/auth")
        if isinstance(nested, dict):
            nested_id = nested.get("chatgpt_account_id")
            if isinstance(nested_id, str) and nested_id:
                return nested_id
        orgs = claims.get("organizations")
        if isinstance(orgs, list) and orgs:
            first = orgs[0]
            if isinstance(first, dict):
                org_id = first.get("id")
                if isinstance(org_id, str) and org_id:
                    return org_id
    return ""


def to_codex_token(tokens: TokenResponse) -> CodexToken:
    """Convert a raw ``TokenResponse`` into a persisted ``CodexToken``."""
    expires_at = int(time.time()) + max(tokens.expires_in, 0)
    return CodexToken(
        refresh_token=tokens.refresh_token,
        access_token=tokens.access_token,
        expires_at=expires_at,
        account_id=extract_account_id(tokens),
    )


def _default_post_kwargs() -> dict[str, str]:
    return {"User-Agent": user_agent()}


async def request_device_challenge(
    client: httpx.AsyncClient, *, client_id: str | None = None
) -> DeviceChallenge:
    """Initiate a device-authorization flow and return the user code."""
    cid = client_id or resolve_client_id()
    response = await client.post(
        DEVICE_CODE_URL,
        json={"client_id": cid},
        headers={"Content-Type": "application/json", **_default_post_kwargs()},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to initiate device authorization: "
            f"HTTP {response.status_code} {response.text[:200]}"
        )
    data = response.json()
    interval_raw = data.get("interval", "5")
    try:
        interval_seconds = max(float(str(interval_raw)), 1.0)
    except (TypeError, ValueError):
        interval_seconds = 5.0
    return DeviceChallenge(
        device_auth_id=str(data["device_auth_id"]),
        user_code=str(data["user_code"]),
        interval_seconds=interval_seconds,
    )


async def poll_device_challenge(
    client: httpx.AsyncClient,
    challenge: DeviceChallenge,
    *,
    client_id: str | None = None,
    max_wait_seconds: float = 600.0,
    on_pending: "object | None" = None,
) -> TokenResponse:
    """Poll the device-authorization endpoint until the user approves.

    ``on_pending`` is an optional async callable invoked after every poll
    attempt that returns 403/404 (still waiting). It lets the CLI refresh
    its spinner without coupling this module to ``rich``.

    Raises ``TimeoutError`` if the user does not approve within
    ``max_wait_seconds``.
    """
    cid = client_id or resolve_client_id()
    deadline = time.monotonic() + max_wait_seconds
    sleep_seconds = challenge.interval_seconds + _DEVICE_POLL_SAFETY_MARGIN_SECONDS

    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Device authorization timed out waiting for user approval"
            )
        response = await client.post(
            DEVICE_POLL_URL,
            json={
                "device_auth_id": challenge.device_auth_id,
                "user_code": challenge.user_code,
            },
            headers={
                "Content-Type": "application/json",
                **_default_post_kwargs(),
            },
            timeout=30,
        )
        if response.status_code < 400:
            data = response.json()
            return await _exchange_authorization_code(
                client,
                authorization_code=str(data["authorization_code"]),
                code_verifier=str(data["code_verifier"]),
                client_id=cid,
            )
        if response.status_code in (403, 404):
            if on_pending is not None:
                await on_pending()  # type: ignore[misc]
            await asyncio.sleep(sleep_seconds)
            continue
        raise RuntimeError(
            f"Device authorization poll failed: HTTP "
            f"{response.status_code} {response.text[:200]}"
        )


async def _exchange_authorization_code(
    client: httpx.AsyncClient,
    *,
    authorization_code: str,
    code_verifier: str,
    client_id: str,
) -> TokenResponse:
    response = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": f"{ISSUER}/deviceauth/callback",
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **_default_post_kwargs(),
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Token exchange failed: HTTP {response.status_code} {response.text[:200]}"
        )
    return _parse_token_response(response.json())


def _parse_token_response(data: dict[str, object]) -> TokenResponse:
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise RuntimeError("Token response missing access_token or refresh_token")
    expires_raw = data.get("expires_in", 3600)
    try:
        expires_in = int(expires_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        expires_in = 3600
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        id_token=str(data.get("id_token") or ""),
        expires_in=expires_in,
    )


async def refresh_access_token(
    client: httpx.AsyncClient,
    refresh_token: str,
    *,
    client_id: str | None = None,
) -> TokenResponse:
    """Exchange a refresh token for a fresh access/refresh token pair."""
    cid = client_id or resolve_client_id()
    response = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": cid,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **_default_post_kwargs(),
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Token refresh failed: HTTP {response.status_code} {response.text[:200]}"
        )
    return _parse_token_response(response.json())


def token_needs_refresh(token: CodexToken) -> bool:
    """True if the access token is missing or expires within the safety margin."""
    if not token.access_token:
        return True
    return token.expires_at - time.time() <= _REFRESH_SAFETY_MARGIN_SECONDS
