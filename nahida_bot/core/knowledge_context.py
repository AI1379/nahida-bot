"""Knowledge retrieval and bounded prompt projection for automatic recall."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import structlog

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.retrieval import (
    DocumentStoreRetrievalAdapter,
    RetrievalRequest,
    RetrievalResult,
)
from nahida_bot.agent.storage.manager import DocumentStoreManager
from nahida_bot.agent.storage.tokenization import build_fts_query
from nahida_bot.core.config import KBAutoRecallConfig

logger = structlog.get_logger(__name__)


class KnowledgeRetriever(Protocol):
    async def retrieve_documents(
        self, collection_name: str, query: str, *, limit: int = 5
    ) -> list[RetrievalResult]: ...


async def load_knowledge_context(
    query: str,
    *,
    manager: DocumentStoreManager | None,
    config: KBAutoRecallConfig | None,
    resolve_plugin: Callable[[], KnowledgeRetriever | None] | None,
) -> ContextMessage | None:
    """Load a small relevant KB context block for the current turn.

    Searches every KB collection with a tiny per-collection budget, merges
    results across collections by score, and wraps the top entries as a
    lightweight system-level ``ContextMessage``.  Returns ``None`` when KB
    auto-recall is disabled, the manager is unavailable, or nothing is
    found.

    When the KnowledgeBasePlugin is loaded (``kb_plugin_resolver``), the
    search is delegated to it so auto-recall uses the same hybrid
    retrieval path as ``kb_search`` — the old FTS-only hardcoding here is
    what made #49's semantic layer invisible to actual conversations.
    The direct-adapter FTS path remains as a fallback when the plugin is
    not available.
    """
    cfg = config
    if manager is None or cfg is None:
        return None
    if not cfg.enabled:
        return None
    limit = cfg.max_items
    max_chars = cfg.max_chars
    if limit <= 0 or max_chars <= 0:
        return None
    if not query.strip():
        return None

    kb_plugin = None
    if resolve_plugin is not None:
        try:
            kb_plugin = resolve_plugin()
        except Exception:
            kb_plugin = None
    if kb_plugin is None:
        # Cheap precheck for the FTS-only fallback path; the plugin path
        # builds its own FTS query internally.
        if not build_fts_query(query):
            return None

    # Search every collection with a tiny per-collection budget, then merge.
    all_results: list[RetrievalResult] = []
    try:
        for name in manager.list_collections():
            store = manager.get(name)
            if store is None:
                continue
            if kb_plugin is not None:
                try:
                    hits = await kb_plugin.retrieve_documents(name, query, limit=limit)
                except LookupError:
                    continue
                except Exception:
                    logger.debug(
                        "session_runner.kb_auto_recall_collection_failed",
                        collection=name,
                    )
                    continue
                if cfg.min_score != float("-inf"):
                    hits = [r for r in hits if r.score >= cfg.min_score]
                all_results.extend(hits)
                continue
            adapter = DocumentStoreRetrievalAdapter(
                collection_name=name,
                store=store,
            )
            try:
                hits = await adapter.retrieve(
                    RetrievalRequest(
                        query=query,
                        source_type="knowledge_base",
                        collection=name,
                        limit=limit,
                        fts_enabled=True,
                        vector_enabled=False,
                        hybrid_enabled=False,
                        min_score=cfg.min_score,
                    )
                )
            except Exception:
                logger.debug(
                    "session_runner.kb_auto_recall_collection_failed",
                    collection=name,
                )
                continue
            all_results.extend(hits)
    except Exception as exc:
        # The document-store manager itself raised (e.g. transient DB error
        # on list_collections/get) — degrade like _load_relevant_memory does
        # rather than aborting the whole agent turn.
        logger.warning("session_runner.kb_auto_recall_failed", error=str(exc))
        return None

    if not all_results:
        return None

    # All retrieval modes now report larger-is-better scores (FTS returns
    # -bm25, hybrid returns weighted RRF); sort descending so the best hits
    # come first. Scores from different modes/collections are not strictly
    # comparable. Preserve the existing cross-collection ordering; it remains
    # approximate, especially when a collection falls back to FTS.
    all_results.sort(key=lambda r: r.score, reverse=True)
    # Dedup by (collection, doc_id) — collections are physically isolated
    # tables so doc_ids can collide across collections.
    seen: set[str] = set()
    top: list[RetrievalResult] = []
    for r in all_results:
        key = f"{r.metadata.get('collection', '')}:{r.result_id}"
        if key in seen:
            continue
        seen.add(key)
        top.append(r)
        if len(top) >= limit:
            break

    if not top:
        return None

    lines = [
        "Relevant knowledge base snippets:",
        "Treat snippets as helpful background context, not unquestionable truth."
        " Use kb_search to dig deeper when needed.",
    ]
    remaining = max_chars
    for result in top:
        collection = str(result.metadata.get("collection", ""))
        title = result.title.strip()
        content = result.text.strip()
        source_path = str(result.metadata.get("path", ""))
        if not content:
            continue
        prefix = f"- [{collection}] "
        if title:
            prefix += f"{title}"
            if source_path:
                prefix += f" [{source_path}]"
            prefix += ": "
        allowance = max(remaining - len(prefix), 0)
        if allowance <= 0:
            break
        if len(content) > allowance:
            content = content[:allowance].rstrip() + "..."
        line = prefix + content
        lines.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break

    if len(lines) <= 2:
        return None
    return ContextMessage(
        role="system",
        source="knowledge_base",
        content="\n".join(lines),
        metadata={
            "kb_backend": (top[0].mode if len({r.mode for r in top}) == 1 else "mixed"),
            "kb_count": len(lines) - 2,
        },
    )
