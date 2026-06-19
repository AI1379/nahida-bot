"""Knowledge Base builtin plugin.

Provides document import, search, and management commands backed by the
generic ``DocumentStore`` / ``DocumentStoreManager`` from ``agent.storage``.

v1 features:
- ``/kb import <collection> <path>`` — import Markdown or text files
- ``/kb import-text <collection> <title> <text>`` — import raw text
- ``/kb list`` — list all collections
- ``/kb search <collection> <query>`` — manual search
- ``/kb delete <collection>`` — delete a collection
- ``kb_search`` tool — agent-callable knowledge search
- Static PromptSupplement telling the agent about available collections
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any, cast

import structlog

from nahida_bot.agent.retrieval import (
    DocumentStoreRetrievalAdapter,
    RetrievalRequest,
    RetrievalService,
)
from nahida_bot.agent.storage.embedding import RoutedEmbeddingProvider
from nahida_bot.agent.storage.vector import SQLiteVecIndex
from nahida_bot.plugins.base import (
    CommandHandlerResult,
    InboundMessage,
    Plugin,
)
from nahida_bot.plugins.knowledge_base.config import parse_kb_config
from nahida_bot.plugins.knowledge_base.ingestion import import_document

logger = structlog.get_logger(__name__)

_PLUGIN_DATA_KEY = "kb_collections"
_COLLECTION_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

# ---------------------------------------------------------------------------
# JSON Schema for the kb_search tool
# ---------------------------------------------------------------------------

_KB_SEARCH_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query to find relevant knowledge documents.",
        },
        "collection": {
            "type": "string",
            "description": (
                "Knowledge base collection name. "
                "If omitted, searches all available collections."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of results to return (default: 5).",
            "default": 5,
        },
    },
    "required": ["query"],
}


class KnowledgeBasePlugin(Plugin):
    """Builtin plugin for importing and searching knowledge documents."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._config = parse_kb_config(self.manifest.config)
        self._manager: Any = None  # DocumentStoreManager — set in on_load
        self._embedding_provider: Any | None = None
        self._vector_indexes: dict[str, Any | None] = {}
        self._embedded_collections: set[str] = set()
        self._embedding_dimensions = self._config.embedding.dimensions

    # ── Lifecycle ────────────────────────────────────────

    async def on_load(self) -> None:
        if not self._config.enabled:
            self.api.logger.info("kb.disabled")
            return

        self._manager = self.api.get_document_store_manager()
        if self._manager is None:
            self.api.logger.warning("kb.no_manager")
            return

        self._embedding_provider = self._resolve_embedding_provider()

        # Re-register collections persisted in plugin_data.
        await self._restore_collections()

        self._register_tool()
        self._register_command()
        await self._register_supplement()

        self.api.logger.info(
            "kb.loaded",
            collections=self._manager.list_collections(),
        )

    async def on_disable(self) -> None:
        # DocumentStoreManager owns the stores — nothing to close here.
        pass

    # ── Tool Registration ────────────────────────────────

    def _register_tool(self) -> None:
        self.api.register_tool(
            "kb_search",
            (
                "Search the knowledge base for relevant documents. "
                "Use this tool when the user asks about topics that might be "
                "covered in imported knowledge documents, such as documentation, "
                "reference material, or domain-specific knowledge."
            ),
            _KB_SEARCH_TOOL_SCHEMA,
            self._handle_kb_search,
        )

    async def _handle_kb_search(self, **kwargs: Any) -> str:
        """Handle the ``kb_search`` tool invocation."""
        query = kwargs.get("query", "")
        collection = kwargs.get("collection")
        limit = min(
            int(kwargs.get("limit", self._config.max_search_results)),
            self._config.max_search_results,
        )

        if not query:
            return "Error: query is required."

        manager = self._manager
        if manager is None:
            return "Error: knowledge base is not available."

        results: list[dict[str, Any]] = []

        if collection:
            try:
                hits = await self.search_documents(collection, query, limit=limit)
            except LookupError:
                return f"Error: collection '{collection}' not found."
            results = [
                {
                    "collection": collection,
                    "doc_id": r.doc_id,
                    "title": r.title,
                    "content": r.content,
                    "score": r.score,
                    "path": getattr(r, "path", ""),
                    "source_id": getattr(r, "source_id", ""),
                    "chunk_index": getattr(r, "chunk_index", 0),
                }
                for r in hits
            ]
        else:
            ranked_results: list[tuple[float, dict[str, Any]]] = []
            for name in manager.list_collections():
                try:
                    hits = await self.search_documents(name, query, limit=limit)
                except LookupError:
                    continue
                for rank, result in enumerate(hits, start=1):
                    ranked_results.append(
                        (
                            1.0 / (60.0 + rank),
                            {
                                "collection": name,
                                "doc_id": result.doc_id,
                                "title": result.title,
                                "content": result.content,
                                "score": result.score,
                                "path": getattr(result, "path", ""),
                                "source_id": getattr(result, "source_id", ""),
                                "chunk_index": getattr(result, "chunk_index", 0),
                            },
                        )
                    )
            ranked_results.sort(key=lambda item: item[0], reverse=True)
            results = [payload for _rank_score, payload in ranked_results[:limit]]

        if not results:
            return "No relevant documents found."

        lines = ["Found relevant knowledge documents:"]
        for r in results:
            source = r.get("collection", "")
            title = r.get("title", "")
            content = r.get("content", "")
            lines.append(f"\n--- [{source}] {title} ---")
            lines.append(content)
        return "\n".join(lines)

    # ── Command Registration ─────────────────────────────

    def _register_command(self) -> None:
        self.api.register_command(
            "kb",
            self._handle_kb_command,
            description="Knowledge base management",
        )

    async def _handle_kb_command(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> CommandHandlerResult:
        """Dispatch ``/kb`` subcommands."""
        parts = args.strip().split(None, 1)
        if not parts:
            return self._kb_help()

        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        dispatch = {
            "import": self._cmd_import_file,
            "import-text": self._cmd_import_text,
            "list": self._cmd_list,
            "search": self._cmd_search,
            "delete": self._cmd_delete,
            "info": self._cmd_info,
            "help": lambda **kw: self._kb_help(),
        }

        handler = dispatch.get(subcmd)
        if handler is None:
            return (
                f"Unknown subcommand '{subcmd}'. "
                f"Available: {', '.join(dispatch.keys())}"
            )

        result = handler(args=rest, inbound=inbound, session_id=session_id)
        if hasattr(result, "__await__"):
            return await result
        return result  # type: ignore[return-value]

    def _kb_help(self) -> str:
        return (
            "Knowledge Base commands:\n"
            "  /kb import <collection> <file_path>  — Import a file into a collection\n"
            "  /kb import-text <collection> <title>|<text>  — Import raw text\n"
            "  /kb list  — List all collections\n"
            "  /kb search <collection> <query>  — Search a collection\n"
            "  /kb delete <collection>  — Delete a collection\n"
            "  /kb info <collection>  — Show collection details"
        )

    # ── Subcommand handlers ──────────────────────────────

    async def _cmd_import_file(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> str:
        """``/kb import <collection> <file_path>``"""
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            return "Usage: /kb import <collection> <file_path>"

        collection_name = parts[0]
        file_path = parts[1].strip()

        # Resolve path relative to workspace.
        resolved = self.api.resolve_workspace_path(file_path)
        if not resolved or not os.path.isfile(resolved):
            return f"File not found: {file_path}"

        try:
            content = await self.api.workspace_read(file_path)
        except Exception as exc:
            return f"Error reading file: {exc}"

        # Determine content type from extension.
        ext = os.path.splitext(file_path)[1].lower()
        content_type = "markdown" if ext in (".md", ".markdown") else "text"

        source_id = os.path.splitext(os.path.basename(file_path))[0]
        try:
            count = await self.import_content(
                collection_name,
                source_id=source_id,
                content=content,
                content_type=content_type,
                extra_metadata={
                    "file_path": file_path,
                    "imported_at": datetime.now(UTC).isoformat(),
                },
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            return str(exc)

        return (
            f"Imported '{source_id}' into '{collection_name}': {count} chunks created."
        )

    async def _cmd_import_text(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> str:
        """``/kb import-text <collection> <title>|<text>``"""
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            return "Usage: /kb import-text <collection> <title>|<text>"

        collection_name = parts[0]
        rest = parts[1].strip()

        # Split title and text by first pipe.
        if "|" in rest:
            title, text = rest.split("|", 1)
            title = title.strip()
            text = text.strip()
        else:
            title = "Untitled"
            text = rest

        if not text:
            return "Error: text content is empty."

        try:
            count = await self.import_content(
                collection_name,
                source_id=title,
                content=text,
                content_type="text",
                extra_metadata={"imported_at": datetime.now(UTC).isoformat()},
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            return str(exc)

        return f"Imported '{title}' into '{collection_name}': {count} chunks created."

    async def _cmd_list(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> str:
        """``/kb list``"""
        try:
            collections = await self.list_collection_summaries()
        except RuntimeError as exc:
            return str(exc)

        if not collections:
            return "No knowledge base collections. Use /kb import to create one."

        lines = ["Knowledge base collections:"]
        for summary in collections:
            lines.append(
                f"  • {summary['name']} ({summary['document_count']} documents)"
            )

        return "\n".join(lines)

    async def _cmd_search(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> str:
        """``/kb search <collection> <query>``"""
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            return "Usage: /kb search <collection> <query>"

        collection_name = parts[0]
        query = parts[1].strip()

        try:
            results = await self.search_documents(
                collection_name,
                query,
                limit=self._config.max_search_results,
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            return str(exc)

        if not results:
            return f"No results in '{collection_name}' for: {query}"

        lines = [f"Search results from '{collection_name}':"]
        for r in results:
            path_info = f" [{getattr(r, 'path', '')}]" if getattr(r, "path", "") else ""
            lines.append(
                f"\n  [{r.doc_id}] {r.title}{path_info} (score: {r.score:.4f})"
            )
            # Truncate content for display.
            snippet = r.content[:200] + "..." if len(r.content) > 200 else r.content
            lines.append(f"  {snippet}")
        return "\n".join(lines)

    async def _cmd_delete(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> str:
        """``/kb delete <collection>``"""
        collection_name = args.strip()
        if not collection_name:
            return "Usage: /kb delete <collection>"

        try:
            await self.delete_collection(collection_name)
        except (LookupError, RuntimeError, ValueError) as exc:
            return str(exc)

        return f"Deleted collection '{collection_name}' and all its documents."

    async def _cmd_info(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> str:
        """``/kb info <collection>``"""
        collection_name = args.strip()
        if not collection_name:
            return "Usage: /kb info <collection>"

        try:
            summary = await self.get_collection_summary(collection_name)
        except (LookupError, RuntimeError, ValueError) as exc:
            return str(exc)

        lines = [f"Collection: {collection_name}"]
        lines.append(f"  Documents: {summary['document_count']}")
        if summary["created_at"]:
            lines.append(f"  Created: {summary['created_at']}")
        return "\n".join(lines)

    # ── Public KB operations ─────────────────────────────

    async def list_collection_summaries(self) -> list[dict[str, Any]]:
        """Return collection summaries for API and command consumers."""
        manager = self._require_manager()
        meta = await self._load_collections_meta()
        summaries: list[dict[str, Any]] = []
        for name in sorted(manager.list_collections()):
            store = manager.get(name)
            doc_count = await store.count() if store is not None else 0
            summaries.append(
                {
                    "name": name,
                    "document_count": doc_count,
                    "created_at": str(meta.get(name, {}).get("created_at", "")),
                }
            )
        return summaries

    async def get_collection_summary(self, collection_name: str) -> dict[str, Any]:
        """Return one collection summary or raise if it does not exist."""
        collection_name = self._validate_collection_name(collection_name)
        manager = self._require_manager()
        store = manager.get(collection_name)
        if store is None:
            raise LookupError(f"Collection '{collection_name}' not found.")

        meta = await self._load_collections_meta()
        return {
            "name": collection_name,
            "document_count": await store.count(),
            "created_at": str(meta.get(collection_name, {}).get("created_at", "")),
        }

    async def create_collection(self, collection_name: str) -> None:
        """Create an empty collection and persist its metadata."""
        collection_name = self._validate_collection_name(collection_name)
        manager = self._require_manager()
        try:
            await manager.create(collection_name)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        await self._persist_collection_meta(collection_name)
        await self._refresh_supplement()

    async def import_content(
        self,
        collection_name: str,
        *,
        source_id: str,
        content: str,
        content_type: str = "text",
        extra_metadata: dict[str, Any] | None = None,
    ) -> int:
        """Import content into a collection, creating it when needed."""
        collection_name = self._validate_collection_name(collection_name)
        manager = self._require_manager()
        store = await manager.get_or_create(collection_name)
        count = await import_document(
            store,
            source_id=source_id.strip() or "Untitled",
            content=content,
            content_type=content_type,
            chunk_size=self._config.default_chunk_size,
            chunk_overlap=self._config.default_chunk_overlap,
            extra_metadata=extra_metadata,
        )
        await self._persist_collection_meta(collection_name)
        await self._refresh_supplement()
        await self._refresh_embeddings_after_import(
            collection_name,
            store,
            imported_count=count,
        )
        return count

    async def search_documents(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int = 5,
    ) -> list[Any]:
        """Search one collection and return raw search results."""
        collection_name = self._validate_collection_name(collection_name)
        manager = self._require_manager()
        store = manager.get(collection_name)
        if store is None:
            raise LookupError(f"Collection '{collection_name}' not found.")
        query = query.strip()
        if not query:
            return []
        search_limit = min(max(1, int(limit)), self._config.max_search_results)
        return await self._search_store(
            collection_name,
            store,
            query,
            limit=search_limit,
        )

    async def delete_collection(self, collection_name: str) -> None:
        """Delete a collection, its documents, and persisted metadata."""
        collection_name = self._validate_collection_name(collection_name)
        manager = self._require_manager()
        deleted = await manager.delete_collection(collection_name)
        if not deleted:
            raise LookupError(f"Collection '{collection_name}' not found.")

        await self._drop_vector_index(collection_name)
        await self._remove_collection_meta(collection_name)
        self._embedded_collections.discard(collection_name)
        await self._refresh_supplement()

    # ── Prompt Supplement ────────────────────────────────

    async def _register_supplement(self) -> None:
        await self._update_supplement()

    async def _refresh_supplement(self) -> None:
        """Re-register the supplement with updated collection list."""
        await self._update_supplement()

    async def _update_supplement(self) -> None:
        if self._manager is None:
            return

        summaries = await self.list_collection_summaries()
        if not summaries:
            instruction = (
                "You have access to a knowledge base system. "
                "When the user asks about topics that might be covered in "
                "imported documents, use the `kb_search` tool to find relevant "
                "information before answering. No collections are currently loaded."
            )
        else:
            lines: list[str] = []
            for s in summaries:
                lines.append(f"  • {s['name']} ({s['document_count']} documents)")
            collection_lines = "\n".join(lines)
            instruction = (
                "You have access to a knowledge base system with the following "
                f"collections:\n{collection_lines}\n"
                "When the user asks about topics that might be covered in imported "
                "documents, use the `kb_search` tool to search for relevant "
                "information before answering."
            )

        self.api.unregister_prompt_supplement("kb_context")
        self.api.register_prompt_supplement(
            "kb_context",
            instruction,
        )

    # ── Collection Persistence ──────────────────────────

    def _require_manager(self) -> Any:
        manager = self._manager
        if manager is None:
            raise RuntimeError("Knowledge base is not available.")
        return manager

    @staticmethod
    def _validate_collection_name(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Collection name is required.")
        if not _COLLECTION_NAME_RE.fullmatch(normalized):
            raise ValueError(
                "Collection name must contain only letters, digits, and underscores."
            )
        return normalized

    async def _restore_collections(self) -> None:
        """Re-create DocumentStore instances for persisted collection names."""
        meta = await self._load_collections_meta()
        if not meta or self._manager is None:
            return
        for name in meta:
            try:
                await self._manager.get_or_create(name)
                self.api.logger.debug("kb.restored_collection", collection=name)
            except Exception as exc:
                self.api.logger.warning(
                    "kb.restore_failed",
                    collection=name,
                    error=str(exc),
                )

    async def _load_collections_meta(self) -> dict[str, Any]:
        """Load collection metadata from plugin_data."""
        data = await self.api.plugin_data_get(_PLUGIN_DATA_KEY)
        if isinstance(data, dict):
            return data
        return {}

    async def _save_collections_meta(self, meta: dict[str, Any]) -> None:
        """Save collection metadata to plugin_data."""
        await self.api.plugin_data_set(_PLUGIN_DATA_KEY, meta)

    async def _persist_collection_meta(self, name: str) -> None:
        """Add a collection to the persisted metadata."""
        meta = await self._load_collections_meta()
        if name not in meta:
            meta[name] = {
                "created_at": datetime.now(UTC).isoformat(),
            }
            await self._save_collections_meta(meta)

    async def _remove_collection_meta(self, name: str) -> None:
        """Remove a collection from the persisted metadata."""
        meta = await self._load_collections_meta()
        meta.pop(name, None)
        await self._save_collections_meta(meta)

    def _resolve_embedding_provider(self) -> Any | None:
        """Resolve the embedding provider configured for KB retrieval."""
        if not self._config.embedding.enabled:
            return None

        get_model_router = getattr(self.api, "get_model_router", None)
        model_router = get_model_router() if callable(get_model_router) else None
        if model_router is None:
            self.api.logger.warning(
                "kb.embedding_disabled",
                reason="no_model_router",
            )
            return None

        routed = cast(Any, model_router).resolve_for_task(
            "embedding",
            explicit=self._config.embedding.model.strip(),
            default_spec="embedding",
            fallback="disabled",
        )
        if routed is None:
            self.api.logger.warning(
                "kb.embedding_disabled",
                reason="no_embedding_model",
                explicit=self._config.embedding.model,
            )
            return None

        model_name = routed.model or routed.slot.default_model
        embed = getattr(routed.slot.provider, "embed_texts", None)
        if not callable(embed):
            self.api.logger.warning(
                "kb.embedding_disabled",
                reason="provider_without_embeddings",
                provider_id=routed.slot.id,
                model=model_name,
            )
            return None

        provider = RoutedEmbeddingProvider(
            routed.slot.provider,
            provider_id=routed.slot.id,
            model=model_name,
            dimensions=self._config.embedding.dimensions,
            batch_size=self._config.embedding.batch_size,
        )
        self.api.logger.info(
            "kb.embedding_initialized",
            provider_id=routed.slot.id,
            model=model_name,
            reason=routed.reason,
            vector_backend=(
                self._config.retrieval.vector_backend
                if self._config.retrieval.vector_enabled
                else "none"
            ),
        )
        return provider

    async def _search_store(
        self,
        collection_name: str,
        store: Any,
        query: str,
        *,
        limit: int,
    ) -> list[Any]:
        """Search one collection using the configured retrieval mode."""
        adapter = DocumentStoreRetrievalAdapter(
            collection_name=collection_name,
            store=store,
            ensure_vector_ready=lambda: self._ensure_vector_search_ready(
                collection_name,
                store,
            ),
            logger=self.api.logger,
            vector_failure_event="kb.vector_search_failed",
            expand_neighbors=self._config.retrieval.expand_neighbors,
            expand_neighbors_top_k=self._config.retrieval.expand_neighbors_top_k,
        )
        service = RetrievalService({"knowledge_base": adapter})
        results = await service.retrieve(
            RetrievalRequest(
                query=query,
                source_type="knowledge_base",
                collection=collection_name,
                limit=limit,
                fts_enabled=self._config.retrieval.fts_enabled,
                vector_enabled=(
                    self._config.retrieval.vector_enabled
                    and self._config.embedding.enabled
                ),
                hybrid_enabled=self._config.retrieval.hybrid_enabled,
            )
        )
        return [result.raw for result in results if result.raw is not None]

    async def _ensure_vector_search_ready(
        self,
        collection_name: str,
        store: Any,
    ) -> tuple[Any | None, Any | None]:
        """Prepare embeddings and optional vector index for one collection."""
        provider = self._embedding_provider
        if provider is None:
            provider = self._resolve_embedding_provider()
            self._embedding_provider = provider
        if provider is None:
            return None, None

        vector_index = await self._get_vector_index(collection_name, provider)
        if collection_name in self._embedded_collections:
            return provider, vector_index

        try:
            total_docs = await store.count()
            result = await store.embed_documents(
                provider,
                limit=total_docs,
                vector_index=vector_index,
            )
            complete = result.added == result.needed
            if complete:
                self._embedded_collections.add(collection_name)
            else:
                self._embedded_collections.discard(collection_name)
            self.api.logger.debug(
                "kb.embeddings_backfilled",
                collection=collection_name,
                documents=total_docs,
                embedded=result.added,
                needed=result.needed,
                complete=complete,
            )
        except Exception as exc:
            self.api.logger.warning(
                "kb.embedding_backfill_failed",
                collection=collection_name,
                error=str(exc),
            )
        return provider, vector_index

    async def _refresh_embeddings_after_import(
        self,
        collection_name: str,
        store: Any,
        *,
        imported_count: int,
    ) -> None:
        """Refresh embeddings for freshly imported documents when configured."""
        if imported_count <= 0:
            return
        if not self._config.embedding.enabled:
            self._embedded_collections.discard(collection_name)
            return
        if not self._config.embedding.embed_after_import:
            self._embedded_collections.discard(collection_name)
            return

        provider = self._embedding_provider
        if provider is None:
            provider = self._resolve_embedding_provider()
            self._embedding_provider = provider
        if provider is None:
            self._embedded_collections.discard(collection_name)
            return

        vector_index = await self._get_vector_index(collection_name, provider)
        try:
            result = await store.embed_documents(
                provider,
                limit=max(1, imported_count),
                vector_index=vector_index,
            )
            total_docs = await store.count()
            if result.added < result.needed:
                self._embedded_collections.discard(collection_name)
            elif total_docs <= imported_count:
                # Import batch covered the whole collection and every pending
                # document in it was embedded; safe to mark complete.
                self._embedded_collections.add(collection_name)
            self.api.logger.debug(
                "kb.embeddings_refreshed",
                collection=collection_name,
                imported=imported_count,
                embedded=result.added,
                needed=result.needed,
                complete=collection_name in self._embedded_collections,
            )
        except Exception as exc:
            self._embedded_collections.discard(collection_name)
            self.api.logger.warning(
                "kb.embedding_refresh_failed",
                collection=collection_name,
                error=str(exc),
            )

    async def _get_vector_index(
        self,
        collection_name: str,
        provider: Any,
    ) -> Any | None:
        """Return the optional vector index for a collection."""
        if (
            not self._config.retrieval.vector_enabled
            or self._config.retrieval.vector_backend != "sqlite-vec"
        ):
            return None
        if collection_name in self._vector_indexes:
            return self._vector_indexes[collection_name]

        manager = self._require_manager()
        dimensions = await self._resolve_embedding_dimensions(provider)
        if dimensions <= 0:
            self.api.logger.warning(
                "kb.vector_index_disabled",
                collection=collection_name,
                reason="sqlite_vec_requires_dimensions",
            )
            self._vector_indexes[collection_name] = None
            return None

        index = SQLiteVecIndex(
            manager.engine,
            dimensions=dimensions,
            table_name=f"kb_{collection_name}_embedding_vec",
            map_table=f"kb_{collection_name}_vec_map",
        )
        try:
            await index.setup()
        except Exception as exc:
            self.api.logger.warning(
                "kb.vector_index_disabled",
                collection=collection_name,
                reason="setup_failed",
                error=str(exc),
            )
            self._vector_indexes[collection_name] = None
            return None

        self._vector_indexes[collection_name] = index
        return index

    async def _resolve_embedding_dimensions(self, provider: Any) -> int:
        """Resolve the embedding dimension for optional sqlite-vec indexes."""
        if self._embedding_dimensions > 0:
            return self._embedding_dimensions
        provider_dimensions = int(getattr(provider, "dimensions", 0) or 0)
        if provider_dimensions > 0:
            self._embedding_dimensions = provider_dimensions
            return provider_dimensions
        try:
            probe = await provider.embed_texts(["0"])
        except Exception as exc:
            self.api.logger.warning(
                "kb.embedding_probe_failed",
                error=str(exc),
            )
            return 0
        dimensions = len(probe[0].embedding) if probe and probe[0].embedding else 0
        if dimensions > 0:
            self._embedding_dimensions = dimensions
        return dimensions

    async def _drop_vector_index(self, collection_name: str) -> None:
        """Drop the optional vector index tables for a deleted collection."""
        vector_index = self._vector_indexes.pop(collection_name, None)
        if vector_index is None:
            manager = self._require_manager()
            engine = getattr(manager, "engine", None)
            if engine is None:
                return
            vector_index = SQLiteVecIndex(
                engine,
                dimensions=max(1, self._embedding_dimensions),
                table_name=f"kb_{collection_name}_embedding_vec",
                map_table=f"kb_{collection_name}_vec_map",
            )
        drop = getattr(vector_index, "drop", None)
        if not callable(drop):
            return
        try:
            await cast(Any, drop)()
        except Exception as exc:
            self.api.logger.warning(
                "kb.vector_index_drop_failed",
                collection=collection_name,
                error=str(exc),
            )
