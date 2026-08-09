"""Node authentication: token issuance, verification and pairing.

This service owns the persistence-backed token store and implements the
``NodeTokenVerifier`` protocol consumed by the WebSocket endpoint. Tokens are
stored as SHA-256 digests (never plaintext) keyed by ``token_id``.

The Gateway injects a SQLite store while isolated consumers may use the
in-memory implementation.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from nahida_bot.gateway.node_protocol.auth import NodePrincipal

logger = structlog.get_logger(__name__)

_TOKEN_PREFIX = "nt_"  # node token
_PAIRING_PREFIX = "np_"  # node pairing token (one-shot)
_TOKEN_BYTES = 32
_DEFAULT_TTL_SECONDS = 0  # 0 = no expiry


class NodeTokenStore(Protocol):
    """Persistence interface for issued node tokens."""

    async def put(self, token_id: str, record: NodeTokenRecord) -> None: ...
    async def get(self, token_id: str) -> NodeTokenRecord | None: ...
    async def delete(self, token_id: str) -> bool: ...
    async def list_by_node(self, node_id: str) -> list[NodeTokenRecord]: ...
    async def mark_used(self, token_id: str) -> bool: ...
    async def revoke(self, token_id: str) -> bool: ...
    async def revoke_all_for_node(self, node_id: str) -> int: ...


@dataclass
class NodeTokenRecord:
    token_id: str
    node_id: str
    token_digest: str  # sha256 hex of the secret
    token_type: str = "node"  # "node" | "pairing"
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0  # 0 = no expiry
    revoked: bool = False
    used: bool = False  # pairing tokens are single-use
    display_name: str = ""
    scope: tuple[str, ...] = ()
    actor_account_key: str = ""
    conversation_id: str = ""


@dataclass
class InMemoryNodeTokenStore:
    """Default process-local token store."""

    _records: dict[str, NodeTokenRecord] = field(default_factory=dict)
    _by_node: dict[str, set[str]] = field(default_factory=dict)

    async def put(self, token_id: str, record: NodeTokenRecord) -> None:
        self._records[token_id] = record
        self._by_node.setdefault(record.node_id, set()).add(token_id)

    async def get(self, token_id: str) -> NodeTokenRecord | None:
        return self._records.get(token_id)

    async def delete(self, token_id: str) -> bool:
        rec = self._records.pop(token_id, None)
        if rec is None:
            return False
        ids = self._by_node.get(rec.node_id)
        if ids is not None:
            ids.discard(token_id)
            if not ids:
                self._by_node.pop(rec.node_id, None)
        return True

    async def list_by_node(self, node_id: str) -> list[NodeTokenRecord]:
        ids = self._by_node.get(node_id, set())
        return [self._records[t] for t in ids if t in self._records]

    async def mark_used(self, token_id: str) -> bool:
        record = self._records.get(token_id)
        if record is None or record.used or record.revoked:
            return False
        record.used = True
        return True

    async def revoke(self, token_id: str) -> bool:
        record = self._records.get(token_id)
        if record is None:
            return False
        record.revoked = True
        return True

    async def revoke_all_for_node(self, node_id: str) -> int:
        count = 0
        for record in await self.list_by_node(node_id):
            if not record.revoked:
                record.revoked = True
                count += 1
        return count


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _new_secret(prefix: str) -> tuple[str, str]:
    """Return ``(full_token, token_id)`` where full_token is shown once to the user."""
    secret = secrets.token_urlsafe(_TOKEN_BYTES)
    token_id = f"{prefix}{secrets.token_hex(6)}"
    full_token = f"{token_id}.{secret}"
    return full_token, token_id


class NodeAuthService:
    """Issues and verifies node tokens; runs the pairing flow."""

    def __init__(
        self,
        store: NodeTokenStore | None = None,
        *,
        default_ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        pairing_ttl_seconds: int = 600,
    ) -> None:
        self._store: NodeTokenStore = store or InMemoryNodeTokenStore()
        self._default_ttl = default_ttl_seconds
        self._pairing_ttl = pairing_ttl_seconds

    # -- NodeTokenVerifier protocol ---------------------------------------

    async def verify(self, token: str) -> NodePrincipal | None:
        """Verify a presented token and return the principal, or ``None``."""
        token_id = self._extract_token_id(token)
        if token_id is None:
            return None
        record = await self._store.get(token_id)
        if record is None:
            return None
        if record.revoked:
            logger.warning("node_auth.revoked_token_used", token_id=token_id)
            return None
        if not hmac.compare_digest(record.token_digest, _digest(token)):
            return None
        if record.expires_at and time.time() > record.expires_at:
            logger.warning("node_auth.expired_token_used", token_id=token_id)
            return None
        if record.token_type == "pairing":
            if record.used:
                logger.warning("node_auth.pairing_token_reused", token_id=token_id)
                return None
            if not await self._store.mark_used(token_id):
                logger.warning("node_auth.pairing_token_reused", token_id=token_id)
                return None
        return NodePrincipal(
            node_id=record.node_id,
            token_id=record.token_id,
            token_type=record.token_type,
            expires_at=str(record.expires_at) if record.expires_at else None,
            scope=record.scope,
            actor_account_key=record.actor_account_key,
            conversation_id=record.conversation_id,
        )

    # -- Issuance ----------------------------------------------------------

    async def issue_node_token(
        self,
        *,
        node_id: str,
        display_name: str = "",
        scope: tuple[str, ...] = (),
        actor_account_key: str = "",
        conversation_id: str = "",
        ttl_seconds: int | None = None,
    ) -> tuple[str, str]:
        """Issue a long-lived node token. Returns ``(full_token, token_id)``.

        ``full_token`` is shown once and must be delivered out-of-band to the
        node (or via pairing); only the digest is stored.
        """
        full_token, token_id = _new_secret(_TOKEN_PREFIX)
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        record = NodeTokenRecord(
            token_id=token_id,
            node_id=node_id,
            token_digest=_digest(full_token),
            token_type="node",
            expires_at=(time.time() + ttl) if ttl > 0 else 0.0,
            display_name=display_name,
            scope=scope,
            actor_account_key=actor_account_key,
            conversation_id=conversation_id,
        )
        await self._store.put(token_id, record)
        logger.info("node_auth.node_token_issued", node_id=node_id, token_id=token_id)
        return full_token, token_id

    async def issue_pairing_token(
        self,
        *,
        node_id: str,
        display_name: str = "",
        scope: tuple[str, ...] = (),
        actor_account_key: str = "",
        conversation_id: str = "",
    ) -> tuple[str, str]:
        """Issue a one-shot pairing token. Returns ``(full_token, token_id)``."""
        full_token, token_id = _new_secret(_PAIRING_PREFIX)
        record = NodeTokenRecord(
            token_id=token_id,
            node_id=node_id,
            token_digest=_digest(full_token),
            token_type="pairing",
            expires_at=time.time() + self._pairing_ttl,
            display_name=display_name,
            scope=scope,
            actor_account_key=actor_account_key,
            conversation_id=conversation_id,
        )
        await self._store.put(token_id, record)
        logger.info(
            "node_auth.pairing_token_issued", node_id=node_id, token_id=token_id
        )
        return full_token, token_id

    async def exchange_pairing_for_node_token(
        self, pairing_full_token: str
    ) -> tuple[str, str] | None:
        """Validate a pairing token and issue a long-lived node token.

        Returns ``(node_full_token, node_token_id)`` or ``None`` if the pairing
        token is invalid/used/expired. The pairing token is consumed on success.
        """
        principal = await self.verify(pairing_full_token)
        if principal is None or principal.token_type != "pairing":
            return None
        return await self.issue_node_token(
            node_id=principal.node_id,
            scope=principal.scope,
            actor_account_key=principal.actor_account_key,
            conversation_id=principal.conversation_id,
        )

    # -- Management --------------------------------------------------------

    async def revoke(self, token_id: str) -> bool:
        record = await self._store.get(token_id)
        if record is None:
            return False
        if not await self._store.revoke(token_id):
            return False
        logger.info(
            "node_auth.token_revoked", token_id=token_id, node_id=record.node_id
        )
        return True

    async def revoke_all_for_node(self, node_id: str) -> int:
        count = await self._store.revoke_all_for_node(node_id)
        if count:
            logger.info("node_auth.node_revoked", node_id=node_id, count=count)
        return count

    async def list_tokens(self, node_id: str) -> list[NodeTokenRecord]:
        return list(await self._store.list_by_node(node_id))

    @property
    def store(self) -> NodeTokenStore:
        return self._store

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _extract_token_id(full_token: str) -> str | None:
        if not full_token or "." not in full_token:
            return None
        token_id, _sep, _secret = full_token.partition(".")
        return token_id or None


__all__ = [
    "InMemoryNodeTokenStore",
    "NodeAuthService",
    "NodeTokenRecord",
    "NodeTokenStore",
]
