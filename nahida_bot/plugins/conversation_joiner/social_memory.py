"""Bounded durable-memory evidence for appraisal; never writes emotions back."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from nahida_bot.agent.memory.attribution import MemoryAttribution, item_subject
from nahida_bot.agent.memory.rendering import item_from_ref


class MemoryBasis(BaseModel):
    memory_id: str
    category: str
    subject: str
    kind: str = "fact"
    confidence: float = Field(default=1, ge=0, le=1)
    reported_by: list[str] = Field(default_factory=list, max_length=4)
    present_subject_accounts: dict[str, list[str]] = Field(default_factory=dict)
    content: str = Field(max_length=240)
    sources: list[str] = Field(default_factory=list, max_length=4)
    evidence_turn_ids: list[int] = Field(default_factory=list, max_length=8)


def appraisal_memories(
    sections: dict[str, list[Any]], *, max_items: int, max_chars: int
) -> list[MemoryBasis]:
    """Round-robin categories so one prolific subject cannot fill the whole prompt."""
    queues = [
        list(sections.get(name, []))
        for name in ("person", "relationship", "chat", "bot", "environment")
    ]
    result, seen, size = [], set(), 0
    while any(queues) and len(result) < max_items:
        for queue in queues:
            if not queue or len(result) >= max_items:
                continue
            item = item_from_ref(queue.pop(0))
            subject = item_subject(item)
            if item.item_id in seen or subject.kind == "unknown":
                continue
            attribution = MemoryAttribution.parse(item.metadata)
            basis = MemoryBasis(
                memory_id=item.item_id,
                category=subject.category,
                subject=subject.label(),
                kind=item.kind,
                confidence=item.confidence,
                present_subject_accounts=item.metadata.get(
                    "present_subject_accounts", {}
                ),
                reported_by=sorted(
                    {e.account_key for e in attribution.evidence if e.account_key}
                )[:4],
                content=item.content[:240],
                sources=sorted({e.chat_id for e in attribution.evidence if e.chat_id})[
                    :4
                ]
                or [f"{item.scope_type}:{item.scope_id}"],
                evidence_turn_ids=list(
                    dict.fromkeys(e.turn_id for e in attribution.evidence if e.turn_id)
                )[:8],
            )
            cost = len(json.dumps(basis.model_dump(), ensure_ascii=False))
            if size + cost > max_chars:
                continue
            size += cost
            seen.add(item.item_id)
            result.append(basis)
    return result
