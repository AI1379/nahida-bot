"""Automatic memory consolidation for recent conversation turns."""

from __future__ import annotations

import re
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from nahida_bot.agent.memory.models import (
    Sensitivity,
    SensitivitySource,
    MemoryChanges,
    MemoryRecord,
)

if TYPE_CHECKING:
    from nahida_bot.identity.policy import MemoryWriteRequest
from nahida_bot.agent.memory.portability import (
    normalize_portable,
)

import structlog

from nahida_bot.agent.memory.markdown import (
    validate_memory_content,
)
from nahida_bot.agent.memory.service import project_workspace_memory
from nahida_bot.agent.memory.scope import (
    SCOPE_ID_GLOBAL,
    SCOPE_TYPE_CHAT,
    SCOPE_TYPE_GLOBAL,
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
    "impression",
    "preference",
    "decision",
    "task",
    "procedure",
    "warning",
    "summary",
}

# --- Sensitivity classification (Piece A3) ----------------------------------
# Conservative auto-tagging so the soft-scope retrieval filter (A2) has
# provenance to protect. ``secret_like`` never crosses scopes; ``private``
# relaxes only when the origin parties are present; the soft ``public``
# baseline is the default. Over-tags slightly (宁可多标): once soft-scope is on,
# a missed sensitive item can surface in another chat.
_SECRET_SIGNAL_RE = re.compile(
    r"(password|passwd|api[_-]?key|secret|bearer|cookie|private[_-]?key|"
    r"密码|密钥|口令|令牌|凭证|私钥)",
    re.IGNORECASE,
)
# Strong PII: CN mobile, ID-card-like, card-like, account identifiers.
_PII_RE = re.compile(
    r"(?:\b1[3-9]\d{9}\b"
    r"|\b\d{15,18}[Xx]?\b"
    r"|\b(?:62|4[0-9]|5[1-5])\d{14,17}\b"
    r"|身份证|银行卡|手机号|微信号|qq号|qq[:：])"
)
# Explicit "keep this between us" markers — the strongest private signal, often
# set in 1:1 chats and the highest leak risk if surfaced in a group.
_PRIVACY_MARKER_RE = re.compile(
    r"(别告诉|不要告诉|不要说|别说|私下|秘密|保密|仅限你我|只给你|"
    r"don'?t tell|keep it (?:private|between|to yourself)|just between us|"
    r"this is private|off the record)",
    re.IGNORECASE,
)
_GROUP_CONTEXT_RE = re.compile(r"(?:这个群|本群|群里|群友|group chat)", re.IGNORECASE)
_SELF_ALIAS_RE = re.compile(
    r"(?:都|一般|通常|平时)?\s*(?:叫|称呼)我(?:为|作)?\s*"
    r"[「『“\"']?([^\s，。！？、；;「」『』“”\"']{1,20})",
    re.IGNORECASE,
)


def classify_sensitivity(
    content: str, *, title: str = ""
) -> tuple[Sensitivity, SensitivitySource]:
    """Conservative sensitivity classification for a consolidated memory.

    Returns ``(sensitivity, sensitivity_source)``.

    Precedence (highest first): ``secret_like`` (strictest, never crosses
    scopes) → explicit-private (user asked to keep it between us) →
    dream-private (inferred from PII) → the soft ``public`` baseline.

    The explicit "keep this between us" markers carry ``sensitivity_source=
    'explicit'`` so they outrank the dreaming pass's inferred ``'dream'``
    classification (Piece A4: explicit > dream). PII and secret signals stay
    ``'dream'`` — they are content-based inferences, not user intent.
    """
    text = f"{title}\n{content}"
    if _SECRET_SIGNAL_RE.search(text):
        return "secret_like", "dream"
    if _PRIVACY_MARKER_RE.search(text):
        return "private", "explicit"
    if _PII_RE.search(text):
        return "private", "dream"
    return "public", "default"


