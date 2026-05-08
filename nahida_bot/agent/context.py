"""Context assembly and budgeting for agent prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog
import yaml

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
    from nahida_bot.agent.providers.base import ChatProvider
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


@dataclass(slots=True, frozen=True)
class ContextBudget:
    """Budget settings for context assembly."""

    max_tokens: int = 8000
    reserved_tokens: int = 1000
    max_chars: int | None = None
    reserved_chars: int = 0
    summary_max_chars: int = 600

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


def build_context_budget(cfg: ContextConfig) -> ContextBudget:
    """Build a ContextBudget dataclass from a ContextConfig Pydantic model."""
    _policy_map = {
        "strip": ReasoningPolicy.STRIP,
        "append": ReasoningPolicy.APPEND,
        "budget": ReasoningPolicy.BUDGET,
    }
    policy = _policy_map.get(cfg.reasoning_policy, ReasoningPolicy.BUDGET)
    return ContextBudget(
        max_tokens=cfg.max_tokens,
        reserved_tokens=cfg.reserved_tokens,
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

    def build_context(
        self,
        *,
        system_prompt: str,
        workspace_root: Path | None = None,
        history_messages: list[ContextMessage] | None = None,
        tool_messages: list[ContextMessage] | None = None,
    ) -> list[ContextMessage]:
        """Build ordered context and apply budget policy.

        Order is fixed as:
        1. System baseline
        2. Workspace instructions (AGENTS.md -> SOUL.md -> USER.md)
        3. History messages
        4. Tool messages
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
            prefix_messages.extend(self.load_workspace_skills(workspace_root))

        dynamic_messages = [*(history_messages or []), *(tool_messages or [])]
        merged = [*prefix_messages, *dynamic_messages]

        merged_tokens = self._estimate_tokens(merged)
        logger.debug(
            "context_builder.build_start",
            prefix_count=len(prefix_messages),
            dynamic_count=len(dynamic_messages),
            merged_count=len(merged),
            merged_tokens=merged_tokens,
            usable_tokens=self.budget.usable_tokens,
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

        if merged_tokens <= self.budget.usable_tokens:
            logger.debug(
                "context_builder.build_done",
                reason="within_budget",
                message_count=len(merged),
                estimated_tokens=merged_tokens,
                usable_tokens=self.budget.usable_tokens,
            )
            return merged

        windowed_dynamic, dropped = self._sliding_window(
            dynamic_messages, prefix_messages
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
            self.budget.usable_tokens - self._estimate_tokens(windowed),
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

    def _sliding_window(
        self,
        dynamic_messages: list[ContextMessage],
        prefix_messages: list[ContextMessage],
    ) -> tuple[list[ContextMessage], list[ContextMessage]]:
        """Apply newest-first retention to dynamic messages."""
        kept_reversed: list[ContextMessage] = []
        dropped: list[ContextMessage] = []

        current_size = self._estimate_tokens(prefix_messages)
        for message in reversed(dynamic_messages):
            message_size = self._estimate_tokens([message])
            if current_size + message_size <= self.budget.usable_tokens:
                kept_reversed.append(message)
                current_size += message_size
            else:
                dropped.append(message)

        kept = list(reversed(kept_reversed))
        dropped = list(reversed(dropped))
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
    ) -> ContextMessage | None:
        """Trim a message content to fit a remaining token budget."""
        overhead_tokens = self._estimate_tokens(
            [ContextMessage(role=message.role, source=message.source, content="")]
        )
        if remaining_budget_tokens <= overhead_tokens + 4:
            return None

        content = message.content
        if not content:
            return message

        low = 0
        high = len(content)
        best = ""

        while low <= high:
            mid = (low + high) // 2
            candidate_content = content[:mid]
            candidate = ContextMessage(
                role=message.role,
                source=message.source,
                content=candidate_content,
            )
            size = self._estimate_tokens([candidate])
            if size <= remaining_budget_tokens:
                best = candidate_content
                low = mid + 1
            else:
                high = mid - 1

        if not best:
            return None

        return ContextMessage(
            role=message.role,
            source=message.source,
            content=best,
        )

    def _fit_summary_with_window(
        self,
        *,
        prefix_messages: list[ContextMessage],
        windowed_dynamic: list[ContextMessage],
        summary_message: ContextMessage,
    ) -> list[ContextMessage] | None:
        """Try to include summary by dropping oldest retained dynamic messages."""
        candidate_dynamic = list(windowed_dynamic)
        while True:
            candidate = [*prefix_messages, summary_message, *candidate_dynamic]
            if self._estimate_tokens(candidate) <= self.budget.usable_tokens:
                return candidate
            if not candidate_dynamic:
                return None
            candidate_dynamic = candidate_dynamic[1:]

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
            serialized = (
                f"role:{message.role}\n"
                f"source:{message.source}\n"
                f"content:{message.content}\n"
                f"parts:{parts_serialized}\n"
                f"metadata:{metadata_serialized}"
            )
            total += self.tokenizer.count_tokens(serialized)
        return total

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
