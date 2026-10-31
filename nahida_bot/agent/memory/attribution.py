"""Typed authorship and evidence, independent of recall scope and audience."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, TYPE_CHECKING

from nahida_bot.agent.memory.models import MemoryItem, MemoryRecord
from nahida_bot.agent.memory.scope import resolve_scope_from_session
from nahida_bot.agent.memory.subjects import MemorySubject

if TYPE_CHECKING:
    from nahida_bot.core.context import SessionContext


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    turn_id: int
    session_id: str
    chat_id: str
    account_key: str
    display_name: str
    message_id: str
    reply_to_message_id: str
    timestamp: str
    role: str
    source: str = ""

    @classmethod
    def from_record(cls, record: MemoryRecord) -> EvidenceRef:
        metadata = record.turn.metadata or {}
        context = metadata.get("message_context") or {}
        if not isinstance(context, dict):
            context = {}
        scope_type, scope_id = resolve_scope_from_session(record.session_id)
        chat_id = scope_id if scope_type == "chat" else ""
        # The typed session carries the configured channel id; display names
        # and platform names cannot establish cross-account identity.
        channel = chat_id.split(":", 1)[0] if chat_id else ""
        sender = str(context.get("sender_id") or "")
        account = f"{channel}:user:{sender}" if channel and sender else ""
        return cls(
            record.turn_id,
            record.session_id,
            chat_id,
            account if record.turn.role == "user" else "",
            str(context.get("sender_display_name") or ""),
            str(context.get("message_id") or metadata.get("message_id") or ""),
            str(context.get("reply_to_message_id") or ""),
            record.turn.created_at.isoformat(),
            record.turn.role,
            record.turn.source,
        )

    @classmethod
    def from_context(cls, ctx: SessionContext) -> EvidenceRef:
        from nahida_bot.core.message_context import context_from_inbound

        inbound = ctx.inbound_message
        context = context_from_inbound(inbound) if inbound is not None else None
        return cls(
            0,
            ctx.session_id,
            ctx.chat_address.chat_key if ctx.chat_address else "",
            ctx.actor_account_key,
            ctx.sender_display_name,
            context.message_id if context else "",
            context.reply_to_message_id if context else "",
            datetime.fromtimestamp(context.timestamp, UTC).isoformat()
            if context
            else "",
            "user",
            ctx.origin,
        )


@dataclass(frozen=True, slots=True)
class MemoryAttribution:
    subject: MemorySubject = MemorySubject()
    evidence: tuple[EvidenceRef, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, **asdict(self)}

    @classmethod
    def parse(cls, metadata: object) -> MemoryAttribution:
        if not isinstance(metadata, dict):
            return cls()
        value = metadata.get("attribution")
        if not isinstance(value, dict) or value.get("version") != 1:
            return cls()
        evidence = []
        for raw in (
            value.get("evidence", [])
            if isinstance(value.get("evidence"), (list, tuple))
            else []
        ):
            if not isinstance(raw, dict):
                continue
            try:
                ref = EvidenceRef(**raw)
            except TypeError:
                continue
            if type(ref.turn_id) is not int or not all(
                isinstance(v, str) for k, v in raw.items() if k != "turn_id"
            ):
                continue
            evidence.append(ref)
        return cls(MemorySubject.parse(value.get("subject")), tuple(evidence))


def attribution_for_candidate(
    metadata: dict[str, Any], records: list[MemoryRecord]
) -> MemoryAttribution:
    """Bind model-selected ids to actual evidence, never model-written sources."""
    ids = metadata.get("evidence_turn_ids")
    if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids):
        raise ValueError("memory candidate requires evidence_turn_ids")
    available = {record.turn_id: record for record in records}
    if any(i not in available for i in ids):
        raise ValueError("memory candidate cites evidence outside its batch")
    refs = tuple(EvidenceRef.from_record(available[i]) for i in dict.fromkeys(ids))
    if not any(
        ref.role == "user" and ref.source not in {"cron_trigger", "proactive_join"}
        for ref in refs
    ):
        raise ValueError("assistant output alone cannot establish a durable fact")
    subject = MemorySubject.parse(metadata.get("subject"))
    if subject.kind == "relationship":
        for member in subject.members:
            attribution_for_candidate(
                {"evidence_turn_ids": ids, "subject": asdict(member)}, records
            )
    if subject.kind == "account" and subject.id not in {
        EvidenceRef.from_record(record).account_key for record in records
    }:
        raise ValueError("memory subject is not an identified batch participant")
    if subject.kind == "person":
        raise ValueError("dreaming cannot infer Person links from conversation")
    if subject.kind == "account" and subject.id not in {
        ref.account_key for ref in refs
    }:
        # A model cannot attach Alice's self-report to Bob merely because Bob
        # was elsewhere in the same batch. Third-party reports must actually
        # identify the reported person; ambiguous names remain reviewable.
        participants = [EvidenceRef.from_record(record) for record in records]
        names = {
            ref.display_name
            for ref in participants
            if ref.account_key == subject.id and len(ref.display_name) > 1
        }
        names = {
            name
            for name in names
            if {ref.account_key for ref in participants if ref.display_name == name}
            == {subject.id}
        }
        identifiers = {subject.id, subject.id.rsplit(":", 1)[-1], *names}
        if not any(
            identifier and identifier in available[i].turn.content
            for i in ids
            for identifier in identifiers
        ):
            raise ValueError(
                "third-party claim does not identify its subject in the cited evidence"
            )
    if subject.kind == "chat" and subject.id not in {ref.chat_id for ref in refs}:
        raise ValueError("memory subject is not the evidence chat")
    return MemoryAttribution(subject, refs)


def item_subject(item: MemoryItem) -> MemorySubject:
    """Unknown legacy subjects stay unknown, even in person/account scopes."""
    metadata = getattr(item, "metadata", None) or {}
    attribution = MemoryAttribution.parse(metadata)
    if attribution.subject.kind != "unknown":
        return attribution.subject
    if metadata.get("relation") == "alias":
        if identity := metadata.get("subject_person_id"):
            return MemorySubject("person", str(identity))
        if identity := metadata.get("subject_account_key"):
            return MemorySubject("account", str(identity))
    return MemorySubject()


def allows_automatic_cross_recall(item: MemoryItem) -> bool:
    """Unattributed personal claims need an explicit, qualified lookup."""
    return item_subject(item).kind != "unknown"


def secret_visible_in_chat(item: MemoryItem, chat_key: str) -> bool:
    """Old Person-scoped secret-like rows need actual originating-chat evidence."""
    if getattr(item, "sensitivity", "public") != "secret_like":
        return True
    if item.scope_type == "chat":
        return item.scope_id == chat_key
    origins = {
        e.chat_id for e in MemoryAttribution.parse(item.metadata).evidence if e.chat_id
    }
    return bool(chat_key) and origins == {chat_key}


def with_present_accounts(
    item: MemoryItem, matches: dict[tuple[str, str], list[str]]
) -> MemoryItem:
    """Explain verified cross-channel correspondence without rewriting the source subject."""
    if not isinstance(item, MemoryItem):
        return item
    subject = item_subject(item)
    members = subject.members if subject.kind == "relationship" else (subject,)
    correspondence = {
        member.label(): matches[(member.kind, member.id)]
        for member in members
        if (member.kind, member.id) in matches
    }
    if not correspondence:
        return item
    return replace(
        item, metadata={**item.metadata, "present_subject_accounts": correspondence}
    )
