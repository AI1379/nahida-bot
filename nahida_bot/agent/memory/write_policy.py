"""One pure policy for attribution, placement and sensitivity of memory writes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from nahida_bot.agent.memory.attribution import (
    MemoryAttribution,
    MemorySubject,
    EvidenceRef,
)
from nahida_bot.agent.memory.models import (
    MemoryItem,
    Sensitivity,
    SensitivitySource,
    normalize_sensitivity,
)
from nahida_bot.agent.memory.portability import normalize_portable
from nahida_bot.agent.memory.scope import (
    MEMORY_KINDS,
    SCOPE_TYPE_CHAT,
    SCOPE_TYPE_GLOBAL,
)

if TYPE_CHECKING:
    from nahida_bot.core.context import SessionContext
    from nahida_bot.identity.policy import MemoryWriteRequest


def resolve_write_sensitivity(
    metadata: dict[str, Any],
) -> tuple[Sensitivity, SensitivitySource]:
    """Pop and validate ``sensitivity`` from a consumer write's metadata.

    Only an explicit RESTRICTION (``private``/``secret_like``) earns
    ``sensitivity_source='explicit'`` (explicit > dream). The soft ``public``
    baseline — whether omitted, explicitly passed, or a fallback for an
    unrecognized value — gets ``'default'`` provenance, matching the
    consolidation default so consumer writes and dreaming agree. The shared
    normalizer handles casing/typos so they can't defeat the SQL filter; an
    unrecognized value is a no-op (public/default) rather than a rejected write.
    """
    raw = metadata.pop("sensitivity", None)
    canonical = normalize_sensitivity(raw)
    if canonical in {"private", "secret_like"}:
        return canonical, "explicit"
    return "public", "default"


def prepare_memory_item(
    key: str,
    content: str,
    *,
    ctx: "SessionContext | None" = None,
    session_id: str = "",
    metadata: dict[str, Any] | None = None,
    write_request: MemoryWriteRequest | None = None,
) -> MemoryItem:
    """The shared write policy for tools, operators and consolidation."""
    from uuid import uuid4
    from nahida_bot.agent.memory.markdown import validate_memory_content
    from nahida_bot.identity.policy import (
        memory_write_request_from_context,
    )

    if error := validate_memory_content(content):
        raise ValueError(error)
    values = dict(metadata or {})
    kind = str(values.pop("kind", "fact")).strip().casefold()
    if kind not in MEMORY_KINDS:
        kind = "fact"
    sensitivity, sensitivity_source = resolve_write_sensitivity(values)
    if "portable" in values:
        values["portable"] = normalize_portable(values["portable"], default=True)
    if values.pop("sensitivity_inferred", False) and sensitivity_source != "default":
        sensitivity_source = "dream"
    request = write_request or memory_write_request_from_context(ctx, session_id)
    attribution = _write_attribution(values, ctx, request)
    values["attribution"] = attribution.to_dict()
    scope_type, scope_id = _resolve_scope(
        request, attribution, kind, sensitivity, values
    )
    values.pop("scope_type", None)
    values.pop("scope_id", None)
    values["audience"] = "global" if scope_type == SCOPE_TYPE_GLOBAL else "current"
    return MemoryItem(
        item_id=f"mem_{uuid4().hex}",
        title=key,
        content=content.strip(),
        scope_type=str(scope_type),
        scope_id=str(scope_id),
        kind=kind,
        source=str(values.pop("source", "plugin")),
        confidence=float(values.pop("confidence", 1.0)),
        importance=float(values.pop("importance", 0.5)),
        sensitivity=sensitivity,
        sensitivity_source=sensitivity_source,
        evidence=values.pop("evidence", None) or {},
        metadata=values,
    )


def _write_attribution(
    values: dict[str, Any], ctx: SessionContext | None, request: MemoryWriteRequest
) -> MemoryAttribution:
    attribution = MemoryAttribution.parse(values)
    selector = values.pop("subject", "unknown")
    if "attribution" not in values:
        subject = MemorySubject()
        if selector == "current_sender" and request.sender_account_key:
            subject = MemorySubject("account", request.sender_account_key)
        elif selector == "chat" and request.chat_scope_id:
            subject = MemorySubject("chat", request.chat_scope_id)
        attribution = MemoryAttribution(
            subject, (EvidenceRef.from_context(ctx),) if ctx else ()
        )
    return attribution


def _resolve_scope(
    request: MemoryWriteRequest,
    attribution: MemoryAttribution,
    kind: str,
    sensitivity: str,
    values: dict[str, Any],
) -> tuple[str, str]:
    from nahida_bot.identity.policy import (
        MemoryWriteRequest,
        resolve_memory_write_scope,
    )

    audience = str(values.get("audience", "current")).strip().casefold()
    portable = normalize_portable(values.get("portable"), default=True)
    # Originating speaker and factual subject are separate. Unknown or
    # third-party claims must never inherit the current speaker's Person.
    subject = attribution.subject
    own_subject = subject.kind == "account" and subject.id == request.sender_account_key
    subject_request = MemoryWriteRequest(
        chat_scope_id=request.chat_scope_id,
        person_id=request.person_id if own_subject else None,
        sender_account_key=subject.id if subject.kind == "account" else "",
    )
    scope_type = values.get("scope_type")
    scope_id = values.get("scope_id")
    if not portable:
        if not request.chat_scope_id:
            raise ValueError("non-portable memory requires a typed current chat")
        scope_type, scope_id = SCOPE_TYPE_CHAT, request.chat_scope_id
    elif (
        scope_type is None
        or scope_id is None
        or (scope_type == SCOPE_TYPE_GLOBAL and sensitivity != "public")
    ):
        scope_type, scope_id = resolve_memory_write_scope(
            subject_request,
            kind,
            global_scope=audience == "global" and sensitivity == "public",
        )
    if (
        attribution.evidence
        and ":group:" in request.chat_scope_id
        and "scope_type" not in values
        and audience != "global"
    ):
        scope_type, scope_id = SCOPE_TYPE_CHAT, request.chat_scope_id
    if scope_type == SCOPE_TYPE_GLOBAL and sensitivity != "public":
        raise ValueError("restricted memory requires a typed current scope")
    return str(scope_type), str(scope_id)