_DREAM_SYSTEM_PROMPT_BODY = """

Your job is to convert recent conversation into durable memory changes.
Return ONLY valid JSON. Do not include markdown.

Rules:
- Add only stable user preferences, qualified impressions, project facts, decisions, procedures, warnings, or follow-up tasks. Use kind=impression for revisable interpretations of a person or relationship; keep uncertainty explicit and cite specific evidence.
- If the conversation contains no stable durable change, return empty add/archive arrays. Do not create a memory merely to summarize that nothing happened.
- Never store routine chat recaps, transient reactions, image-only exchanges, greetings, or "recent conversation" summaries.
- Do not store secrets, credentials, tokens, cookies, private keys, signed URLs, base64, or raw event dumps.
- Do not invent facts. Use the conversation as evidence.
- Input message records include trusted turn IDs, authors and source chats. Preserve who said what; never merge different people into "the user".
- For each addition and archive, return evidence_turn_ids from these records. Assistant replies, quotations, forwards and existing memories are not independent user assertions.
- Each addition must include subject: {"kind": "account", "id": "channel:user:id"} for a known participant, {"kind": "chat", "id": "source chat key"} for shared context, or {"kind": "unknown", "id": ""} when unresolved. The speaker is not necessarily the subject. Unknown personal claims will be held for review.
- Additional subjects: {"kind":"bot","id":"self"} for stable self-knowledge, {"kind":"environment","id":"runtime"} for environment knowledge, or {"kind":"relationship","members":[{"kind":"account","id":"channel:user:A"},{"kind":"account","id":"channel:user:B"}]} for a relationship. Every participant must be identified in the evidence. An impression is qualified, revisable interpretation, never proof of someone's inner life. Do not persist transient social moods.
- Never infer Person links or treat matching display names as identity. Preserve uncertainty and distinguish reports about others from their own statements.
- Content that reached this conversation via knowledge-base recall (auto-recalled snippets or kb_search/context_read results, including dream-promoted nodes) is existing knowledge, not new evidence: never create a memory entry that merely restates it.
- Prefer the language and terminology the user normally uses in the conversation.
- Archive an existing memory only when the new conversation clearly makes it obsolete or contradictory.
- Audience is independent from kind. Use "current" unless the item is intentionally applicable across every chat and user of this bot.
- "global" is exceptional: only public bot/environment self-knowledge or cross-chat bot-wide decisions, procedures, or warnings qualify. Personal information, project/session state, cron-specific instructions, and every summary must use "current".
- A group-local nickname for the current sender is a contextual alias: set relation="alias", subject to the identified account, alias to the nickname, and portable=false. Do not infer aliases for third parties or use an alias as proof that two accounts are the same Person.

Schema:
{
  "add": [
    {
      "kind": "fact|impression|preference|decision|task|procedure|warning|summary",
      "audience": "current|global",
      "title": "short title",
      "content": "one concise durable memory",
      "confidence": 0.0,
      "importance": 0.0,
      "evidence": "short quote or reason",
      "relation": "alias or omit",
      "subject": {"kind": "bot|environment|account|chat|relationship|unknown", "id": "verified ID or empty", "members": []},
      "evidence_turn_ids": [1],
      "alias": "group-local nickname or omit",
      "portable": true
    }
  ],
  "archive": [
    {
      "item_id": "existing memory id",
      "reason": "why this should be archived",
      "evidence_turn_ids": [2]
    }
  ]
}
"""


def build_dream_system_prompt(app_name: str) -> str:
    """Build the LLM dreaming system prompt."""
    name = app_name.strip() or "the assistant"
    return (
        f"You are the memory dreaming worker for {name}.\n{_DREAM_SYSTEM_PROMPT_BODY}"
    )


