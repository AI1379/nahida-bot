"""Context assembly and budgeting for agent prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog
import yaml

from nahida_bot.agent.memory.markdown import (
    MAX_CONTEXT_MEMORY_CHARS,
    build_memory_context,
    load_workspace_markdown_memory,
)
from nahida_bot.agent.tokenization import Tokenizer, resolve_tokenizer
from nahida_bot.core.logging import log_trace

logger = structlog.get_logger(__name__)


class ReasoningPolicy(Enum):
    """Controls how reasoning content is injected into context history.

    Attributes:
        STRIP: Discard reasoning text, keep only signatures (saves tokens).
        APPEND: Inject reasoning text fully (most complete context).
        BUDGET: Inject when within token budget, otherwise discard (recommended default).
    """

    STRIP = "strip"
    APPEND = "append"
    BUDGET = "budget"


if TYPE_CHECKING:
    from nahida_bot.agent.providers.base import ChatProvider, ModelCapabilities
    from nahida_bot.core.config import ContextConfig

MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True, frozen=True)
class ContextPart:
    """Multimodal content part for provider request context.

    ``type`` values: ``text``, ``image_url``, ``image_base64``, ``image_description``.
    """

    type: str
    text: str = ""
    url: str = ""
    data: str = ""  # base64, only after size/mime validation
    mime_type: str = ""
    media_id: str = ""
    cache_control: str = ""  # "ephemeral" | ""


@dataclass(slots=True, frozen=True)
class ContextMessage:
    """Single message unit used to build provider request context."""

    role: MessageRole
    content: str
    source: str
    metadata: dict[str, object] | None = None

    # Reasoning chain support (Phase 2.8 — all have defaults for backward compat)
    reasoning: str | None = None
    reasoning_signature: str | None = None
    has_redacted_thinking: bool = False

    # Multimodal support (Phase 2.9 — default empty for backward compat)
    parts: list[ContextPart] = field(default_factory=list)


# ── tool-transcript protocol helpers (module level) ────────────────────
#
# These classify assistant ``tool_use`` messages and their paired ``tool``
# results so that any caller that truncates or serializes a message list can
# keep each call/result pair atomic. They are shared by:
#   - ``ContextBuilder`` token-budget trimming (keeps pairs atomic)
#   - ``truncate_messages_to_window`` (turn-count windowing, see below)
#   - ``providers._tool_protocol.sanitize_tool_transcript`` (wire-format safety)
#
# Keeping them at module level (instead of on ``ContextBuilder``) means the
# truncation/sanitization paths do not need to construct a builder to use them.


def has_assistant_tool_calls(message: ContextMessage) -> bool:
    """True if ``message`` is an assistant turn that emitted ≥1 tool call."""
    if message.role != "assistant" or message.metadata is None:
        return False
    raw_tool_calls = message.metadata.get("tool_calls")
    return isinstance(raw_tool_calls, list) and bool(raw_tool_calls)


def assistant_tool_call_ids(message: ContextMessage) -> set[str]:
    """Return the set of non-empty ``id`` values declared by an assistant turn.

    Empty for non-assistant messages or messages without ``tool_calls``. Safe to
    call on any message (the sanitizer feeds it every message in a list).
    """
    if not has_assistant_tool_calls(message):
        return set()
    metadata = message.metadata
    assert metadata is not None  # guaranteed by has_assistant_tool_calls
    raw_tool_calls = metadata.get("tool_calls")
    assert isinstance(raw_tool_calls, list)  # guaranteed non-empty by helper
    ids: set[str] = set()
    for call in raw_tool_calls:
        if isinstance(call, dict):
            call_id = call.get("id")
            if isinstance(call_id, str) and call_id:
                ids.add(call_id)
    return ids


def tool_message_call_id(message: ContextMessage) -> str:
    """Return the ``tool_call_id`` a tool-result message answers, else ``""``."""
    if message.metadata is None:
        return ""
    raw_id = message.metadata.get("tool_call_id")
    return raw_id if isinstance(raw_id, str) and raw_id else ""


def tool_transcript_groups(
    messages: list[ContextMessage],
) -> list[list[ContextMessage]]:
    """Group messages into atomic units that must never be split.

    Each assistant turn that emits tool calls is grouped with the maximal
    contiguous run of ``role == "tool"`` messages that immediately follows it.
    Every other message forms its own single-element group.

    Invariant: a group is either a single non-tool-call message or
    ``[assistant-with-calls, tool, tool, …]``. An orphan ``tool`` message
    (no preceding assistant-with-calls) becomes its own singleton group, which
    is what lets ``truncate_messages_to_window`` detect and drop it.
    """
    groups: list[list[ContextMessage]] = []
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        if not has_assistant_tool_calls(message):
            groups.append([message])
            index += 1
            continue

        group = [message]
        index += 1
        while index < total and messages[index].role == "tool":
            group.append(messages[index])
            index += 1
        groups.append(group)

    return groups


def truncate_messages_to_window(
    messages: list[ContextMessage],
    max_messages: int,
) -> list[ContextMessage]:
    """Window ``messages`` to at most ``max_messages`` without splitting pairs.

    Replacement for the blind ``messages[-max_messages:]`` slice. Messages are
    grouped via :func:`tool_transcript_groups` and a newest-first window of
    *complete* groups is kept so that no ``tool_result`` survives without its
    originating assistant ``tool_use`` in the window.

    Rules:
    - The newest group is always retained (it is the active turn); even if a
      single group alone exceeds ``max_messages`` it is kept whole rather than
      split — token limits are enforced downstream by ``ContextBuilder``.
    - Older groups are added newest-first while the running total stays within
      ``max_messages``; the first group that would overflow is dropped along
      with everything older.
    - As a belt-and-suspenders final step, any residual ``tool`` message whose
      ``tool_call_id`` is not declared by an assistant in the window is dropped
      (defends against malformed input).

    For histories with no tool calls this degenerates exactly to
    ``messages[-max_messages:]``.

    Note: this deliberately does NOT call ``transcript.repair_pairs`` — that
    function assumes single-run structure and would synthesize spurious
    interrupted results for calls whose results legitimately sit just outside
    the window. Post-truncation cleanup is the job of
    ``providers._tool_protocol.sanitize_tool_transcript`` (drop-only).
    """
    if max_messages <= 0 or not messages:
        return []

    groups = tool_transcript_groups(messages)

    kept_reversed: list[list[ContextMessage]] = []
    running = 0
    for group in reversed(groups):
        group_size = len(group)
        # The newest group (first iteration) is always kept, even if it alone
        # exceeds max_messages — dropping the active turn would be worse than a
        # small overrun (token budget is enforced downstream).
        if running == 0:
            kept_reversed.append(group)
            running = group_size
            continue
        if running + group_size <= max_messages:
            kept_reversed.append(group)
            running += group_size
        # else: this group would overflow — drop it and stop (older groups are
        # even larger-cumulatively, so they're dropped too by not continuing).

    kept = [message for group in reversed(kept_reversed) for message in group]

    # Belt-and-suspenders: drop any orphan tool result whose call was not kept.
    declared: set[str] = set()
    for message in kept:
        if has_assistant_tool_calls(message):
            declared |= assistant_tool_call_ids(message)
    cleaned: list[ContextMessage] = []
    dropped_orphans = 0
    for message in kept:
        if message.role == "tool" and tool_message_call_id(message) not in declared:
            dropped_orphans += 1
            continue
        cleaned.append(message)
    if dropped_orphans:
        logger.warning(
            "context.truncated_window_dropped_orphan_tool_results",
            dropped_orphan_tool_result_count=dropped_orphans,
            window_size=len(cleaned),
            max_messages=max_messages,
        )
    return cleaned


@dataclass(slots=True, frozen=True)
class SkillInfo:
    """Lightweight metadata for a workspace skill (frontmatter only, no body)."""

    name: str
    description: str
    file_path: Path  # absolute path to SKILL.md


class SkillCatalog:
    """Load and query workspace skills by name or scan for catalog listing.

    Reuses the same on-disk layout as ``ContextBuilder.load_workspace_skills``:
    ``{workspace_root}/{directory}/<skill-name>/SKILL.md`` with optional
    YAML frontmatter (``name``, ``description`` fields).

    Static methods — no instance state needed.
    """

    _skill_directories: tuple[str, ...] = (".agents/skills", "skills")

    # ── public helpers ────────────────────────────────────

    @classmethod
    def scan_catalog(cls, workspace_root: Path) -> list[SkillInfo]:
        """Return lightweight (name, description, path) for every workspace skill."""
        seen: dict[str, SkillInfo] = {}
        for directory in cls._skill_directories:
            root = workspace_root / directory
            if not root.exists() or not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                raw = skill_file.read_text(encoding="utf-8").strip()
                if not raw:
                    continue
                meta, _body = cls._parse_frontmatter(raw)
                name = str(meta.get("name") or skill_file.parent.name).strip()
                description = str(meta.get("description", "")).strip()
                seen[name] = SkillInfo(
                    name=name,
                    description=description,
                    file_path=skill_file,
                )
        return [seen[name] for name in sorted(seen)]

    @classmethod
    def build_catalog_message(cls, workspace_root: Path) -> "ContextMessage | None":
        """Build a compact system message listing available skills by name+description."""
        skills = cls.scan_catalog(workspace_root)
        if not skills:
            return None

        lines: list[str] = [
            "Available workspace skills (invoke with /<name> or via the skill tool):"
        ]
        for skill in skills:
            desc = skill.description[:120] if skill.description else "(no description)"
            lines.append(f"- {skill.name}: {desc}")

        from nahida_bot.agent.context import ContextMessage  # avoid circular

        return ContextMessage(
            role="system",
            source="skill_catalog",
            content="\n".join(lines),
        )

    @classmethod
    def load_skill_content(cls, workspace_root: Path, skill_name: str) -> str | None:
        """Load the full formatted content for a skill by name, or ``None``."""
        catalog = cls.scan_catalog(workspace_root)
        info = next((s for s in catalog if s.name == skill_name.lower()), None)
        # Try case-sensitive fallback
        if info is None:
            info = next((s for s in catalog if s.name == skill_name), None)
        if info is None:
            return None

        raw = info.file_path.read_text(encoding="utf-8").strip()
        metadata, body = cls._parse_frontmatter(raw)
        name = str(metadata.get("name") or info.name).strip()
        description = str(metadata.get("description", "")).strip()

        parts = [f"# Skill: {name}"]
        if description:
            parts.append(f"Description: {description}")
        parts.append(body.strip())
        return "\n\n".join(part for part in parts if part)

    @classmethod
    def list_skill_names(cls, workspace_root: Path) -> set[str]:
        """Return all skill names for the workspace."""
        return {s.name for s in cls.scan_catalog(workspace_root)}

    # ── internal helpers ──────────────────────────────────

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[dict[str, object], str]:
        """Split YAML frontmatter from a SKILL.md string.

        Returns ``(metadata, body)``.  If frontmatter is absent or malformed
        the metadata dict will be empty and the body is the whole raw text.
        """
        if not raw.startswith("---"):
            return {}, raw
        _, sep, rest = raw.partition("\n")
        if not sep:
            return {}, raw
        frontmatter, sep2, body = rest.partition("\n---")
        if not sep2:
            return {}, raw
        try:
            parsed = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:
            return {}, raw
        if not isinstance(parsed, dict):
            return {}, raw
        return {str(key): value for key, value in parsed.items()}, body.lstrip()


@dataclass(slots=True, frozen=True)
class ContextBudget:
    """Budget settings for context assembly."""

    max_tokens: int = 272000
    reserved_tokens: int = 10000
    auto_compact_token_limit: int | None = None
    max_chars: int | None = None
    reserved_chars: int = 0
    summary_max_chars: int = 2000

    # Reasoning chain budgeting (Phase 2.8)
    reasoning_policy: ReasoningPolicy = ReasoningPolicy.BUDGET
    max_reasoning_tokens: int = 2000

    @property
    def usable_tokens(self) -> int:
        """Token-like units available for prompt context.

        `max_chars` / `reserved_chars` are retained for backward compatibility,
        but token budgeting should be preferred for new code.
        """
        if self.max_chars is not None:
            usable = self.max_chars - self.reserved_chars
            return usable if usable > 0 else 0

        usable = self.max_tokens - self.reserved_tokens
        return usable if usable > 0 else 0

    @property
    def soft_token_limit(self) -> int:
        """Token threshold that triggers proactive context compaction.

        When unset, manual budgets keep the historical behavior and compact only
        at the hard usable budget.
        """
        if self.auto_compact_token_limit is None:
            return self.usable_tokens
        return min(max(0, self.auto_compact_token_limit), self.usable_tokens)


def build_context_budget(
    cfg: ContextConfig,
    *,
    capabilities: ModelCapabilities | None = None,
) -> ContextBudget:
    """Build a ContextBudget dataclass from a ContextConfig Pydantic model."""
    _policy_map = {
        "strip": ReasoningPolicy.STRIP,
        "append": ReasoningPolicy.APPEND,
        "budget": ReasoningPolicy.BUDGET,
    }
    policy = _policy_map.get(cfg.reasoning_policy, ReasoningPolicy.BUDGET)
    max_tokens = cfg.max_tokens
    reserved_tokens = cfg.reserved_tokens
    auto_compact_token_limit: int | None = None

    if capabilities is not None:
        context_window = capabilities.resolved_context_window()
        if context_window is not None:
            max_tokens = context_window
            percent = capabilities.normalized_effective_context_window_percent()
            usable = (max_tokens * percent) // 100
            reserved_tokens = max(0, max_tokens - usable)
        auto_compact_token_limit = capabilities.resolved_auto_compact_token_limit()

    if auto_compact_token_limit is None and cfg.max_chars is None:
        auto_compact_token_limit = (max_tokens * 9) // 10

    return ContextBudget(
        max_tokens=max_tokens,
        reserved_tokens=reserved_tokens,
        auto_compact_token_limit=auto_compact_token_limit,
        max_chars=cfg.max_chars,
        reserved_chars=cfg.reserved_chars,
        summary_max_chars=cfg.summary_max_chars,
        reasoning_policy=policy,
        max_reasoning_tokens=cfg.max_reasoning_tokens,
    )


class ContextBuilder:
    """Build context from system prompt, workspace instructions, and history."""

    instruction_filenames: tuple[str, ...] = ("AGENTS.md", "SOUL.md", "USER.md")
    skill_directories: tuple[str, ...] = (".agents/skills", "skills")

    def __init__(
        self,
        budget: ContextBudget | None = None,
        *,
        provider: ChatProvider | None = None,
        tokenizer: Tokenizer | None = None,
        fallback_tokenizer: Tokenizer | None = None,
    ) -> None:
        """Create context builder with optional provider/tokenizer strategy."""
        self.budget = budget or ContextBudget()
        self.tokenizer = resolve_tokenizer(
            provider_tokenizer=provider.tokenizer if provider is not None else None,
            tokenizer=tokenizer,
            fallback_tokenizer=fallback_tokenizer,
        )

    def load_workspace_instructions(self, workspace_root: Path) -> list[ContextMessage]:
        """Load instruction files in strict priority order."""
        messages: list[ContextMessage] = []
        for filename in self.instruction_filenames:
            path = workspace_root / filename
            if not path.exists() or not path.is_file():
                continue

            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            messages.append(
                ContextMessage(
                    role="system",
                    source=f"workspace_instruction:{filename}",
                    content=content,
                )
            )
        return messages

    def load_workspace_skills(self, workspace_root: Path) -> list[ContextMessage]:
        """Load AgentSkills-compatible workspace skills.

        Each skill is a directory containing ``SKILL.md`` with optional YAML
        frontmatter. If the same skill exists in multiple workspace locations,
        later locations in ``skill_directories`` take precedence.
        """
        skills: dict[str, ContextMessage] = {}
        for directory in self.skill_directories:
            root = workspace_root / directory
            if not root.exists() or not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                parsed = self._parse_skill_file(skill_file, workspace_root)
                if parsed is not None:
                    skills[parsed.source.removeprefix("workspace_skill:")] = parsed
        return [skills[name] for name in sorted(skills)]

    def load_workspace_memory(self, workspace_root: Path) -> ContextMessage | None:
        """Load bounded Markdown memory from the active workspace."""
        entries = load_workspace_markdown_memory(
            workspace_root,
            max_chars=MAX_CONTEXT_MEMORY_CHARS,
        )
        content = build_memory_context(entries, max_chars=MAX_CONTEXT_MEMORY_CHARS)
        if not content:
            return None
        return ContextMessage(
            role="system",
            source="workspace_memory:markdown",
            content=content,
            metadata={
                "memory_paths": [entry.path for entry in entries],
                "memory_backend": "markdown",
            },
        )

    def build_context(
        self,
        *,
        system_prompt: str,
        workspace_root: Path | None = None,
        history_messages: list[ContextMessage] | None = None,
        tool_messages: list[ContextMessage] | None = None,
        protected_messages: list[ContextMessage] | None = None,
    ) -> list[ContextMessage]:
        """Build ordered context and apply budget policy.

        Order is fixed as:
        1. System baseline
        2. Workspace instructions (AGENTS.md -> SOUL.md -> USER.md)
        3. History messages
        4. Tool messages
        5. Protected messages, usually the active user turn and live tool transcript
        """
        prefix_messages: list[ContextMessage] = [
            ContextMessage(
                role="system",
                source="system_baseline",
                content=system_prompt,
            )
        ]

        if workspace_root is not None:
            prefix_messages.extend(self.load_workspace_instructions(workspace_root))
            catalog_msg = SkillCatalog.build_catalog_message(workspace_root)
            if catalog_msg is not None:
                prefix_messages.append(catalog_msg)
            memory_message = self.load_workspace_memory(workspace_root)
            if memory_message is not None:
                prefix_messages.append(memory_message)

        optional_messages = [*(history_messages or []), *(tool_messages or [])]
        protected = list(protected_messages or [])
        dynamic_messages = [*optional_messages, *protected]
        merged = [*prefix_messages, *dynamic_messages]

        merged_tokens = self._estimate_tokens(merged)
        soft_token_limit = self.budget.soft_token_limit
        logger.debug(
            "context_builder.build_start",
            prefix_count=len(prefix_messages),
            dynamic_count=len(dynamic_messages),
            merged_count=len(merged),
            merged_tokens=merged_tokens,
            usable_tokens=self.budget.usable_tokens,
            soft_token_limit=soft_token_limit,
            roles=[m.role for m in merged],
            sources=[m.source for m in merged],
        )
        log_trace(
            logger,
            "context_builder.message_trace",
            messages=[
                {
                    "index": idx,
                    "role": message.role,
                    "source": message.source,
                    "content_chars": len(message.content),
                    "content_preview": message.content[:200],
                    "part_types": [part.type for part in message.parts],
                    "has_reasoning": bool(message.reasoning),
                    "has_reasoning_signature": bool(message.reasoning_signature),
                }
                for idx, message in enumerate(merged)
            ],
        )

        if merged_tokens <= soft_token_limit:
            logger.debug(
                "context_builder.build_done",
                reason="within_soft_budget",
                message_count=len(merged),
                estimated_tokens=merged_tokens,
                usable_tokens=self.budget.usable_tokens,
                soft_token_limit=soft_token_limit,
            )
            return merged

        if protected:
            return self._build_context_with_protected_messages(
                prefix_messages=prefix_messages,
                optional_messages=optional_messages,
                protected_messages=protected,
                token_limit=soft_token_limit,
            )

        windowed_dynamic, dropped = self._sliding_window(
            dynamic_messages,
            prefix_messages,
            token_limit=soft_token_limit,
        )
        windowed = [*prefix_messages, *windowed_dynamic]
        logger.debug(
            "context_builder.sliding_window_applied",
            kept_dynamic_count=len(windowed_dynamic),
            dropped_count=len(dropped),
            windowed_tokens=self._estimate_tokens(windowed),
            usable_tokens=self.budget.usable_tokens,
            dropped_roles=[m.role for m in dropped],
            dropped_sources=[m.source for m in dropped],
        )

        if not dropped:
            return windowed

        summary_message = self._build_summary_message(dropped)
        with_summary = self._fit_summary_with_window(
            prefix_messages=prefix_messages,
            windowed_dynamic=windowed_dynamic,
            summary_message=summary_message,
            token_limit=soft_token_limit,
        )
        if with_summary is not None:
            logger.debug(
                "context_builder.build_done",
                reason="summary_fit",
                message_count=len(with_summary),
                estimated_tokens=self._estimate_tokens(with_summary),
                summary_chars=len(summary_message.content),
            )
            return with_summary

        compact_summary = self._truncate_message_to_budget(
            summary_message,
            soft_token_limit - self._estimate_tokens(windowed),
        )
        if compact_summary is None:
            logger.debug(
                "context_builder.build_done",
                reason="window_without_summary",
                message_count=len(windowed),
                estimated_tokens=self._estimate_tokens(windowed),
            )
            return windowed

        maybe_summarized = self._fit_summary_with_window(
            prefix_messages=prefix_messages,
            windowed_dynamic=windowed_dynamic,
            summary_message=compact_summary,
            token_limit=soft_token_limit,
        )
        if maybe_summarized is not None:
            logger.debug(
                "context_builder.build_done",
                reason="compact_summary_fit",
                message_count=len(maybe_summarized),
                estimated_tokens=self._estimate_tokens(maybe_summarized),
                summary_chars=len(compact_summary.content),
            )
            return maybe_summarized

        logger.debug(
            "context_builder.build_done",
            reason="window_after_summary_failed",
            message_count=len(windowed),
            estimated_tokens=self._estimate_tokens(windowed),
        )
        return windowed

    def _build_context_with_protected_messages(
        self,
        *,
        prefix_messages: list[ContextMessage],
        optional_messages: list[ContextMessage],
        protected_messages: list[ContextMessage],
        token_limit: int,
    ) -> list[ContextMessage]:
        """Build context while preserving the active turn suffix.

        Agent loops need the latest user request and any immediately following
        assistant/tool transcript to survive budgeting. If a large tool result
        blows the budget, truncate protected message content before falling back
        to dropping older optional history.
        """
        protected_fit = self._fit_protected_messages(
            prefix_messages=prefix_messages,
            protected_messages=protected_messages,
        )
        windowed_optional, dropped = self._sliding_window_with_suffix(
            optional_messages,
            prefix_messages=prefix_messages,
            suffix_messages=protected_fit,
            token_limit=token_limit,
        )
        windowed = [*prefix_messages, *windowed_optional, *protected_fit]

        logger.debug(
            "context_builder.protected_window_applied",
            optional_kept_count=len(windowed_optional),
            protected_count=len(protected_fit),
            dropped_count=len(dropped),
            windowed_tokens=self._estimate_tokens(windowed),
            usable_tokens=self.budget.usable_tokens,
            dropped_roles=[m.role for m in dropped],
            dropped_sources=[m.source for m in dropped],
            protected_roles=[m.role for m in protected_fit],
            protected_sources=[m.source for m in protected_fit],
        )

        if not dropped:
            return windowed

        summary_message = self._build_summary_message(dropped)
        with_summary = self._fit_summary_with_window(
            prefix_messages=prefix_messages,
            windowed_dynamic=windowed_optional,
            summary_message=summary_message,
            suffix_messages=protected_fit,
            token_limit=token_limit,
        )
        if with_summary is not None:
            logger.debug(
                "context_builder.build_done",
                reason="protected_summary_fit",
                message_count=len(with_summary),
                estimated_tokens=self._estimate_tokens(with_summary),
                summary_chars=len(summary_message.content),
            )
            return with_summary

        compact_summary = self._truncate_message_to_budget(
            summary_message,
            token_limit - self._estimate_tokens(windowed),
        )
        if compact_summary is None:
            logger.debug(
                "context_builder.build_done",
                reason="protected_window_without_summary",
                message_count=len(windowed),
                estimated_tokens=self._estimate_tokens(windowed),
            )
            return windowed

        maybe_summarized = self._fit_summary_with_window(
            prefix_messages=prefix_messages,
            windowed_dynamic=windowed_optional,
            summary_message=compact_summary,
            suffix_messages=protected_fit,
            token_limit=token_limit,
        )
        if maybe_summarized is not None:
            logger.debug(
                "context_builder.build_done",
                reason="protected_compact_summary_fit",
                message_count=len(maybe_summarized),
                estimated_tokens=self._estimate_tokens(maybe_summarized),
                summary_chars=len(compact_summary.content),
            )
            return maybe_summarized

        logger.debug(
            "context_builder.build_done",
            reason="protected_window_after_summary_failed",
            message_count=len(windowed),
            estimated_tokens=self._estimate_tokens(windowed),
        )
        return windowed

    def _fit_protected_messages(
        self,
        *,
        prefix_messages: list[ContextMessage],
        protected_messages: list[ContextMessage],
    ) -> list[ContextMessage]:
        """Fit protected active-turn messages by truncating large contents first."""
        if (
            self._estimate_tokens([*prefix_messages, *protected_messages])
            <= self.budget.usable_tokens
        ):
            return protected_messages

        compacted = list(protected_messages)
        truncated: list[dict[str, object]] = []

        while (
            self._estimate_tokens([*prefix_messages, *compacted])
            > self.budget.usable_tokens
        ):
            made_progress = False
            for index in self._protected_truncation_order(compacted):
                message = compacted[index]
                message_tokens = self._estimate_tokens([message])
                without_message = [
                    *prefix_messages,
                    *compacted[:index],
                    *compacted[index + 1 :],
                ]
                remaining = self.budget.usable_tokens - self._estimate_tokens(
                    without_message
                )
                if remaining <= 0 or message_tokens <= remaining:
                    continue

                truncated_message = self._truncate_message_to_budget(
                    message,
                    remaining,
                    truncation_marker="\n[truncated due to context budget]",
                    allow_empty=True,
                )
                if truncated_message is None:
                    truncated_message = self._truncate_message_to_budget(
                        message,
                        remaining,
                        allow_empty=True,
                    )
                if (
                    truncated_message is None
                    or truncated_message.content == message.content
                ):
                    continue

                compacted[index] = truncated_message
                truncated.append(
                    {
                        "role": message.role,
                        "source": message.source,
                        "original_chars": len(message.content),
                        "truncated_chars": len(truncated_message.content),
                    }
                )
                made_progress = True
                break

            if not made_progress:
                break

        if (
            self._estimate_tokens([*prefix_messages, *compacted])
            <= self.budget.usable_tokens
        ):
            if truncated:
                logger.warning(
                    "context_builder.protected_messages_truncated",
                    truncated=truncated,
                    protected_roles=[m.role for m in protected_messages],
                    protected_sources=[m.source for m in protected_messages],
                )
            return compacted

        kept, dropped = self._sliding_window(compacted, prefix_messages)
        logger.warning(
            "context_builder.protected_messages_dropped",
            reason="protected_suffix_exceeds_budget",
            kept_roles=[m.role for m in kept],
            kept_sources=[m.source for m in kept],
            dropped_roles=[m.role for m in dropped],
            dropped_sources=[m.source for m in dropped],
            truncated=truncated,
            usable_tokens=self.budget.usable_tokens,
        )
        return kept

    def _protected_truncation_order(
        self,
        messages: list[ContextMessage],
    ) -> list[int]:
        """Return protected message indices ordered by safest truncation first."""
        buckets: list[list[int]] = [
            [
                index
                for index, message in enumerate(messages)
                if message.role == "tool" and message.content
            ],
            [
                index
                for index, message in enumerate(messages)
                if message.role == "assistant"
                and has_assistant_tool_calls(message)
                and message.content
            ],
            [
                index
                for index, message in enumerate(messages)
                if message.role == "assistant"
                and not has_assistant_tool_calls(message)
                and message.content
            ],
            [
                index
                for index, message in enumerate(messages)
                if message.role == "user" and message.content
            ],
        ]

        ordered: list[int] = []
        for bucket in buckets:
            ordered.extend(
                sorted(
                    bucket,
                    key=lambda index: len(messages[index].content),
                    reverse=True,
                )
            )
        return ordered

    def _sliding_window(
        self,
        dynamic_messages: list[ContextMessage],
        prefix_messages: list[ContextMessage],
        *,
        token_limit: int | None = None,
    ) -> tuple[list[ContextMessage], list[ContextMessage]]:
        """Apply newest-first retention to dynamic messages."""
        return self._sliding_window_with_suffix(
            dynamic_messages,
            prefix_messages=prefix_messages,
            suffix_messages=[],
            token_limit=token_limit,
        )

    def _sliding_window_with_suffix(
        self,
        dynamic_messages: list[ContextMessage],
        *,
        prefix_messages: list[ContextMessage],
        suffix_messages: list[ContextMessage],
        token_limit: int | None = None,
    ) -> tuple[list[ContextMessage], list[ContextMessage]]:
        """Apply newest-first retention with a required suffix already reserved."""
        message_groups = tool_transcript_groups(dynamic_messages)
        kept_groups_reversed: list[list[ContextMessage]] = []
        dropped_groups_reversed: list[list[ContextMessage]] = []
        limit = self.budget.usable_tokens if token_limit is None else token_limit

        current_size = self._estimate_tokens([*prefix_messages, *suffix_messages])
        for group in reversed(message_groups):
            group_size = self._estimate_tokens(group)
            if current_size + group_size <= limit:
                kept_groups_reversed.append(group)
                current_size += group_size
            else:
                dropped_groups_reversed.append(group)

        kept = [
            message for group in reversed(kept_groups_reversed) for message in group
        ]
        dropped = [
            message for group in reversed(dropped_groups_reversed) for message in group
        ]
        return kept, dropped

    def _build_summary_message(
        self, dropped_messages: list[ContextMessage]
    ) -> ContextMessage:
        """Create a compact summary entry for dropped context."""
        lines: list[str] = []
        for message in dropped_messages:
            normalized = " ".join(message.content.split())
            lines.append(f"- {message.role}: {normalized[:120]}")

        summary_body = "\n".join(lines)
        summary = f"Compressed summary of older context:\n{summary_body}"
        summary = summary[: self.budget.summary_max_chars]
        return ContextMessage(
            role="system",
            source="history_summary",
            content=summary,
        )

    def _truncate_message_to_budget(
        self,
        message: ContextMessage,
        remaining_budget_tokens: int,
        *,
        truncation_marker: str = "",
        allow_empty: bool = False,
    ) -> ContextMessage | None:
        """Trim a message content to fit a remaining token budget."""
        overhead_tokens = self._estimate_tokens([replace(message, content="")])
        if remaining_budget_tokens < overhead_tokens:
            return None
        if not allow_empty and remaining_budget_tokens <= overhead_tokens + 4:
            return None

        content = message.content
        if not content:
            return message

        low = 0
        high = len(content)
        best: str | None = None

        while low <= high:
            mid = (low + high) // 2
            candidate_content = content[:mid]
            if truncation_marker and mid < len(content):
                candidate_content += truncation_marker
            candidate = replace(message, content=candidate_content)
            size = self._estimate_tokens([candidate])
            if size <= remaining_budget_tokens:
                best = candidate_content
                low = mid + 1
            else:
                high = mid - 1

        if best is None or (best == "" and not allow_empty):
            return None

        return replace(message, content=best)

    def _fit_summary_with_window(
        self,
        *,
        prefix_messages: list[ContextMessage],
        windowed_dynamic: list[ContextMessage],
        summary_message: ContextMessage,
        suffix_messages: list[ContextMessage] | None = None,
        token_limit: int | None = None,
    ) -> list[ContextMessage] | None:
        """Try to include summary by dropping oldest retained dynamic messages."""
        suffix = list(suffix_messages or [])
        limit = self.budget.usable_tokens if token_limit is None else token_limit
        candidate_groups = tool_transcript_groups(windowed_dynamic)
        while True:
            candidate_dynamic = [
                message for group in candidate_groups for message in group
            ]
            candidate = [
                *prefix_messages,
                summary_message,
                *candidate_dynamic,
                *suffix,
            ]
            if self._estimate_tokens(candidate) <= limit:
                return candidate
            if not candidate_groups:
                return None
            if self._is_tool_transcript_group(candidate_groups[0]):
                return None
            candidate_groups = candidate_groups[1:]

    def _is_tool_transcript_group(self, group: list[ContextMessage]) -> bool:
        return any(message.role == "tool" for message in group) or any(
            has_assistant_tool_calls(message) for message in group
        )

    def _estimate_tokens(self, messages: list[ContextMessage]) -> int:
        """Estimate context size using configured tokenizer strategy.

        TODO: Metadata JSON is re-serialized on every call. The binary-search
        truncation path calls this repeatedly for the same messages. Cache the
        serialized form on ContextMessage or compute metadata overhead once.
        """
        total = 0
        for message in messages:
            metadata_serialized = (
                json.dumps(message.metadata, sort_keys=True)
                if message.metadata is not None
                else ""
            )
            parts_serialized = self._serialize_parts_for_budget(message)
            reasoning_serialized = self._serialize_reasoning_for_budget(message)
            serialized = (
                f"role:{message.role}\n"
                f"source:{message.source}\n"
                f"content:{message.content}\n"
                f"{reasoning_serialized}"
                f"parts:{parts_serialized}\n"
                f"metadata:{metadata_serialized}"
            )
            total += self.tokenizer.count_tokens(serialized)
        return total

    @staticmethod
    def _serialize_reasoning_for_budget(message: ContextMessage) -> str:
        parts: list[str] = []
        if message.reasoning:
            parts.append(f"reasoning:{message.reasoning}")
        if message.reasoning_signature:
            parts.append(f"reasoning_signature:{message.reasoning_signature}")
        if message.has_redacted_thinking:
            parts.append("has_redacted_thinking:true")
        if not parts:
            return ""
        return "\n".join(parts) + "\n"

    def _serialize_parts_for_budget(self, message: ContextMessage) -> str:
        if not message.parts:
            return ""
        return json.dumps(
            [
                {
                    "type": part.type,
                    "text": part.text,
                    "url": part.url,
                    "data": part.data,
                    "mime_type": part.mime_type,
                    "media_id": part.media_id,
                }
                for part in message.parts
            ],
            sort_keys=True,
        )

    def _parse_skill_file(
        self, skill_file: Path, workspace_root: Path
    ) -> ContextMessage | None:
        raw = skill_file.read_text(encoding="utf-8").strip()
        if not raw:
            return None

        metadata, body = self._split_skill_frontmatter(raw)
        skill_name_raw = metadata.get("name") or skill_file.parent.name
        skill_name = str(skill_name_raw).strip() or skill_file.parent.name
        description_raw = metadata.get("description", "")
        description = str(description_raw).strip()

        parts = [f"# Skill: {skill_name}"]
        if description:
            parts.append(f"Description: {description}")
        parts.append(body.strip())
        relative_path = skill_file.relative_to(workspace_root).as_posix()
        return ContextMessage(
            role="system",
            source=f"workspace_skill:{skill_name}",
            content="\n\n".join(part for part in parts if part),
            metadata={
                "skill_name": skill_name,
                "description": description,
                "path": relative_path,
            },
        )

    def _split_skill_frontmatter(self, raw: str) -> tuple[dict[str, object], str]:
        if not raw.startswith("---"):
            return {}, raw

        _, separator, rest = raw.partition("\n")
        if not separator:
            return {}, raw
        frontmatter, separator, body = rest.partition("\n---")
        if not separator:
            return {}, raw

        try:
            parsed = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:
            return {}, raw
        if not isinstance(parsed, dict):
            return {}, raw
        return {str(key): value for key, value in parsed.items()}, body.lstrip()
