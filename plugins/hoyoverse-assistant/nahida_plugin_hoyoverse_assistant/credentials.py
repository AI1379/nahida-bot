"""Per-user credential storage isolated from ordinary plugin data."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Protocol

_COOKIE_PREFIX = "cookie:"
_QR_PREFIX = "qr:"


class PluginSecretPort(Protocol):
    async def plugin_secret_get(self, key: str) -> str | None: ...

    async def plugin_secret_set(self, key: str, secret: str) -> None: ...

    async def plugin_secret_delete(self, key: str) -> bool: ...


@dataclass(slots=True, frozen=True)
class PendingQRCode:
    ticket: str
    created_at: float


class CredentialStore:
    """Store opaque credentials by a hashed, stable platform-user key."""

    def __init__(self, secrets: PluginSecretPort, *, qr_ttl_seconds: int) -> None:
        self._secrets = secrets
        self._qr_ttl_seconds = qr_ttl_seconds

    async def get_cookies(self, actor_key: str) -> str | None:
        value = await self._secrets.plugin_secret_get(
            self._key(_COOKIE_PREFIX, actor_key)
        )
        return value or None

    async def set_cookies(self, actor_key: str, cookies: str) -> None:
        await self._secrets.plugin_secret_set(
            self._key(_COOKIE_PREFIX, actor_key), cookies
        )

    async def delete_cookies(self, actor_key: str) -> bool:
        return await self._secrets.plugin_secret_delete(
            self._key(_COOKIE_PREFIX, actor_key)
        )

    async def set_pending_qr(self, actor_key: str, ticket: str) -> None:
        pending = PendingQRCode(ticket=ticket, created_at=time.time())
        await self._secrets.plugin_secret_set(
            self._key(_QR_PREFIX, actor_key),
            json.dumps(asdict(pending), separators=(",", ":")),
        )

    async def get_pending_qr(self, actor_key: str) -> PendingQRCode | None:
        raw = await self._secrets.plugin_secret_get(self._key(_QR_PREFIX, actor_key))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            pending = PendingQRCode(
                ticket=str(payload["ticket"]),
                created_at=float(payload["created_at"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await self.delete_pending_qr(actor_key)
            return None
        if time.time() - pending.created_at > self._qr_ttl_seconds:
            await self.delete_pending_qr(actor_key)
            return None
        return pending

    async def delete_pending_qr(self, actor_key: str) -> bool:
        return await self._secrets.plugin_secret_delete(
            self._key(_QR_PREFIX, actor_key)
        )

    @staticmethod
    def _key(prefix: str, actor_key: str) -> str:
        digest = hashlib.sha256(actor_key.encode("utf-8")).hexdigest()
        return f"{prefix}{digest}"