@dataclass(slots=True, frozen=True)
class ExtractedMemory:
    """One memory extracted from a conversation turn."""

    kind: str
    title: str
    content: str
    audience: str = "current"
    confidence: float = 0.7
    importance: float = 0.5
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class DreamArchive:
    """One existing memory item the dreaming pass wants to archive."""

    item_id: str
    reason: str
    evidence_turn_ids: list[int] = field(default_factory=list)


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
        contextual_alias = _extract_contextual_self_alias(user_text, session_id)
        explicit = _EXPLICIT_MEMORY_RE.search(user_text)
        if explicit is not None:
            content = (
                _contextual_alias_content(contextual_alias[0])
                if contextual_alias is not None
                else _clean_candidate_content(explicit.group(1))
            )
            if content:
                metadata: dict[str, Any] = {
                    "extractor": "rule_based",
                    "signal": "explicit",
                }
                if contextual_alias is not None:
                    metadata.update(_contextual_alias_metadata(contextual_alias[0]))
                candidates.append(
                    ExtractedMemory(
                        kind="fact",
                        title=(
                            f"群内称呼：{contextual_alias[0]}"
                            if contextual_alias is not None
                            else _title_from_content(content)
                        ),
                        content=content,
                        confidence=0.95,
                        importance=0.8,
                        evidence={
                            "session_id": session_id,
                            "source_role": "user",
                            "user_message": user_text[:500],
                        },
                        metadata=metadata,
                    )
                )

        if contextual_alias is not None and explicit is None:
            alias, source_sentence = contextual_alias
            candidates.append(
                ExtractedMemory(
                    kind="fact",
                    title=f"群内称呼：{alias}",
                    content=_contextual_alias_content(alias),
                    confidence=0.9,
                    importance=0.65,
                    evidence={
                        "session_id": session_id,
                        "source_role": "user",
                        "user_message": source_sentence[:500],
                    },
                    metadata={
                        "extractor": "rule_based",
                        "signal": "contextual_alias",
                        **_contextual_alias_metadata(alias),
                    },
                )
            )

        for sentence in _candidate_sentences(user_text):
            if explicit is not None and explicit.group(0) in sentence:
                continue
            if contextual_alias is not None and contextual_alias[1] in sentence:
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
        app_name: str = "the assistant",
        max_existing: int = 20,
    ) -> None:
        self._provider = provider
        self._model = model
        self._app_name = app_name
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

        logger.debug(
            "memory_dreaming.llm_start",
            session_id=session_id,
            model=self._model or "",
            existing_count=len(existing_items),
            user_chars=len(user_message),
            assistant_chars=len(assistant_message),
        )
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
                    content=build_dream_system_prompt(self._app_name),
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
        dream = parse_memory_dream(str(response.content or ""))
        logger.debug(
            "memory_dreaming.llm_done",
            session_id=session_id,
            additions=len(dream.additions),
            archives=len(dream.archives),
        )
        return dream

    @staticmethod
    def _build_prompt(
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        existing_items: list[Any],
    ) -> str:
        from nahida_bot.agent.memory.rendering import format_memory_items

        existing_block = (
            format_memory_items(existing_items, max_chars=8000)
            if existing_items
            else "(none)"
        )
        return (
            f"Session: {session_id}\n\n"
            "Existing durable memories:\n"
            f"{existing_block}\n\n"
            "Recent conversation:\n"
            f"User: {user_message}\n"
            f"Assistant: {assistant_message}\n\n"
            "Return the JSON memory changes now."
        )


