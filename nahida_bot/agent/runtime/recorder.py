"""RunRecorder — drives the canonical ledger from inside the agent loop.

The recorder owns three things the loop should not care about:

- **Sequencing**: a single-threaded monotonic ``sequence`` counter per run,
  assigned in call order so ``agent_run_events`` reconstructs faithfully.
- **Sanitization**: only summaries + hashes are persisted — never reasoning
  text, full tool output, or raw argument values.
- **Best-effort writes**: every store call is wrapped so a ledger failure
  becomes a warning and never affects the run (Phase 1 invariant: zero
  behaviour change).

To avoid an import cycle (the loop imports the recorder), the rich provider
types are referenced only under ``TYPE_CHECKING``; at runtime the recorder
reads their attributes directly.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from nahida_bot.agent.runtime.models import (
    RECEIPT_VERIFICATION_UNVERIFIED,
    AgentRunContext,
    ExecutionReceipt,
    TerminalState,
    utc_now_iso,
)
from nahida_bot.agent.runtime.store import AgentRunStore

if TYPE_CHECKING:
    from nahida_bot.agent.loop import ToolExecutionResult
    from nahida_bot.agent.providers import ToolCall

logger = structlog.get_logger(__name__)

_ASSISTANT_SUMMARY_CHARS = 1000
_TOOL_OUTPUT_SUMMARY_CHARS = 500
_ERROR_MESSAGE_CHARS = 300

# Map the loop's string terminal-state vocabulary to the enum.
_TERMINAL_STATE_BY_NAME = {
    "running": TerminalState.RUNNING,
    "completed": TerminalState.COMPLETED,
    "unverified": TerminalState.UNVERIFIED,
    "incomplete": TerminalState.INCOMPLETE,
    "failed": TerminalState.FAILED,
    "cancelled": TerminalState.CANCELLED,
}


def _to_terminal_state(value: str | TerminalState) -> TerminalState:
    if isinstance(value, TerminalState):
        return value
    return _TERMINAL_STATE_BY_NAME.get(value, TerminalState.FAILED)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(_stringify(value).encode("utf-8")).hexdigest()[:16]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _receipt_status(result: ToolExecutionResult, phase: str) -> str:
    if phase == "prepare_failed":
        return "error"
    if result.is_error:
        if result.error_code == "tool_timeout":
            return "timed_out"
        return "error"
    return "ok"


class RunRecorder:
    """Append-only canonical recorder for one agent run."""

    def __init__(self, store: AgentRunStore, context: AgentRunContext) -> None:
        self._store = store
        self._context = context
        self._sequence = 0
        self._finalized = False
        self._tool_started_at: dict[str, str] = {}

    @property
    def run_id(self) -> str:
        return self._context.run_id

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    async def _append(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._finalized:
            return
        sequence = self._next_sequence()
        try:
            await self._store.append_event(
                self._context.run_id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
                created_at=utc_now_iso(),
            )
        except Exception:
            logger.warning(
                "agent_runtime.ledger_append_failed",
                run_id=self._context.run_id,
                sequence=sequence,
                event_type=event_type,
                exc_info=True,
            )

    async def run_started(
        self,
        *,
        user_message: str,
        model: str = "",
        api_family: str = "",
    ) -> None:
        try:
            await self._store.create_run(
                self._context,
                model=model,
                api_family=api_family,
                started_at=utc_now_iso(),
            )
        except Exception:
            logger.warning(
                "agent_runtime.ledger_create_run_failed",
                run_id=self._context.run_id,
                exc_info=True,
            )
        await self._append(
            "user_input",
            {
                "content_summary": _truncate(user_message, _ASSISTANT_SUMMARY_CHARS),
                "content_fingerprint": _digest(user_message),
            },
        )

    async def assistant_output(
        self,
        *,
        step: int,
        content: str,
        finish_reason: str = "",
        tool_call_count: int = 0,
        protocol_anomaly: str = "",
    ) -> None:
        await self._append(
            "assistant_output",
            {
                "step": step,
                "content_summary": _truncate(content, _ASSISTANT_SUMMARY_CHARS),
                "content_fingerprint": _digest(content),
                "finish_reason": finish_reason or "",
                "tool_call_count": tool_call_count,
            },
        )
        if protocol_anomaly:
            await self._append(
                "provider_anomaly",
                {
                    "step": step,
                    "anomaly": protocol_anomaly,
                    "finish_reason": finish_reason or "",
                },
            )

    async def begin_tool_call(self, tool_call: ToolCall) -> None:
        self._tool_started_at[tool_call.call_id] = utc_now_iso()
        await self._append(
            "tool_call",
            {
                "call_id": tool_call.call_id,
                "tool_name": tool_call.name,
                "argument_keys": sorted(tool_call.arguments.keys()),
                "argument_fingerprint": _digest(tool_call.arguments),
            },
        )

    async def end_tool_call(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
        *,
        attempt: int,
        phase: str,
    ) -> None:
        started_at = self._tool_started_at.pop(tool_call.call_id, None)
        finished_at = utc_now_iso()
        status = _receipt_status(result, phase)
        output_summary = _truncate(
            _stringify(result.output), _TOOL_OUTPUT_SUMMARY_CHARS
        )
        output_hash = _digest(result.output)
        evidence: dict[str, Any] = {
            "phase": phase,
            "attempt": attempt,
            "output_summary": output_summary,
            "output_hash": output_hash,
            "retryable": bool(result.retryable),
        }
        if result.is_error:
            evidence["error_code"] = result.error_code or ""
            evidence["error_message"] = _truncate(
                result.error_message or "", _ERROR_MESSAGE_CHARS
            )

        await self._append(
            "tool_result",
            {
                "call_id": tool_call.call_id,
                "tool_name": tool_call.name,
                "status": status,
                "output_summary": output_summary,
                "output_hash": output_hash,
            },
        )

        receipt = ExecutionReceipt(
            receipt_id=uuid4().hex,
            run_id=self._context.run_id,
            call_id=tool_call.call_id,
            tool_name=tool_call.name,
            status=status,  # type: ignore[arg-type]
            verification_status=RECEIPT_VERIFICATION_UNVERIFIED,
            input_fingerprint=_digest(tool_call.arguments),
            evidence=evidence,
            started_at=started_at or finished_at,
            finished_at=finished_at,
        )
        try:
            await self._store.record_receipt(receipt)
        except Exception:
            logger.warning(
                "agent_runtime.ledger_receipt_failed",
                run_id=self._context.run_id,
                call_id=tool_call.call_id,
                exc_info=True,
            )

    async def terminal(
        self,
        *,
        terminal_state: str | TerminalState,
        reason: str,
        finish_reason: str = "",
        failure_code: str = "",
        failure_detail: str = "",
    ) -> None:
        state = _to_terminal_state(terminal_state)
        await self._append(
            "terminal",
            {
                "terminal_state": state.value,
                "reason": reason,
                "finish_reason": finish_reason or "",
            },
        )
        await self._finalize(
            state, failure_code=failure_code, failure_detail=failure_detail
        )

    async def ensure_finalized(self) -> None:
        """Safety net: finalize if the run ended without an explicit terminal."""
        if self._finalized:
            return
        await self._finalize(
            TerminalState.FAILED,
            failure_code="unhandled",
            failure_detail="run ended without an explicit terminal event",
        )

    async def _finalize(
        self,
        terminal_state: TerminalState,
        *,
        failure_code: str = "",
        failure_detail: str = "",
    ) -> None:
        if self._finalized:
            return
        self._finalized = True
        try:
            await self._store.finalize_run(
                self._context.run_id,
                terminal_state=terminal_state,
                ended_at=utc_now_iso(),
                failure_code=failure_code,
                failure_detail=failure_detail,
            )
        except Exception:
            logger.warning(
                "agent_runtime.ledger_finalize_failed",
                run_id=self._context.run_id,
                terminal_state=terminal_state.value,
                exc_info=True,
            )
