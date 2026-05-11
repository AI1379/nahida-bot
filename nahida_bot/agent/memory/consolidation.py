"""Automatic memory consolidation for recent conversation turns."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import structlog

from nahida_bot.agent.memory.markdown import (
    MEMORY_FILE,
    MEMORY_SUMMARY_FILE,
    build_memory_projection,
    build_memory_summary,
    replace_generated_memory_section,
    validate_memory_content,
)

logger = structlog.get_logger(__name__)

_EXPLICIT_MEMORY_RE = re.compile(
    r"(?:请)?(?:记住|记一下|帮我记|remember(?: that)?|please remember)\s*[:：,，]?\s*(.+)",
    re.IGNORECASE,
)
_PREFERENCE_RE = re.compile(
    r"(我(?:更)?(?:喜欢|偏好|倾向|希望|不喜欢|讨厌)|以后(?:请)?(?:默认)?|prefer|preference)",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"(决定|确定|确认|采用|选用|改成|规划|方案|we decided|decision)",
    re.IGNORECASE,
)
_TASK_RE = re.compile(
    r"(下一步|之后|稍后|待办|TODO|todo|需要做|要做|follow up|next step)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+|\n+")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_VALID_KINDS = {
    "fact",
    "preference",
    "decision",
    "task",
    "procedure",
    "warning",
    "summary",
}
_DREAM_SYSTEM_PROMPT = """You are Nahida Bot's memory dreaming worker.

Your job is to convert recent conversation into durable memory changes.
Return ONLY valid JSON. Do not include markdown.

Rules:
- Add only stable user preferences, project facts, decisions, procedures, warnings, or follow-up tasks.
- Do not store secrets, credentials, tokens, cookies, private keys, signed URLs, base64, or raw event dumps.
- Do not invent facts. Use the conversation as evidence.
- Prefer concise Chinese if the conversation is Chinese.
- Archive an existing memory only when the new conversation clearly makes it obsolete or contradictory.