class MemoryConsolidator:
    """Extract candidates; MemoryService owns policy and durable mutations."""

    def __init__(
        self,
        memory_store: Any,
        *,
        extractor: RuleBasedMemoryExtractor | None = None,
        projection_limit: int = 40,
        app_name: str = "the assistant",
        default_scope_type: str = SCOPE_TYPE_GLOBAL,
        default_scope_id: str = SCOPE_ID_GLOBAL,
        default_person_id: str | None = None,
        default_sender_account_key: str = "",
    ) -> None:
        self._memory = memory_store
        self._extractor = extractor or RuleBasedMemoryExtractor()
        self._projection_limit = projection_limit
        self._app_name = app_name
        self._default_scope_type = default_scope_type
        self._default_scope_id = default_scope_id
        self._default_person_id = default_person_id
        self._default_sender_account_key = default_sender_account_key

    async def consolidate_turn(self, **kwargs: Any) -> int:
        """Compatibility entry for immediate rules; never projects shared files."""
        from nahida_bot.agent.memory.service import MemoryService

        changes = await self.prepare_turn(**kwargs)
        return await MemoryService(self._memory).apply_changes(changes)

    async def prepare_turn(
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
        scope_type: str | None = None,
        scope_id: str | None = None,
        person_id: str | None = None,
        sender_account_key: str = "",
        records: list[MemoryRecord] | None = None,
    ) -> MemoryChanges:
        from nahida_bot.agent.memory.models import MemoryChanges
        from nahida_bot.identity.policy import MemoryWriteRequest

        changes = MemoryChanges()
        if not run_rules and dream_provider is None:
            return changes
        scope_type = scope_type or self._default_scope_type
        scope_id = scope_id or self._default_scope_id
        request = MemoryWriteRequest(
            chat_scope_id=scope_id if scope_type == SCOPE_TYPE_CHAT else "",
            person_id=person_id or self._default_person_id,
            sender_account_key=sender_account_key or self._default_sender_account_key,
        )
        query = (
            "\n".join(record.turn.content for record in records)
            if records
            else user_message
        )
        existing = await self._load_existing_items(
            scope_type=scope_type, scope_id=scope_id, query=query
        )
        if ":private:" in request.chat_scope_id:
            from nahida_bot.agent.memory.attribution import EvidenceRef

            accounts = {
                EvidenceRef.from_record(record).account_key for record in records or []
            }
            accounts.add(request.sender_account_key)
            scopes = [("account", account) for account in accounts if account]
            if request.person_id:
                scopes.insert(0, ("person", request.person_id))
            for identity_type, identity_id in scopes:
                existing.extend(
                    await self._load_existing_items(
                        scope_type=identity_type, scope_id=identity_id, query=query
                    )
                )
            existing = list({item.item_id: item for item in existing}.values())

        extracted = (
            self._extractor.extract(
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
            if run_rules
            else []
        )
        archives = []
        if dream_provider is not None:
            # A failed request/parse must propagate, so batch processing cannot
            # mistake it for a successful empty dream and advance its cursor.
            dream = await LlmMemoryDreamer(
                dream_provider, model=dream_model, app_name=self._app_name
            ).dream(
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                existing_items=existing,
            )
            extracted = _dedupe_extractions([*extracted, *dream.additions])
            archives = dream.archives
        for memory in extracted:
            await self._prepare_addition(
                changes,
                memory,
                request=request,
                scope_type=scope_type,
                scope_id=scope_id,
                session_id=session_id,
                workspace_id=workspace_id,
                records=records,
            )
        existing_by_id = {item.item_id: item for item in existing}
        for archive in archives:
            old = existing_by_id.get(archive.item_id)
            if old is None:
                continue
            if records is not None:
                from nahida_bot.agent.memory.attribution import (
                    attribution_for_candidate,
                    item_subject,
                )

                try:
                    attribution_for_candidate(
                        {
                            "evidence_turn_ids": archive.evidence_turn_ids,
                            "subject": asdict(item_subject(old)),
                        },
                        records,
                    )
                except ValueError:
                    continue
            changes.archives.append(archive.item_id)
            from nahida_bot.agent.memory.models import MemoryCandidate

            changes.candidates.append(
                MemoryCandidate(
                    candidate_id=f"cand_{uuid4().hex}",
                    scope_type=old.scope_type,
                    scope_id=old.scope_id,
                    kind="archive",
                    title=f"Archive {old.item_id}",
                    content=archive.reason,
                    status="auto_applied",
                    evidence={
                        "item_id": old.item_id,
                        "evidence_turn_ids": archive.evidence_turn_ids,
                    },
                )
            )
        return changes

    async def _prepare_addition(
        self,
        changes: MemoryChanges,
        memory: ExtractedMemory,
        *,
        request: MemoryWriteRequest,
        scope_type: str,
        scope_id: str,
        session_id: str,
        workspace_id: str | None,
        records: list[MemoryRecord] | None,
    ) -> None:
        from nahida_bot.agent.memory.attribution import (
            EvidenceRef,
            MemoryAttribution,
            MemorySubject,
            attribution_for_candidate,
            item_subject,
        )
        from nahida_bot.agent.memory.models import MemoryCandidate
        from nahida_bot.agent.memory.service import MemoryService
        from dataclasses import replace

        metadata = dict(memory.metadata)
        rejection = validate_memory_content(memory.content) or ""
        if rejection:
            return
        if records is not None:
            if memory.metadata.get("extractor") != "llm_dream" and len(records) == 1:
                ref = EvidenceRef.from_record(records[0])
                metadata["evidence_turn_ids"] = [ref.turn_id]
                metadata["subject"] = (
                    {"kind": "account", "id": ref.account_key}
                    if ref.account_key
                    else {"kind": "chat", "id": ref.chat_id}
                )
            try:
                attribution = attribution_for_candidate(metadata, records)
                metadata["attribution"] = attribution.to_dict()
                if attribution.subject.kind == "unknown":
                    rejection = (
                        "memory requires an identified subject or shared chat context"
                    )
            except ValueError as exc:
                rejection = str(exc)
        else:
            subject = (
                MemorySubject("account", request.sender_account_key)
                if request.sender_account_key
                else MemorySubject()
            )
            metadata["attribution"] = MemoryAttribution(subject).to_dict()
        if metadata.get("relation") == "alias":
            attribution = MemoryAttribution.parse(metadata)
            account = (
                attribution.subject.id if attribution.subject.kind == "account" else ""
            )
            if records is not None:
                own_evidence = [
                    record
                    for record in records
                    if record.turn_id in metadata.get("evidence_turn_ids", [])
                    and EvidenceRef.from_record(record).account_key == account
                ]
                if not account or not any(
                    _extract_contextual_self_alias(record.turn.content, session_id)
                    for record in own_evidence
                ):
                    rejection = "alias requires an evidenced self-identification"
            elif not request.sender_account_key and not request.person_id:
                rejection = "alias requires a single identified speaker"
            metadata.update(context_chat_id=request.chat_scope_id, portable=False)
            if account:
                metadata["subject_account_key"] = account
            if request.person_id and account == request.sender_account_key:
                metadata["subject_person_id"] = request.person_id
        candidate_id = f"cand_{uuid4().hex}"
        metadata.update(
            session_id=session_id,
            workspace_id=workspace_id or "",
            candidate_id=candidate_id,
        )
        if rejection:
            changes.candidates.append(
                MemoryCandidate(
                    candidate_id=candidate_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    kind=memory.kind,
                    title=memory.title,
                    content=memory.content,
                    status="pending",
                    confidence=memory.confidence,
                    evidence=memory.evidence,
                    metadata={
                        **metadata,
                        "rejection_reason": rejection,
                        "batch_turn_ids": [r.turn_id for r in records or []],
                    },
                )
            )
            logger.info(
                "memory_consolidation.candidate_deferred",
                session_id=session_id,
                reason=rejection,
            )
            return
        sensitivity, sensitivity_source = classify_sensitivity(
            memory.content, title=memory.title
        )
        try:
            item = MemoryService(self._memory).prepare_item(
                memory.title,
                memory.content,
                session_id=session_id,
                write_request=request,
                metadata={
                    **metadata,
                    "kind": memory.kind,
                    "source": "consolidation",
                    "audience": memory.audience,
                    "confidence": memory.confidence,
                    "importance": memory.importance,
                    "evidence": memory.evidence,
                    "sensitivity": sensitivity,
                },
            )
        except ValueError:
            return
        item = replace(item, sensitivity_source=sensitivity_source)
        duplicates = await self._memory.search_items(
            item.content,
            scope_type=item.scope_type,
            scope_id=item.scope_id,
            limit=10,
        )
        if any(
            _normalize_for_dedupe(other.content) == _normalize_for_dedupe(item.content)
            and item_subject(other) == item_subject(item)
            for other in [*duplicates, *changes.additions]
        ):
            return
        changes.additions.append(item)
        changes.candidates.append(
            MemoryCandidate(
                candidate_id=candidate_id,
                scope_type=item.scope_type,
                scope_id=item.scope_id,
                kind=item.kind,
                title=item.title,
                content=item.content,
                status="auto_applied",
                confidence=item.confidence,
                evidence=item.evidence,
                metadata=item.metadata,
            )
        )

    async def project_workspace_memory(
        self,
        workspace_root: Path,
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
    ) -> None:
        """Explicit operator export only; never called by consolidation."""
        await project_workspace_memory(
            self._memory,
            workspace_root,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=self._projection_limit,
        )

    async def _load_existing_items(
        self, *, scope_type: str, scope_id: str, query: str = ""
    ) -> list[Any]:
        relevant = (
            await self._memory.search_items(
                query,
                scope_type=scope_type,
                scope_id=scope_id,
                limit=self._projection_limit // 2,
            )
            if query.strip()
            else []
        )
        recent = await self._memory.search_items(
            "",
            scope_type=scope_type,
            scope_id=scope_id,
            limit=self._projection_limit // 2,
        )
        return list({item.item_id: item for item in [*relevant, *recent]}.values())


def _candidate_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    if not parts and text.strip():
        parts = [text.strip()]
    return [part for part in parts if 8 <= len(part) <= 500]


def _extract_contextual_self_alias(
    text: str, session_id: str
) -> tuple[str, str] | None:
    """Extract the narrow, safe group-local "people call me X" form.

    This intentionally handles only the current sender. Resolving a nickname
    for another participant requires structured mention/account data; guessing
    from display text would turn a social observation into a false identity
    link.
    """

    if ":group:" not in session_id.casefold():
        return None
    for sentence in _candidate_sentences(text):
        if _GROUP_CONTEXT_RE.search(sentence) is None:
            continue
        match = _SELF_ALIAS_RE.search(sentence)
        if match is None:
            continue
        alias = _clean_candidate_content(match.group(1))
        if alias:
            return alias, sentence
    return None


def _contextual_alias_content(alias: str) -> str:
    return f"在当前群聊中，当前发送者被称为“{alias}”。"


def _contextual_alias_metadata(alias: str) -> dict[str, Any]:
    return {
        "relation": "alias",
        "subject": "current_sender",
        "alias": alias,
        "portable": False,
    }


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
        key = _normalize_for_dedupe(item.content) + json.dumps(
            item.metadata.get("subject"), sort_keys=True
        )
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

    if not isinstance(data.get("add"), list) or not isinstance(
        data.get("archive"), list
    ):
        raise ValueError("memory dream must contain add and archive arrays")
    additions: list[ExtractedMemory] = []
    raw_additions = data["add"]
    if isinstance(raw_additions, list):
        for raw_item in raw_additions:
            if not isinstance(raw_item, dict):
                raise ValueError("memory addition must be an object")
            content = _clean_candidate_content(str(raw_item.get("content", "")))
            if not content:
                continue
            kind = str(raw_item.get("kind", "fact") or "fact").strip()
            if kind not in _VALID_KINDS:
                kind = "fact"
            audience = str(raw_item.get("audience", "current") or "current")
            audience = audience.strip().casefold()
            if audience not in {"current", "global"} or kind == "summary":
                audience = "current"
            title = _clean_candidate_content(str(raw_item.get("title", "")))
            if not title:
                title = _title_from_content(content)
            evidence_text = _clean_candidate_content(str(raw_item.get("evidence", "")))
            metadata: dict[str, Any] = {
                "extractor": "llm_dream",
                "evidence_turn_ids": raw_item.get("evidence_turn_ids", []),
                "subject": raw_item.get("subject", {}),
            }
            relation = str(raw_item.get("relation", "") or "").strip().casefold()
            subject = str(raw_item.get("subject", "") or "").strip().casefold()
            alias = _clean_candidate_content(str(raw_item.get("alias", "") or ""))
            if relation == "alias" and subject == "current_sender" and alias:
                metadata.update(_contextual_alias_metadata(alias))
            elif (
                relation == "alias"
                and isinstance(raw_item.get("subject"), dict)
                and alias
            ):
                metadata.update(relation="alias", alias=alias, portable=False)
            elif "portable" in raw_item:
                metadata["portable"] = normalize_portable(
                    raw_item.get("portable"), default=True
                )
            additions.append(
                ExtractedMemory(
                    kind=kind,
                    title=title,
                    content=content,
                    audience=audience,
                    confidence=_clamp_float(raw_item.get("confidence"), default=0.65),
                    importance=_clamp_float(raw_item.get("importance"), default=0.5),
                    evidence={"llm_evidence": evidence_text} if evidence_text else {},
                    metadata=metadata,
                )
            )

    archives: list[DreamArchive] = []
    raw_archives = data.get("archive", [])
    if isinstance(raw_archives, list):
        for raw_item in raw_archives:
            if not isinstance(raw_item, dict):
                raise ValueError("memory archive request must be an object")
            item_id = str(raw_item.get("item_id", "") or "").strip()
            reason = _clean_candidate_content(str(raw_item.get("reason", "")))
            if item_id and reason:
                archives.append(
                    DreamArchive(
                        item_id=item_id,
                        reason=reason,
                        evidence_turn_ids=raw_item.get("evidence_turn_ids", []),
                    )
                )

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
