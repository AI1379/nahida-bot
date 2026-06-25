"""Cross-turn tool transcript projection (agent-loop repair Phase 5).

Reads the ordered raw transcript persisted per run (``agent_runs.transcript_json``)
and rebuilds a ``ContextMessage`` history in which assistant tool-call requests
and their tool results survive as *paired* messages, instead of being collapsed
to plain assistant text (the #24 bug). At long context this is what stops the
model from seeing only its own text narration of past actions — the structural
fix for the "longer context → more promise-without-tool" finding.

The Phase 1 canonical ledger (``agent_run_events``) is deliberately sanitized to
summaries + hashes and is **not** the replay source; this module reads the
separate raw ``transcript_json`` column written after each run finalizes.

Invariant borrowed from Codex / OpenCode / OpenClaw (see
``docs/design/agent-loop-repair-plan.md`` §10): a provider must never be shown a
dangling ``tool_use``. So during projection:

- a ``tool_call`` with no matching result (e.g. an interrupted run) gets a
  synthetic *interrupted* result inserted right after its assistant turn;
- an orphan ``tool_result`` (no preceding call in the same run) is dropped;
- the call/result block is emitted in call order so provider pairing rules hold.

Provider serialization itself is unchanged — the reconstructed
``ContextMessage`` objects already carry ``metadata["tool_calls"]`` and
``role="tool"`` + ``metadata["tool_call_id"]``, which all three provider
adapters translate correctly.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.agent.runtime.store import AgentRunStore

if TYPE_CHECKING:
    from nahida_bot.agent.providers.base import ModelCapabilities

# Per-tool-result output cap applied at BOTH write time (before transcript_json
# is stored) and read time (defence in depth), so one huge tool output cannot
# bloat the column or the replayed context window.
TRANSCRIPT_TOOL_OUTPUT_MAX_CHARS = 4000
_TRUNCATED_MARKER = "…[truncated]"


# ── serialization helpers ──────────────────────────────────────────────


def message_to_dict(message: ContextMessage) -> dict[str, Any]:
    """Serialize a :class:`ContextMessage` to a JSON-safe dict.

    Only ``text`` parts are kept; image / base64 parts are dropped (image
    history is the media-policy path's job, not replay's). Tool arguments and
    metadata are preserved verbatim — they are needed for faithful replay and
    are bounded by the per-result output cap applied elsewhere.
    """
    parts = [
        {"type": "text", "text": part.text}
        for part in message.parts
        if part.type == "text" and part.text
    ]
    return {
        "role": message.role,
        "content": message.content,
        "source": message.source,
        "metadata": message.metadata,
        "reasoning": message.reasoning,
        "reasoning_signature": message.reasoning_signature,
        "has_redacted_thinking": message.has_redacted_thinking,
        "parts": parts,
    }


def message_from_dict(data: dict[str, Any]) -> ContextMessage:
    """Rebuild a :class:`ContextMessage` from its serialized dict."""
    raw_parts = data.get("parts") or []
    parts = [
        ContextPart(type="text", text=str(part.get("text", "")))
        for part in raw_parts
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    metadata = data.get("metadata")
    return ContextMessage(
        role=data.get("role", "assistant"),  # type: ignore[arg-type]
        content=data.get("content", ""),
        source=data.get("source", ""),
        metadata=metadata if isinstance(metadata, dict) else None,
        reasoning=data.get("reasoning"),
        reasoning_signature=data.get("reasoning_signature"),
        has_redacted_thinking=bool(data.get("has_redacted_thinking", False)),
        parts=parts,
    )


def transcript_to_payload(
    messages: list[ContextMessage],
    *,
    tool_output_cap: int = TRANSCRIPT_TOOL_OUTPUT_MAX_CHARS,
) -> list[dict[str, Any]]:
    """Convert an ordered transcript to a JSON-safe payload for ``save_transcript``.

    Caps each ``tool`` message's content so ``transcript_json`` stays bounded.
    """
    payload: list[dict[str, Any]] = []
    for message in messages:
        data = message_to_dict(message)
        if message.role == "tool" and len(message.content) > tool_output_cap:
            data["content"] = _cap_tool_content(message.content, tool_output_cap)
        payload.append(data)
    return payload


def _cap_tool_content(content: str, cap: int) -> str:
    if len(content) <= cap:
        return content
    return content[:cap] + _TRUNCATED_MARKER


def _synthetic_result(call_id: str, tool_name: str) -> ContextMessage:
    """A interrupted-result message for a tool_call whose result was lost."""
    content = json.dumps(
        {
            "status": "interrupted",
            "output": "[Tool execution was interrupted]",
            "error": None,
            "logs": [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return ContextMessage(
        role="tool",
        source=f"tool_result:synthetic:{tool_name}",
        content=content,
        metadata={
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "synthetic": True,
        },
    )


# ── pairing repair ─────────────────────────────────────────────────────


def _issued_calls(messages: list[ContextMessage]) -> dict[str, str]:
    """Map every ``call_id`` issued in this run to its tool name."""
    issued: dict[str, str] = {}
    for message in messages:
        if message.role != "assistant" or not isinstance(message.metadata, dict):
            continue
        raw_calls = message.metadata.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            if isinstance(call_id, str):
                issued[call_id] = str(call.get("name") or "tool")
    return issued


def repair_pairs(
    messages: list[ContextMessage],
    *,
    tool_output_cap: int = TRANSCRIPT_TOOL_OUTPUT_MAX_CHARS,
) -> list[ContextMessage]:
    """Return a copy where every tool_call has a paired tool result.

    - tool_call without a following result → synthetic interrupted result.
    - orphan tool_result (no matching call anywhere in the run) → dropped.
    - tool result content is capped.
    - results are emitted in the assistant's call order, immediately after it.
    """
    issued = _issued_calls(messages)
    out: list[ContextMessage] = []
    i = 0
    total = len(messages)
    while i < total:
        message = messages[i]
        if message.role == "assistant" and isinstance(message.metadata, dict):
            raw_calls = message.metadata.get("tool_calls")
            calls = [
                call
                for call in (raw_calls if isinstance(raw_calls, list) else [])
                if isinstance(call, dict) and isinstance(call.get("id"), str)
            ]
            call_ids = [str(call["id"]) for call in calls]
            out.append(message)
            i += 1
            # Consume the contiguous block of tool results that follow.
            results: dict[str, ContextMessage] = {}
            while i < total and messages[i].role == "tool":
                tool_msg = messages[i]
                cid = (
                    tool_msg.metadata.get("tool_call_id")
                    if isinstance(tool_msg.metadata, dict)
                    else None
                )
                if isinstance(cid, str) and cid in issued and cid not in results:
                    results[cid] = (
                        replace(
                            tool_msg,
                            content=_cap_tool_content(
                                tool_msg.content, tool_output_cap
                            ),
                        )
                        if len(tool_msg.content) > tool_output_cap
                        else tool_msg
                    )
                # duplicates and orphans are dropped
                i += 1
            # Emit in call order; synthesize any that are still missing.
            for cid in call_ids:
                if cid in results:
                    out.append(results[cid])
                else:
                    out.append(_synthetic_result(cid, issued.get(cid, "tool")))
        elif message.role == "tool":
            # A tool message not absorbed by the assistant block above: keep it
            # only if its call was actually issued somewhere in this run.
            cid = (
                message.metadata.get("tool_call_id")
                if isinstance(message.metadata, dict)
                else None
            )
            if isinstance(cid, str) and cid in issued:
                out.append(
                    replace(
                        message,
                        content=_cap_tool_content(message.content, tool_output_cap),
                    )
                    if len(message.content) > tool_output_cap
                    else message
                )
            i += 1
        else:
            out.append(message)
            i += 1
    return out


# ── projector ──────────────────────────────────────────────────────────


def _tag_source(message: ContextMessage, run_id: str) -> ContextMessage:
    tag = f"transcript_replay:{run_id}"
    if message.source == tag:
        return message
    return replace(message, source=tag)


def _result_status(content: str) -> str:
    try:
        decoded = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return "ok"
    if isinstance(decoded, dict):
        status = decoded.get("status")
        if isinstance(status, str):
            return status
    return "ok"


def _summarize_run(messages: list[ContextMessage], run_id: str) -> ContextMessage:
    """Collapse a run's transcript to a single text assistant summary.

    Used when the active model lacks ``tool_calling``: providers that cannot
    consume tool_use/tool_result blocks get a factual execution summary instead.
    """
    prose = ""
    for message in reversed(messages):
        if message.role == "assistant" and message.content:
            prose = message.content
            break
    receipt_lines: list[str] = []
    for message in messages:
        if message.role != "tool" or not isinstance(message.metadata, dict):
            continue
        call_id = message.metadata.get("tool_call_id") or "?"
        name = message.metadata.get("tool_name") or "tool"
        receipt_lines.append(
            f"- call_id: {call_id} · tool: {name} · status: {_result_status(message.content)}"
        )
    body = prose.rstrip()
    header = "Verified execution receipts this turn:"
    if receipt_lines:
        block = header + "\n" + "\n".join(receipt_lines)
        body = f"{body}\n\n{block}" if body else block
    if not body:
        body = "(turn produced no user-facing output)"
    return ContextMessage(
        role="assistant",
        content=body,
        source=f"transcript_replay:{run_id}",
    )


class TranscriptProjector:
    """Project persisted run transcripts back into provider-ready history."""

    def __init__(
        self,
        store: AgentRunStore,
        *,
        tool_output_cap: int = TRANSCRIPT_TOOL_OUTPUT_MAX_CHARS,
    ) -> None:
        self._store = store
        self._tool_output_cap = tool_output_cap

    async def save_transcript(
        self,
        run_id: str,
        messages: list[ContextMessage],
    ) -> None:
        """Persist a run's ordered transcript via the underlying store.

        Thin wrapper so callers (SessionRunner) depend on the projector alone
        and never reach into the store directly. Handles payload conversion +
        per-result output capping.
        """
        payload = transcript_to_payload(messages, tool_output_cap=self._tool_output_cap)
        await self._store.save_transcript(run_id, payload)

    async def project(
        self,
        session_id: str,
        *,
        capabilities: "ModelCapabilities | None",
        limit: int = 20,
    ) -> list[ContextMessage]:
        """Return replayed history for ``session_id``, oldest-first.

        Empty list ⇒ no transcripts available; the caller should fall back to
        the legacy plain-text history path.
        """
        runs = await self._store.list_recent_transcripts(session_id, limit=limit)
        if not runs:
            return []
        tool_capable = bool(capabilities is not None and capabilities.tool_calling)
        out: list[ContextMessage] = []
        for run in runs:
            run_id = str(run.get("run_id") or "")
            messages = self._parse_run(run)
            if not messages:
                continue
            repaired = repair_pairs(messages, tool_output_cap=self._tool_output_cap)
            if tool_capable:
                out.extend(_tag_source(message, run_id) for message in repaired)
            else:
                out.append(_summarize_run(repaired, run_id))
        return out

    @staticmethod
    def _parse_run(run: dict[str, Any]) -> list[ContextMessage]:
        raw = run.get("transcript_json")
        if not isinstance(raw, str) or not raw:
            return []
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(items, list):
            return []
        return [
            message_from_dict(item)
            for item in items
            if isinstance(item, dict) and item.get("role")
        ]


__all__ = [
    "TRANSCRIPT_TOOL_OUTPUT_MAX_CHARS",
    "TranscriptProjector",
    "message_from_dict",
    "message_to_dict",
    "repair_pairs",
    "transcript_to_payload",
]