Schema:
{
  "add": [
    {
      "kind": "fact|preference|decision|task|procedure|warning|summary",
      "title": "short title",
      "content": "one concise durable memory",
      "confidence": 0.0,
      "importance": 0.0,
      "evidence": "short quote or reason"
    }
  ],
  "archive": [
    {
      "item_id": "existing memory id",
      "reason": "why this should be archived"
    }
  ]
}
"""


@dataclass(slots=True, frozen=True)
class ExtractedMemory:
    """One memory extracted from a conversation turn."""

    kind: str
    title: str
    content: str
    confidence: float = 0.7
    importance: float = 0.5
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class DreamArchive:
    """One existing memory item the dreaming pass wants to archive."""

    item_id: str
    reason: str


@dataclass(slots=True, frozen=True)
class MemoryDream:
    """Structured output from a memory dreaming pass."""

    additions: list[ExtractedMemory] = field(default_factory=list)
    archives: list[DreamArchive] = field(default_factory=list)


class RuleBasedMemoryExtractor:
    """Conservative extractor for obvious durable memory signals.

    This is intentionally narrow. It captures explicit remember requests,
    preferences, decisions, and follow-up tasks without requiring an extra LLM
    call after every chat turn.
    """

    def extract(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str = "",
    ) -> list[ExtractedMemory]:
        user_text = _compact_text(user_message)
        assistant_text = _compact_text(assistant_message)
        if not user_text:
            return []

        candidates: list[ExtractedMemory] = []
        explicit = _EXPLICIT_MEMORY_RE.search(user_text)
        if explicit is not None:
            content = _clean_candidate_content(explicit.group(1))
            if content:
                candidates.append(
                    ExtractedMemory(
                        kind="fact",
                        title=_title_from_content(content),
                        content=content,
                        confidence=0.95,
                        importance=0.8,
                        evidence={
                            "session_id": session_id,
                            "source_role": "user",
                            "user_message": user_text[:500],
                        },
                        metadata={"extractor": "rule_based", "signal": "explicit"},
                    )
                )

        for sentence in _candidate_sentences(user_text):
            if explicit is not None and explicit.group(0) in sentence:
                continue
            kind = _classify_sentence(sentence)
            if kind is None:
                continue
            content = _clean_candidate_content(sentence)
            if not content:
                continue
            candidates.append(
                ExtractedMemory(
                    kind=kind,
                    title=_title_from_content(content),
                    content=content,
                    confidence=0.78 if kind != "task" else 0.68,
                    importance=0.7 if kind in {"preference", "decision"} else 0.55,
                    evidence={
                        "session_id": session_id,
                        "source_role": "user",
                        "user_message": user_text[:500],
                    },
                    metadata={"extractor": "rule_based", "signal": kind},
                )
            )

        if assistant_text and _DECISION_RE.search(assistant_text):
            for sentence in _candidate_sentences(assistant_text):
                if _DECISION_RE.search(sentence) is None:
                    continue
                content = _clean_candidate_content(sentence)
                if not content:
                    continue
                candidates.append(
                    ExtractedMemory(
                        kind="decision",
                        title=_title_from_content(content),
                        content=content,
                        confidence=0.6,
                        importance=0.5,
                        evidence={
                            "session_id": session_id,
                            "source_role": "assistant",
                            "assistant_message": assistant_text[:500],
                        },
                        metadata={
                            "extractor": "rule_based",
                            "signal": "assistant_decision",
                        },
                    )
                )

        return _dedupe_extractions(candidates)


class LlmMemoryDreamer:
    """LLM-backed memory dreaming extractor.

    The model proposes structured memory changes. The consolidator remains
    responsible for validation, dedupe, safety filtering, and persistence.
    """

    def __init__(
        self,
        provider: Any,
        *,
        model: str | None = None,
        max_existing: int = 20,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_existing = max_existing

    async def dream(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        existing_items: list[Any],
    ) -> MemoryDream:
        """Ask the LLM for structured add/archive memory changes."""
        from nahida_bot.agent.context import ContextMessage

        prompt = self._build_prompt(
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            existing_items=existing_items[: self._max_existing],
        )
        response = await self._provider.chat(
            messages=[
                ContextMessage(
                    role="system",
                    source="memory_dreaming_system",
                    content=_DREAM_SYSTEM_PROMPT,
                ),
                ContextMessage(
                    role="user",
                    source="memory_dreaming_input",
                    content=prompt,
                ),
            ],
            tools=[],
            model=self._model,
        )
        return parse_memory_dream(str(response.content or ""))

    @staticmethod
    def _build_prompt(
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        existing_items: list[Any],
    ) -> str:
        existing_lines: list[str] = []
        for item in existing_items:
            item_id = str(getattr(item, "item_id", "") or "")
            kind = str(getattr(item, "kind", "fact") or "fact")
            title = str(getattr(item, "title", "") or "").strip()
            content = str(getattr(item, "content", "") or "").strip()
            if not item_id or not content:
                continue
            label = f"{item_id} ({kind})"
            if title:
                label += f" {title}"
            existing_lines.append(f"- {label}: {content[:400]}")

        existing_block = "\n".join(existing_lines) or "(none)"
        return (
            f"Session: {session_id}\n\n"
            "Existing durable memories:\n"
            f"{existing_block}\n\n"
            "Recent conversation:\n"
            f"User: {_compact_text(user_message)[:2000]}\n"
            f"Assistant: {_compact_text(assistant_message)[:2000]}\n\n"
            "Return the JSON memory changes now."
        )


class MemoryConsolidator:
    """Promote extracted conversation memory into durable memory items."""

    def __init__(
        self,
        memory_store: Any,
        *,
        extractor: RuleBasedMemoryExtractor | None = None,
        projection_limit: int = 40,
    ) -> None:
        self._memory = memory_store
        self._extractor = extractor or RuleBasedMemoryExtractor()
        self._projection_limit = projection_limit

    async def consolidate_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str = "",
        workspace_id: str | None = None,
        workspace_root: Path | None = None,
        dream_provider: Any | None = None,
        dream_model: str | None = None,
        run_rules: bool = True,
    ) -> int:
        """Extract and auto-apply durable memory from one completed turn."""
        append_item = getattr(self._memory, "append_item", None)
        if not callable(append_item):
            return 0

        existing_items = await self._load_existing_items()
        extracted = (
            self._extractor.extract(
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
            if run_rules
            else []
        )
        archives: list[DreamArchive] = []
        if dream_provider is not None:
            try:
                dream = await LlmMemoryDreamer(
                    dream_provider,
                    model=dream_model,
                ).dream(
                    session_id=session_id,
                    user_message=user_message,
                    assistant_message=assistant_message,
                    existing_items=existing_items,
                )
                extracted = _dedupe_extractions([*extracted, *dream.additions])
                archives = dream.archives
            except Exception as exc:
                logger.warning("memory_consolidation.dream_failed", error=str(exc))

        applied = 0
        for memory in extracted:
            if validate_memory_content(memory.content) is not None:
                continue
            if await self._has_duplicate(memory.content):
                continue
            candidate_id = await self._append_candidate(
                memory, workspace_id=workspace_id
            )
            metadata = {
                **memory.metadata,
                "session_id": session_id,
                "workspace_id": workspace_id or "",
                "candidate_id": candidate_id,
                "consolidated_at": datetime.now(UTC).isoformat(),
            }
            await cast(Any, append_item)(
                title=memory.title,
                content=memory.content,
                scope_type="global",
                scope_id="__global__",
                kind=memory.kind,
                source="consolidation",
                confidence=memory.confidence,
                importance=memory.importance,
                evidence=memory.evidence,
                metadata=metadata,
            )
            if candidate_id:
                await self._mark_candidate_applied(candidate_id)
            applied += 1

        applied += await self._apply_archives(
            archives,
            existing_items=existing_items,
            workspace_id=workspace_id,
        )

        if applied and workspace_root is not None:
            await self.project_workspace_memory(workspace_root)
        return applied

    async def project_workspace_memory(self, workspace_root: Path) -> None:
        """Regenerate workspace memory projection files from structured memory."""
        search_items = getattr(self._memory, "search_items", None)
        if not callable(search_items):
            return
        try:
            items = await cast(Any, search_items)("", limit=self._projection_limit)
        except Exception as exc:
            logger.warning("memory_consolidation.project_search_failed", error=str(exc))
            return

        root = Path(workspace_root)
        root.mkdir(parents=True, exist_ok=True)
        summary = build_memory_summary(items, max_items=self._projection_limit)
        (root / MEMORY_SUMMARY_FILE).write_text(summary, encoding="utf-8")

        memory_file = root / MEMORY_FILE
        existing = (
            memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
        )
        generated = build_memory_projection(items, max_items=self._projection_limit)
        memory_file.write_text(
            replace_generated_memory_section(existing, generated),
            encoding="utf-8",
        )

    async def _load_existing_items(self) -> list[Any]:
        search_items = getattr(self._memory, "search_items", None)
        if not callable(search_items):
            return []
        try:
            return list(await cast(Any, search_items)("", limit=self._projection_limit))
        except Exception as exc:
            logger.warning("memory_consolidation.load_existing_failed", error=str(exc))
            return []

    async def _apply_archives(
        self,
        archives: list[DreamArchive],
        *,
        existing_items: list[Any],
        workspace_id: str | None,
    ) -> int:
        archive_item = getattr(self._memory, "archive_item", None)
        if not callable(archive_item) or not archives:
            return 0
        existing_ids = {
            str(getattr(item, "item_id", "") or "")
            for item in existing_items
            if getattr(item, "item_id", "")
        }
        applied = 0
        for archive in archives:
            if archive.item_id not in existing_ids:
                continue
            candidate_id = f"cand_{uuid4().hex}"
            append_candidate = getattr(self._memory, "append_candidate", None)
            if callable(append_candidate):
                await cast(Any, append_candidate)(
                    candidate_id=candidate_id,
                    scope_type="global",
                    scope_id="__global__",
                    kind="archive",
                    title=f"Archive {archive.item_id}",
                    content=archive.reason,
                    status="auto_applied",
                    confidence=0.8,
                    evidence={"item_id": archive.item_id, "reason": archive.reason},
                    metadata={
                        "extractor": "llm_dream",
                        "workspace_id": workspace_id or "",
                    },
                )
            if await cast(Any, archive_item)(archive.item_id):
                applied += 1
        return applied

    async def _append_candidate(
        self, memory: ExtractedMemory, *, workspace_id: str | None
    ) -> str:
        append_candidate = getattr(self._memory, "append_candidate", None)
        if not callable(append_candidate):
            return ""
        candidate_id = f"cand_{uuid4().hex}"
        await cast(Any, append_candidate)(
            candidate_id=candidate_id,
            scope_type="global",
            scope_id="__global__",
            kind=memory.kind,
            title=memory.title,
            content=memory.content,
            status="auto_applied",
            confidence=memory.confidence,
            evidence=memory.evidence,
            metadata={**memory.metadata, "workspace_id": workspace_id or ""},
        )
        return candidate_id

    async def _mark_candidate_applied(self, candidate_id: str) -> None:
        mark_candidate_applied = getattr(self._memory, "mark_candidate_applied", None)
        if callable(mark_candidate_applied):
            await cast(Any, mark_candidate_applied)(candidate_id)

    async def _has_duplicate(self, content: str) -> bool:
        search_items = getattr(self._memory, "search_items", None)
        if not callable(search_items):
            return False
        try:
            results = await cast(Any, search_items)(content, limit=10)
        except Exception:
            return False
        needle = _normalize_for_dedupe(content)
        return any(
            _normalize_for_dedupe(str(getattr(item, "content", ""))) == needle
            for item in results
        )


def _candidate_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    if not parts and text.strip():
        parts = [text.strip()]
    return [part for part in parts if 8 <= len(part) <= 500]


def _classify_sentence(sentence: str) -> str | None:
    if _PREFERENCE_RE.search(sentence) is not None:
        return "preference"
    if _DECISION_RE.search(sentence) is not None:
        return "decision"
    if _TASK_RE.search(sentence) is not None:
        return "task"
    return None


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clean_candidate_content(content: str) -> str:
    value = _compact_text(content)
    value = value.strip(" -:：,，;；")
    if len(value) > 500:
        value = value[:500].rstrip() + "..."
    return value


def _title_from_content(content: str) -> str:
    value = re.sub(r"[。！？.!?].*$", "", content).strip()
    return value[:40].rstrip()


def _normalize_for_dedupe(content: str) -> str:
    return re.sub(r"\s+", "", content).casefold()


def _dedupe_extractions(items: list[ExtractedMemory]) -> list[ExtractedMemory]:
    seen: set[str] = set()
    result: list[ExtractedMemory] = []
    for item in items:
        key = _normalize_for_dedupe(item.content)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def parse_memory_dream(raw: str) -> MemoryDream:
    """Parse and validate LLM dreaming JSON output."""
    payload = _extract_json_payload(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("memory dream output was not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("memory dream output must be a JSON object")

    additions: list[ExtractedMemory] = []
    raw_additions = data.get("add", [])
    if isinstance(raw_additions, list):
        for raw_item in raw_additions:
            if not isinstance(raw_item, dict):
                continue
            content = _clean_candidate_content(str(raw_item.get("content", "")))
            if not content:
                continue
            kind = str(raw_item.get("kind", "fact") or "fact").strip()
            if kind not in _VALID_KINDS:
                kind = "fact"
            title = _clean_candidate_content(str(raw_item.get("title", "")))
            if not title:
                title = _title_from_content(content)
            evidence_text = _clean_candidate_content(str(raw_item.get("evidence", "")))
            additions.append(
                ExtractedMemory(
                    kind=kind,
                    title=title,
                    content=content,
                    confidence=_clamp_float(raw_item.get("confidence"), default=0.65),
                    importance=_clamp_float(raw_item.get("importance"), default=0.5),
                    evidence={"llm_evidence": evidence_text} if evidence_text else {},
                    metadata={"extractor": "llm_dream"},
                )
            )

    archives: list[DreamArchive] = []
    raw_archives = data.get("archive", [])
    if isinstance(raw_archives, list):
        for raw_item in raw_archives:
            if not isinstance(raw_item, dict):
                continue
            item_id = str(raw_item.get("item_id", "") or "").strip()
            reason = _clean_candidate_content(str(raw_item.get("reason", "")))
            if item_id and reason:
                archives.append(DreamArchive(item_id=item_id, reason=reason))

    return MemoryDream(
        additions=_dedupe_extractions(additions),
        archives=archives,
    )


def _extract_json_payload(raw: str) -> str:
    value = raw.strip()
    fenced = _JSON_FENCE_RE.search(value)
    if fenced is not None:
        return fenced.group(1).strip()
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        return value[start : end + 1]
    return value


def _clamp_float(value: object, *, default: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0.0, min(number, 1.0))
