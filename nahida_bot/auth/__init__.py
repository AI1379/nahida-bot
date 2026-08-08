"""Authentication helpers (OAuth, token refresh, etc.).

Currently houses the ChatGPT Codex OAuth flow. Future OAuth-based providers
(xAI, GitHub Copilot, …) can share the same package.
"""

from nahida_bot.auth.codex import (
    CODEX_API_ENDPOINT,
    DEFAULT_CLIENT_ID,
    DEFAULT_ORIGINATOR,
    DEVICE_VERIFICATION_URL,
    DeviceChallenge,
    ISSUER,
    TokenResponse,
    extract_account_id,
    poll_device_challenge,
    refresh_access_token,
    request_device_challenge,
    resolve_client_id,
    resolve_originator,
    to_codex_token,
    token_needs_refresh,
    user_agent,
)

__all__ = [
    "CODEX_API_ENDPOINT",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_ORIGINATOR",
    "DEVICE_VERIFICATION_URL",
    "DeviceChallenge",
    "ISSUER",
    "TokenResponse",
    "extract_account_id",
    "poll_device_challenge",
    "refresh_access_token",
    "request_device_challenge",
    "resolve_client_id",
    "resolve_originator",
    "to_codex_token",
    "token_needs_refresh",
    "user_agent",
]
