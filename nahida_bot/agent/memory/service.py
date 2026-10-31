"""Unified conversation memory reads and validated writes.

Markdown is an explicit operator export. Model context and memory tools only
consume attributed records from this service; shared files are never fallback
sources. Storage, ranking and scheduling remain separate responsibilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

import structlog

from nahida_bot.agent.memory.markdown import (
    MEMORY_FILE,
    MEMORY_SUMMARY_FILE,
    GENERATED_MEMORY_START,
    GENERATED_MEMORY_END,
    replace_generated_memory_section,
)
from nahida_bot.agent.memory.models import (
    MemoryCandidate,
    MemoryChanges,
    MemoryItem,
    MemoryRecord,
)
from nahida_bot.agent.memory.portability import (
    normalize_portable,
)
from nahida_bot.agent.memory.scope import (
    SCOPE_ID_GLOBAL,
    SCOPE_TYPE_GLOBAL,
)
from nahida_bot.agent.memory.store import StructuredMemoryStore
from nahida_bot.agent.memory.write_policy import resolve_write_sensitivity

if TYPE_CHECKING:
    from nahida_bot.core.context import SessionContext
    from nahida_bot.identity.policy import MemoryWriteRequest
    from nahida_bot.agent.retrieval.models import RetrievalResult

logger = structlog.get_logger(__name__)

#: Default item budget when (re)projecting the workspace Markdown files.
DEFAULT_PROJECTION_LIMIT = 40


async def project_workspace_memory(
    store: StructuredMemoryStore,
    workspace_root: Path,
    *,
    scope_type: str = SCOPE_TYPE_GLOBAL,
    scope_id: str = SCOPE_ID_GLOBAL,
    limit: int = DEFAULT_PROJECTION_LIMIT,
) -> None:
    """Explicit operator export for one scope, never automatic model input.

    The export filters restricted items but may still contain context-bound
    public facts. It does not confer cross-chat applicability or permission.
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
    from datetime import UTC, datetime
    from nahida_bot.agent.memory.rendering import format_memory_items

    export_header = f"Export scope: {scope_type}:{scope_id}\nExported at: {datetime.now(UTC).isoformat()}\n"
    body = format_memory_items(public_items[:limit], max_chars=1000000)
    summary = f"# Memory Summary\n\n{export_header}\n{body}\n"
    (root / MEMORY_SUMMARY_FILE).write_text(summary, encoding="utf-8")

    memory_file = root / MEMORY_FILE
    existing = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
    generated = f"{GENERATED_MEMORY_START}\n## Structured Memory\n\n{export_header}\n{body}\n{GENERATED_MEMORY_END}"
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

    async def retrieve(
        self,
        query: str,
        *,
        ctx: "SessionContext | None",
        session_id: str = "",
        limit: int = 5,
        embedding_provider: Any = None,
        vector_index: Any = None,
        fts_enabled: bool = True,
        vector_enabled: bool = False,
        hybrid_enabled: bool = True,
        automatic: bool = False,
    ) -> list[RetrievalResult]:
        """Resolve conversation policy once, then delegate ranking to retrieval."""
        from nahida_bot.agent.retrieval.adapters import MemoryStoreRetrievalAdapter
        from nahida_bot.agent.retrieval.models import RetrievalRequest, RetrievalScope

        scopes = await self.read_scopes(ctx, session_id)
        adapter = MemoryStoreRetrievalAdapter(
            memory_store=self._store,
            embedding_provider=embedding_provider,
            vector_index=vector_index,
            soft_scope=self._soft_scope,
            automatic=automatic,
            allow_empty_query=not automatic,
            account_owner=ctx.sender_account_key if ctx else "",
            chat_owner=ctx.chat_address.chat_key if ctx and ctx.chat_address else "",
        )
        results = await adapter.retrieve(
            RetrievalRequest(
                query=query,
                source_type="memory",
                limit=limit,
                scopes=tuple(RetrievalScope(*scope) for scope in scopes),
                fts_enabled=fts_enabled,
                vector_enabled=vector_enabled,
                hybrid_enabled=hybrid_enabled,
            )
        )
        if ctx and ctx.sender_account_key:
            from dataclasses import replace
            from nahida_bot.agent.memory.attribution import with_present_accounts

            matches = {
                scope: [ctx.sender_account_key]
                for scope in scopes
                if scope[0] in {"account", "person"}
            }
            matches[("account", ctx.sender_account_key)] = [ctx.sender_account_key]
            results = [
                replace(result, raw=with_present_accounts(result.raw, matches))
                if result.raw is not None
                else result
                for result in results
            ]
        return results

    async def search_items_cascade(
        self,
        query: str,
        *,
        ctx: "SessionContext | None",
        session_id: str = "",
        limit: int = 5,
    ) -> list[MemoryItem]:
        """Explicit recall uses the same cascade as automatic context."""
        results = await self.retrieve(
            query, ctx=ctx, session_id=session_id, limit=limit
        )
        return [result.raw for result in results if result.raw is not None]

    async def read_scopes(
        self, ctx: SessionContext | None, session_id: str = ""
    ) -> list[tuple[str, str]]:
        from dataclasses import replace
        from nahida_bot.identity.policy import (
            memory_read_request_from_context,
            resolve_memory_read_scopes,
        )

        request = memory_read_request_from_context(ctx, session_id)
        accounts: list[str] = []
        identity = getattr(self._store, "memory_identity", None)
        if (
            request.target_type == "private"
            and request.person_id
            and request.sender_account_key
            and callable(identity)
        ):
            person_id, accounts = await cast(Any, identity)(request.sender_account_key)
            request = replace(request, person_id=person_id)
        scopes = resolve_memory_read_scopes(request)
        if accounts:
            own = ("account", request.sender_account_key)
            position = scopes.index(own) + 1
            scopes[position:position] = [
                ("account", key)
                for key in accounts
                if key != request.sender_account_key
            ]
        return scopes

    async def context_items(
        self,
        *,
        ctx: SessionContext,
        participants: list[str] | None = None,
        category: str = "",
        query: str = "",
        limit_per_category: int = 3,
    ) -> dict[str, list[MemoryItem]]:
        """A bounded dossier of the present people/chat, not a global biography dump."""
        from nahida_bot.agent.memory.subjects import MEMORY_CATEGORIES

        catalog = getattr(self._store, "catalog_items", None)
        if (
            not callable(catalog)
            or not ctx.chat_address
            or not ctx.chat_address.is_typed
        ):
            return {}
        scopes = await self.read_scopes(ctx, ctx.session_id)
        subjects = [
            ("account", key)
            for key in dict.fromkeys(
                participants if participants is not None else [ctx.sender_account_key]
            )
            if key
        ]
        identity = getattr(self._store, "memory_identity", None)
        matches = {subject: [subject[1]] for subject in subjects}
        if callable(identity):
            for _, account in list(subjects):
                person_id, accounts = await cast(Any, identity)(account)
                if person_id:
                    subjects.append(("person", person_id))
                    subjects.extend(("account", key) for key in accounts)
                    matches[("person", person_id)] = [account]
                    for key in accounts:
                        matches[("account", key)] = [account]
        subjects.append(("chat", ctx.chat_address.chat_key))
        categories = (
            [category] if category in MEMORY_CATEGORIES else MEMORY_CATEGORIES[:-1]
        )
        result = {}
        from nahida_bot.agent.memory.attribution import with_present_accounts

        for name in categories:
            result[name] = await cast(Any, catalog)(
                scopes=scopes,
                category=name,
                subjects=subjects,
                public_recall=self._soft_scope,
                query=query,
                limit=limit_per_category,
                account_owner=ctx.sender_account_key,
                chat_owner=ctx.chat_address.chat_key,
            )
            result[name] = [
                with_present_accounts(item, matches) for item in result[name]
            ]
        return result

    def prepare_item(
        self,
        key: str,
        content: str,
        *,
        ctx: "SessionContext | None" = None,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
        write_request: MemoryWriteRequest | None = None,
    ) -> MemoryItem:
        """Prepare a mutation through the shared, pure write policy."""
        from nahida_bot.agent.memory.write_policy import prepare_memory_item

        return prepare_memory_item(
            key,
            content,
            ctx=ctx,
            session_id=session_id,
            metadata=metadata,
            write_request=write_request,
        )

    async def store_item(
        self,
        key: str,
        content: str,
        *,
        ctx: "SessionContext | None" = None,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        values = dict(metadata or {})
        evidence_ids = values.pop("evidence_turn_ids", None)
        subject_account = values.pop("subject_account_key", "")
        relationship_accounts = values.pop("relationship_accounts", None)
        # Public tool callers cite original turns; they cannot supply their own
        # provenance object. Internal consolidators use prepare_item directly.
        values.pop("attribution", None)
        if evidence_ids:
            from nahida_bot.agent.memory.attribution import attribution_for_candidate
            from nahida_bot.identity.policy import memory_read_request_from_context

            reader = getattr(self._store, "get_turns_by_ids", None)
            if not callable(reader):
                raise ValueError("evidence lookup is unavailable")
            records = await cast(Any, reader)(evidence_ids)
            chat = memory_read_request_from_context(ctx, session_id).chat_scope_id
            from nahida_bot.agent.memory.attribution import EvidenceRef

            if any(EvidenceRef.from_record(r).chat_id != chat for r in records):
                raise ValueError("memory evidence must belong to the current chat")
            from dataclasses import asdict
            from nahida_bot.agent.memory.subjects import tool_subject

            subject = asdict(
                tool_subject(
                    str(values.get("subject", "unknown")),
                    current_account=ctx.sender_account_key if ctx else "",
                    chat_key=chat,
                    account_key=subject_account,
                    relationship_accounts=relationship_accounts,
                )
            )
            values["attribution"] = attribution_for_candidate(
                {
                    "evidence_turn_ids": evidence_ids,
                    "subject": subject,
                },
                records,
            ).to_dict()
        elif (
            subject_account
            or relationship_accounts
            or values.get("subject") == "relationship"
        ):
            raise ValueError("third-party subjects require evidence_turn_ids")
        item = self.prepare_item(
            key, content, ctx=ctx, session_id=session_id, metadata=values
        )
        from nahida_bot.agent.memory.models import MemoryChanges

        changes = MemoryChanges(additions=[item])
        await self.apply_changes(changes)
        return changes.additions[0].item_id

    async def apply_changes(self, changes: MemoryChanges) -> int:
        """Use an atomic backend when available, retaining external-store support."""
        apply = getattr(self._store, "apply_memory_changes", None)
        if callable(apply):
            return await cast(Any, apply)(changes)
        from dataclasses import asdict, replace

        applied = 0
        for index, item in enumerate(changes.additions):
            fields = asdict(item)
            for key in (
                "item_id",
                "status",
                "created_at",
                "updated_at",
                "score",
                "parent_id",
                "root_id",
                "node_type",
                "path",
                "source_id",
            ):
                fields.pop(key, None)
            assigned_id = await self._store.append_item(**fields)
            changes.additions[index] = replace(item, item_id=assigned_id)
            applied += 1
        for candidate in changes.candidates:
            fields = asdict(candidate)
            fields.pop("created_at", None)
            fields.pop("updated_at", None)
            await getattr(self._store, "append_candidate")(**fields)
        for item_id in changes.archives:
            applied += int(await self._store.archive_item(item_id))
        return applied

    async def archive_item(self, item_id: str) -> bool:
        """Archive (soft-delete) a durable memory item."""
        return await self._store.archive_item(item_id)

    async def archive_item_for_context(
        self,
        item_id: str,
        *,
        ctx: "SessionContext | None",
        session_id: str = "",
    ) -> bool:
        """Archive an item only when it is visible to the current context."""
        item = await self._accessible_item(item_id, ctx=ctx, session_id=session_id)
        if item is None:
            return False
        return await self._store.archive_item(item.item_id)

    async def update_item_for_context(
        self,
        item_id: str,
        content: str,
        *,
        ctx: "SessionContext | None",
        session_id: str = "",
        key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Replace a visible item, preserving provenance.

        Durable memory is append-oriented: an update creates a replacement and
        archives the old item. This keeps a recoverable provenance chain and
        avoids mutating historical evidence in place.

        When *metadata* contains ``audience``, scope is re-resolved: ``global``
        promotes a public item to the shared global scope; ``current``
        re-resolves to the current identity/chat. Without ``audience`` the
        original scope is kept for backward-compatible in-place edits.
        """
        old = await self._accessible_item(item_id, ctx=ctx, session_id=session_id)
        if old is None or not content.strip():
            return None

        from dataclasses import replace
        from nahida_bot.agent.memory.models import MemoryChanges
        from nahida_bot.agent.memory.attribution import MemoryAttribution, EvidenceRef

        updates = dict(metadata or {})
        # Edits preserve the factual subject. Changing the current speaker or
        # moving storage scope cannot silently reassign somebody else's fact.
        updates.pop("attribution", None)
        updates.pop("subject", None)
        sensitivity, sensitivity_source = (
            resolve_write_sensitivity(updates)
            if "sensitivity" in updates
            else (old.sensitivity, old.sensitivity_source)
        )
        values = {
            **old.metadata,
            **updates,
            "kind": updates.get("kind", old.kind),
            "source": "memory_update",
            "sensitivity": sensitivity,
            "confidence": updates.get("confidence", old.confidence),
            "importance": updates.get("importance", old.importance),
            "evidence": old.evidence,
            "replaces_item_id": old.item_id,
            "updated_via": "memory_update",
        }
        target_type = str(values.pop("target_scope_type", "") or "")
        target_id = str(values.pop("target_scope_id", "") or "")
        if target_type and target_id:
            values.update(scope_type=target_type, scope_id=target_id)
        elif updates.get("audience") == "global":
            values.update(scope_type=SCOPE_TYPE_GLOBAL, scope_id=SCOPE_ID_GLOBAL)
        elif "audience" not in updates:
            values.update(scope_type=old.scope_type, scope_id=old.scope_id)
        attribution = MemoryAttribution.parse(old.metadata)
        if ctx is not None:
            values["attribution"] = MemoryAttribution(
                attribution.subject,
                (*attribution.evidence, EvidenceRef.from_context(ctx)),
            ).to_dict()
        if values.get("audience") == "global" and (
            sensitivity != "public" or not normalize_portable(values.get("portable"))
        ):
            return None
        try:
            replacement = self.prepare_item(
                key.strip() or old.title,
                content,
                ctx=ctx,
                session_id=session_id,
                metadata=values,
            )
        except ValueError:
            return None
        replacement = replace(replacement, sensitivity_source=sensitivity_source)
        changes = MemoryChanges(additions=[replacement], archives=[old.item_id])
        if callable(getattr(self._store, "apply_memory_changes", None)):
            applied = await self.apply_changes(changes)
            return replacement.item_id if applied >= 0 else None
        # External backends without batch transactions retain compensating rollback.
        additions = MemoryChanges(additions=[replacement])
        await self.apply_changes(additions)
        assigned_id = additions.additions[0].item_id
        if await self._store.archive_item(old.item_id):
            return assigned_id
        await self._store.archive_item(assigned_id)
        return None

    async def _accessible_item(
        self,
        item_id: str,
        *,
        ctx: "SessionContext | None",
        session_id: str,
    ) -> MemoryItem | None:
        if not item_id:
            return None
        items = await self._store.get_items_by_ids([item_id])
        if not items:
            return None
        item = items[0]
        # Old restricted-global rows remain operator-visible for later cleanup,
        # but are intentionally unavailable to ordinary bot recall/mutation.
        if item.scope_type == SCOPE_TYPE_GLOBAL and item.sensitivity != "public":
            return None

        allowed = set(await self.read_scopes(ctx, session_id))
        if (item.scope_type, item.scope_id) not in allowed:
            return None
        from nahida_bot.agent.memory.attribution import secret_visible_in_chat

        if not secret_visible_in_chat(
            item, ctx.chat_address.chat_key if ctx and ctx.chat_address else ""
        ):
            return None
        if (
            item.scope_type == "account"
            and ctx
            and item.scope_id != ctx.sender_account_key
            and (
                item.sensitivity == "secret_like"
                or not normalize_portable(item.metadata.get("portable"), default=True)
            )
        ):
            return None
        return item

    async def list_items(
        self,
        *,
        query: str = "",
        scope_type: str | None = SCOPE_TYPE_GLOBAL,
        scope_id: str | None = SCOPE_ID_GLOBAL,
        limit: int = 50,
        category: str = "",
    ) -> list[MemoryItem]:
        """List or search durable items with optional scope filters.

        Unfiltered by sensitivity — this is the admin/webui surface, not the
        recall path; the webui is trusted to see restricted items in its own
        workspace. ``None`` independently matches every scope type or id.
        """
        if category:
            from nahida_bot.agent.memory.subjects import MEMORY_CATEGORIES

            if category not in MEMORY_CATEGORIES:
                raise ValueError("unknown memory category")
            reader = getattr(self._store, "list_category_items", None)
            return (
                await cast(Any, reader)(
                    query,
                    category=category,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    limit=limit,
                )
                if callable(reader)
                else []
            )
        return await self._store.search_items(
            query,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )

    async def list_candidates(
        self,
        *,
        status: str | None = None,
        scope_type: str | None = SCOPE_TYPE_GLOBAL,
        scope_id: str | None = SCOPE_ID_GLOBAL,
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

    async def read_chat_turns(
        self,
        *,
        chat_address: str = "",
        session_id: str = "",
        query: str = "",
        since: Any | None = None,
        until: Any | None = None,
        before_turn_id: int | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        reader = cast(
            Callable[..., Awaitable[list[MemoryRecord]]] | None,
            getattr(self._store, "read_chat_turns", None),
        )
        if not callable(reader):
            return []
        return await reader(
            chat_address=chat_address,
            session_id=session_id,
            query=query,
            since=since,
            until=until,
            before_turn_id=before_turn_id,
            limit=limit,
        )

    async def find_turn_by_message_id(
        self,
        message_id: str,
        *,
        chat_address: str = "",
        session_id: str = "",
    ) -> MemoryRecord | None:
        finder = cast(
            Callable[..., Awaitable[MemoryRecord | None]] | None,
            getattr(self._store, "find_turn_by_message_id", None),
        )
        if not callable(finder):
            return None
        return await finder(
            message_id,
            chat_address=chat_address,
            session_id=session_id,
        )

    async def read_turns_around(
        self,
        anchor_turn_id: int,
        *,
        chat_address: str = "",
        session_id: str = "",
        before: int = 5,
        after: int = 5,
    ) -> list[MemoryRecord]:
        reader = cast(
            Callable[..., Awaitable[list[MemoryRecord]]] | None,
            getattr(self._store, "read_turns_around", None),
        )
        if not callable(reader):
            return []
        return await reader(
            anchor_turn_id,
            chat_address=chat_address,
            session_id=session_id,
            before=before,
            after=after,
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
