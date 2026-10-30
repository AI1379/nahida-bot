"""Checkpointed conversation processing; scheduling is only a trigger."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import structlog

from nahida_bot.agent.memory.attribution import EvidenceRef
from nahida_bot.agent.memory.consolidation import MemoryConsolidator
from nahida_bot.agent.memory.models import ConsolidationOutcome, MemoryRecord
from nahida_bot.agent.memory.scope import resolve_scope_from_session

logger = structlog.get_logger(__name__)


def build_dream_batch(
    records: list[MemoryRecord], *, max_chars: int
) -> tuple[list[MemoryRecord], str]:
    """Preserve entire records and identity; unconsumed records remain pending."""
    selected: list[MemoryRecord] = []
    lines: list[str] = []
    size = 0
    for record in records:
        line = json.dumps(
            {**asdict(EvidenceRef.from_record(record)), "content": record.turn.content},
            ensure_ascii=False,
        )
        if size + len(line) + 1 > max_chars:
            if not selected:
                raise ValueError(
                    f"turn {record.turn_id} exceeds dreaming input budget; review required"
                )
            break
        selected.append(record)
        lines.append(line)
        size += len(line) + 1
    return selected, "\n".join(lines)


class MemoryDreamingService:
    def __init__(self, memory: Any, *, app_name: str = "the assistant") -> None:
        self._memory = memory
        self._app_name = app_name

    async def run_session(
        self,
        session_id: str,
        *,
        provider: Any,
        model: str | None = None,
        workspace_id: str | None = None,
        turn_limit: int = 40,
        max_chars: int = 12000,
    ) -> ConsolidationOutcome:
        meta = await self._memory.get_session_meta(session_id)
        last_id = int(meta.get("memory_dream_last_turn_id") or 0)
        try:
            records = await self._memory.get_turns_after(
                session_id, last_id, limit=turn_limit
            )
            if not records:
                return ConsolidationOutcome("empty", last_turn_id=last_id)
            batch, conversation = build_dream_batch(records, max_chars=max_chars)
            logger.debug(
                "scheduler.memory_dreaming_session_start",
                session_id=session_id,
                new_turns=len(batch),
                last_turn_id=last_id,
                model=model or "",
            )
            scope_type, scope_id = resolve_scope_from_session(session_id)
            consolidator = MemoryConsolidator(
                self._memory,
                app_name=self._app_name,
                default_scope_type=scope_type,
                default_scope_id=scope_id,
            )
            changes = await consolidator.prepare_turn(
                session_id=session_id,
                user_message=conversation,
                workspace_id=workspace_id,
                dream_provider=provider,
                dream_model=model,
                run_rules=False,
                records=batch,
            )
            applied = await self._memory.apply_memory_changes(
                changes,
                session_id=session_id,
                expected_turn_id=last_id,
                last_turn_id=batch[-1].turn_id,
            )
            if applied < 0:
                return ConsolidationOutcome("stale", last_turn_id=last_id)
            logger.debug(
                "scheduler.memory_dreaming_session_done",
                session_id=session_id,
                applied=applied,
                max_turn_id=batch[-1].turn_id,
                deferred=sum(c.status == "pending" for c in changes.candidates),
            )
            return ConsolidationOutcome(
                "applied", applied=applied, last_turn_id=batch[-1].turn_id
            )
        except Exception as exc:
            logger.warning(
                "memory_dreaming.batch_failed",
                session_id=session_id,
                last_turn_id=last_id,
                error=str(exc),
            )
            await self._memory.update_session_meta(
                session_id, {"memory_dream_error": str(exc)[:500]}
            )
            return ConsolidationOutcome("failed", last_turn_id=last_id, error=str(exc))
