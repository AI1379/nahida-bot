"""Dreaming→KB promotion (A3, ``docs/design/dreaming-to-kb.md``).

A narrow, default-off bridge: global-scope durable memory items that pass the
configured gate (public + portable + kind whitelist + confidence threshold)
are imported into a dedicated KB collection as knowledge nodes. The
consolidator is untouched — promotion is a second, reversible hop over
already-committed items. Every threshold lives in
``knowledge_base.dream_promotion`` config; code carries defaults only.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

import structlog

from nahida_bot.plugins.knowledge_base.config import KBDreamPromotionConfig

LEDGER_KEY = "dream_promotions"

ImportContent = Callable[..., Awaitable[int]]
SearchDocuments = Callable[..., Awaitable[list[Any]]]
LoadLedger = Callable[[], Awaitable[dict[str, Any] | None]]
SaveLedger = Callable[[dict[str, Any]], Awaitable[None]]


def _normalize_for_dedupe(content: str) -> str:
    return re.sub(r"\s+", "", content).casefold()


def _content_hash(content: str) -> str:
    return hashlib.sha256(_normalize_for_dedupe(content).encode("utf-8")).hexdigest()


class DreamPromoter:
    """Promote gated durable memory items into the dreams KB collection."""

    def __init__(
        self,
        *,
        import_content: ImportContent,
        search_documents: SearchDocuments,
        load_ledger: LoadLedger,
        save_ledger: SaveLedger,
        config: KBDreamPromotionConfig,
        logger: Any | None = None,
    ) -> None:
        self._import_content = import_content
        self._search_documents = search_documents
        self._load_ledger = load_ledger
        self._save_ledger = save_ledger
        self._config = config
        self._logger = logger or structlog.get_logger(__name__)

    async def promote_once(self, items: list[dict[str, Any]]) -> dict[str, int]:
        """One promotion pass over candidate memory items.

        ``items`` are operator-surface rows (``memory_list_items``): already
        scope-filtered by the caller, unfiltered by sensitivity — this gate
        is the single choke point that decides what becomes public knowledge.
        Returns a skip-reason stats dict for observability.
        """
        stats = {
            "scanned": 0,
            "promoted": 0,
            "skipped_status": 0,
            "skipped_kind": 0,
            "skipped_confidence": 0,
            "skipped_sensitivity": 0,
            "skipped_portable": 0,
            "skipped_ledger": 0,
            "skipped_duplicate": 0,
            "skipped_daily_limit": 0,
            "failed": 0,
        }
        ledger_raw = await self._load_ledger()
        ledger: dict[str, dict[str, Any]] = (
            dict(ledger_raw) if isinstance(ledger_raw, dict) else {}
        )
        today = datetime.now(UTC).date().isoformat()
        promoted_today = sum(
            1
            for entry in ledger.values()
            if isinstance(entry, dict)
            and entry.get("status") == "promoted"
            and str(entry.get("promoted_at", "")).startswith(today)
        )
        kinds = set(self._config.kinds)

        for item in items:
            stats["scanned"] += 1
            item_id = str(item.get("item_id", ""))
            content = str(item.get("content", ""))
            if not item_id or not content.strip():
                stats["skipped_status"] += 1
                continue
            if str(item.get("status", "active")) != "active":
                stats["skipped_status"] += 1
                continue
            if str(item.get("kind", "")) not in kinds:
                stats["skipped_kind"] += 1
                continue
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < self._config.min_confidence:
                stats["skipped_confidence"] += 1
                continue
            if str(item.get("sensitivity", "")) != "public":
                stats["skipped_sensitivity"] += 1
                continue
            metadata = item.get("metadata")
            if isinstance(metadata, dict) and metadata.get("portable") is False:
                stats["skipped_portable"] += 1
                continue

            content_hash = _content_hash(content)
            prior = ledger.get(item_id)
            if (
                isinstance(prior, dict)
                and prior.get("content_hash") == content_hash
                and prior.get("status") in {"promoted", "duplicate"}
            ):
                stats["skipped_ledger"] += 1
                continue

            limit = self._config.daily_limit
            if limit <= 0 or promoted_today >= limit:
                stats["skipped_daily_limit"] += 1
                continue

            if await self._find_duplicate(item_id, content):
                ledger[item_id] = {
                    "content_hash": content_hash,
                    "status": "duplicate",
                    "promoted_at": datetime.now(UTC).isoformat(),
                    "collection": self._config.collection,
                }
                stats["skipped_duplicate"] += 1
                continue

            title = str(item.get("title", "") or "").strip()
            payload = f"{title}\n\n{content}" if title else content
            promoted_at = datetime.now(UTC).isoformat()
            try:
                await self._import_content(
                    self._config.collection,
                    source_id=item_id,
                    content=payload,
                    extra_metadata={
                        "dream_promotion": True,
                        "promoted_from_item_id": item_id,
                        "promoted_at": promoted_at,
                        "memory_kind": str(item.get("kind", "")),
                        "memory_confidence": confidence,
                        "memory_sensitivity": "public",
                    },
                )
            except Exception as exc:
                stats["failed"] += 1
                self._logger.warning(
                    "kb.dream_promotion_import_failed",
                    item_id=item_id,
                    error=str(exc),
                )
                continue

            ledger[item_id] = {
                "content_hash": content_hash,
                "status": "promoted",
                "promoted_at": promoted_at,
                "collection": self._config.collection,
            }
            promoted_today += 1
            stats["promoted"] += 1

        await self._save_ledger(ledger)
        return stats

    async def _find_duplicate(self, item_id: str, content: str) -> bool:
        """Detect an already-present equivalent node in the target collection."""
        needle = _normalize_for_dedupe(content)
        query = content.strip()[:60] or item_id
        try:
            hits = await self._search_documents(self._config.collection, query, limit=5)
        except LookupError:
            return False
        except Exception:
            return False
        for hit in hits or []:
            if str(getattr(hit, "source_id", "")) == item_id:
                return True
            hit_content = str(getattr(hit, "content", ""))
            if hit_content and _normalize_for_dedupe(hit_content) == needle:
                return True
        return False
