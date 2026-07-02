"""Unified memory service — the single consumer-facing entry over the durable
memory store.

Both the agent (tools, plugin SDK, the ``/memory`` command via ``api_bridge``)
and the gateway REST API (webui) call this one service. The durable SQLite store
is the single source of truth; the workspace Markdown projection
(``MEMORY.md`` / ``memory_summary.md``) is a **derived, sensitivity-filtered,
read-only** surface that lets the agent self-service recall via generic file
tools (grep / rg) without leaking restricted items across scopes.

See ``docs/design/memory-simplification-proposal.md`` (Path B): database as the
sole writable source, Markdown kept as a grep-fallback projection that applies
the same scope/sensitivity boundary as retrieval.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.agent.memory.markdown import (
    MEMORY_FILE,
    MEMORY_SUMMARY_FILE,
    build_memory_projection,
    build_memory_summary,
    replace_generated_memory_section,
)
from nahida_bot.agent.memory.models import (
    MemoryCandidate,
    MemoryItem,
    MemoryRecord,
    Sensitivity,
    SensitivitySource,
    normalize_sensitivity,
)
from nahida_bot.agent.memory.scope import (
    SCOPE_ID_GLOBAL,
    SCOPE_TYPE_GLOBAL,
)
from nahida_bot.agent.memory.store import StructuredMemoryStore

if TYPE_CHECKING:
    from nahida_bot.core.context import SessionContext

logger = structlog.get_logger(__name__)

#: Default item budget when (re)projecting the workspace Markdown files.
DEFAULT_PROJECTION_LIMIT = 40


def resolve_write_sensitivity(
    metadata: dict[str, Any],
) -> tuple[Sensitivity, SensitivitySource]:
    """Pop and validate ``sensitivity`` from a consumer write's metadata.

    Only an explicit RESTRICTION (``private``/``secret_like``) earns
    ``sensitivity_source='explicit'`` (explicit > dream). The soft ``public``
    baseline — whether omitted, explicitly passed, or a fallback for an
    unrecognized value — gets ``'default'`` provenance, matching the
    consolidation default so consumer writes and dreaming agree. The shared
    normalizer handles casing/typos so they can't defeat the SQL filter; an
    unrecognized value is a no-op (public/default) rather than a rejected write.
    """
    raw = metadata.pop("sensitivity", None)
    canonical = normalize_sensitivity(raw)
    if canonical in {"private", "secret_like"}:
        return canonical, "explicit"
    return "public", "default"


async def project_workspace_memory(
    store: StructuredMemoryStore,
    workspace_root: Path,
    *,
    scope_type: str = SCOPE_TYPE_GLOBAL,
    scope_id: str = SCOPE_ID_GLOBAL,
    limit: int = DEFAULT_PROJECTION_LIMIT,
) -> None:
    """Regenerate the workspace Markdown projection from durable memory items.

    The database is the single source of truth; these files are a **derived
    read-only projection**. Only ``sensitivity='public'`` items are written, so
    the grep-fallback recall path can never leak private/secret_like items into
    another chat — the leak class fixed in ``96860d7`` is structurally impossible
    once the projection is filtered at the source. Restricted items stay in the
    DB, reachable only via the sensitivity-filtered retrieval/service reads.

    This is the single implementation of the projection algorithm; both
    :class:`MemoryService` (the consumer/REST entry) and the consolidator (which
    re-projects after applying new items) call it, so the filter lives in one
    place.
    """
    try:
        items = await store.list_public_items(
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
    except Exception as exc:  # pragma: no cover - defensive, store is local
        logger.warning("memory.projection_search_failed", error=str(exc))
        return

    # ``list_public_items`` already filters at the SQL layer (before the LIMIT,
    # so restricted items can't starve public ones out of the budget). This
    # Python filter is a defensive invariant guard at the leak boundary — a
    # no-op for the SQLite store, but fails closed if a backend ever forgets.
    public_items = [item for item in items if item.sensitivity == "public"]

    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    summary = build_memory_summary(public_items, max_items=limit)
    (root / MEMORY_SUMMARY_FILE).write_text(summary, encoding="utf-8")

    memory_file = root / MEMORY_FILE
    existing = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
    generated = build_memory_projection(public_items, max_items=limit)
    memory_file.write_text(
        replace_generated_memory_section(existing, generated),
        encoding="utf-8",
    )


class MemoryService:
    """Unified consumer-facing memory service over the durable store.

    Owns the read-cascade + soft-scope policy and the write policy so the agent
    (tools / plugin SDK / ``/memory`` command) and the gateway REST API share a
    single implementation. The store is the single writable source; this service
    mediates every consumer read/write and produces the filtered Markdown
    projection.
    """

    def __init__(
        self,
        store: StructuredMemoryStore,
        *,
        soft_scope: bool = False,
        projection_limit: int = DEFAULT_PROJECTION_LIMIT,
    ) -> None:
        self._store = store
        self._soft_scope = soft_scope
        self._projection_limit = projection_limit

    @property
    def store(self) -> StructuredMemoryStore:
        """The backing structured store (single source of truth)."""
        return self._store

    @property
    def soft_scope(self) -> bool:
        """Whether cross-scope public recall supplements the read cascade."""
        return self._soft_scope

    async def search_items_cascade(
        self,
        query: str,
        *,
        ctx: "SessionContext | None",
        session_id: str = "",
        limit: int = 5,
    ) -> list[MemoryItem]:
        """Identity-aware read cascade with optional soft-scope public recall.

        Resolves the ordered scope cascade (person -> account -> chat -> global
        via ``identity.policy``; collapses to V1 chat -> global or global-only
        when identity is off) and fills the budget in priority order, deduped by
        ``item_id``.

        When ``soft_scope`` is on and budget remains, a supplementary global pass
        admits ONLY ``sensitivity='public'`` items from outside the cascade
        (Piece A2). Restricted items never leave the store — the public filter is
        enforced in SQL — so this round can only add cross-scope public recall,
        never leak.

        Returns raw :class:`MemoryItem` objects; callers project to their own
        shape (``MemoryRef`` for the SDK, ``RetrievalResult`` for retrieval,
        Pydantic for REST).
        """
        # Lazy import keeps the memory package free of an identity dependency at
        # import time (identity imports memory.scope; the reverse direction must
        # stay deferred to avoid a cycle).
        from nahida_bot.identity.policy import (
            memory_read_request_from_context,
            resolve_memory_read_scopes,
        )

        read_request = memory_read_request_from_context(ctx, session_id)
        scopes = resolve_memory_read_scopes(read_request)

        items: list[MemoryItem] = []
        seen: set[str] = set()
        remaining = max(limit, 0)
        for scope_type, scope_id in scopes:
            if remaining <= 0:
                break
            scoped = list(
                await self._store.search_items(
                    query,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    limit=remaining,
                )
            )
            for item in scoped:
                if item.item_id in seen:
                    continue
                seen.add(item.item_id)
                items.append(item)
                remaining -= 1
                if remaining <= 0:
                    break

        if self._soft_scope and remaining > 0:
            # Over-fetch by len(seen): the public pool includes in-scope public
            # items already returned, so fetching only ``remaining`` can starve
            # cross-scope recall (the pool's top hits may all dedupe against
            # ``seen``). ``remaining + len(seen)`` guarantees enough headroom.
            public_items = list(
                await self._store.search_items_public_all_scopes(
                    query,
                    limit=remaining + len(seen),
                )
            )
            for item in public_items:
                if not item.item_id or item.item_id in seen:
                    continue
                seen.add(item.item_id)
                items.append(item)
                remaining -= 1
                if remaining <= 0:
                    break
        return items

    async def store_item(
        self,
        key: str,
        content: str,
        *,
        ctx: "SessionContext | None" = None,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Write a durable memory item.

        Scope is resolved identity-aware, matching the consolidator (issue #7,
        Phase 3): personal kinds (``preference``/``fact``/``task``) follow the
        sender (``person`` -> ``account`` -> ``chat``), global kinds stay global.
        An explicit ``scope_type`` + ``scope_id`` pair in ``metadata`` overrides
        identity routing. Applies the sensitivity policy
        (see :func:`resolve_write_sensitivity`) and returns the new item id.
        Extra ``metadata`` keys are forwarded as item fields (``kind``,
        ``source``, ``confidence``, ``importance``, ``evidence``) or stored as
        the item's metadata blob.
        """
        metadata = dict(metadata or {})
        kind = str(metadata.pop("kind", "fact"))
        scope_type_value = metadata.pop("scope_type", None)
        scope_id_value = metadata.pop("scope_id", None)
        if scope_type_value is None or scope_id_value is None:
            # Lazy import keeps the memory package free of an identity import at
            # module load (identity imports memory.scope).
            from nahida_bot.identity.policy import (
                memory_write_request_from_context,
                resolve_memory_write_scope,
            )

            write_req = memory_write_request_from_context(ctx, session_id)
            scope_type_value, scope_id_value = resolve_memory_write_scope(
                write_req, kind
            )
        sensitivity_value, sensitivity_source = resolve_write_sensitivity(metadata)
        return await self._store.append_item(
            title=key,
            content=content,
            scope_type=str(scope_type_value),
            scope_id=str(scope_id_value),
            kind=kind,
            source=str(metadata.pop("source", "plugin")),
            confidence=float(metadata.pop("confidence", 1.0)),
            importance=float(metadata.pop("importance", 0.5)),
            sensitivity=sensitivity_value,
            sensitivity_source=sensitivity_source,
            evidence=metadata.pop("evidence", None),
            metadata=metadata,
        )

    async def archive_item(self, item_id: str) -> bool:
        """Archive (soft-delete) a durable memory item."""
        return await self._store.archive_item(item_id)

    async def list_items(
        self,
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 50,
    ) -> list[MemoryItem]:
        """List durable items in a scope (most recently updated first).

        Unfiltered by sensitivity — this is the admin/webui surface, not the
        recall path; the webui is trusted to see restricted items in its own
        workspace.
        """
        return await self._store.search_items(
            "",
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )

    async def list_candidates(
        self,
        *,
        status: str | None = None,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 20,
    ) -> list[MemoryCandidate]:
        """List consolidation candidates (the dreaming audit ledger)."""
        return await self._store.list_candidates(
            status=status,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )

    async def search_turns(
        self,
        query: str = "",
        *,
        chat_address: str = "",
        source: str = "",
        role: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """Cross-session raw-turn search (admin/debug view)."""
        return await self._store.search_turns(
            query,
            chat_address=chat_address,
            source=source,
            role=role,
            limit=limit,
        )

    async def project_workspace_memory(
        self,
        workspace_root: Path,
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
    ) -> None:
        """Regenerate the filtered Markdown projection (see module function)."""
        await project_workspace_memory(
            self._store,
            workspace_root,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=self._projection_limit,
        )
