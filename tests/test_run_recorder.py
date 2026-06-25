"""Tests for RunRecorder — sequencing, receipt derivation, sanitization (Phase 1)."""

from __future__ import annotations

from typing import Any

from nahida_bot.agent.loop import ToolExecutionResult
from nahida_bot.agent.providers import ToolCall
from nahida_bot.agent.runtime.models import (
    AgentRunContext,
    TerminalState,
)
from nahida_bot.agent.runtime.recorder import RunRecorder
from nahida_bot.agent.runtime.store import AgentRunStore


class _CapturingStore(AgentRunStore):
    def __init__(self) -> None:
        self.created_runs: list[AgentRunContext] = []
        self.events: list[tuple[str, int, str, dict[str, Any]]] = []
        self.receipts: list[Any] = []
        self.finalized: list[tuple[str, TerminalState, str, str, str]] = []

    async def create_run(self, context, *, model="", api_family="", started_at):
        self.created_runs.append(context)

    async def append_event(self, run_id, *, sequence, event_type, payload, created_at):
        self.events.append((run_id, sequence, event_type, payload))

    async def record_receipt(self, receipt):
        self.receipts.append(receipt)

    async def finalize_run(
        self, run_id, *, terminal_state, ended_at, failure_code="", failure_detail=""
    ):
        self.finalized.append(
            (run_id, terminal_state, ended_at, failure_code, failure_detail)
        )

    async def get_run(self, run_id):
        return None

    async def list_events(self, run_id):
        return []

    async def list_receipts(self, run_id):
        return []

    async def save_transcript(self, run_id, messages):
        return None

    async def list_recent_transcripts(self, session_id, *, limit=20):
        return []


class _ExplodingStore(_CapturingStore):
    async def create_run(self, *args, **kwargs):
        raise RuntimeError("boom create")

    async def append_event(self, *args, **kwargs):
        raise RuntimeError("boom append")

    async def record_receipt(self, *args, **kwargs):
        raise RuntimeError("boom receipt")

    async def finalize_run(self, *args, **kwargs):
        raise RuntimeError("boom finalize")


def _ctx(run_id: str = "run_x") -> AgentRunContext:
    return AgentRunContext(
        run_id=run_id, trace_id=run_id, session_id="s", provider_id="p"
    )


def _tool(
    call_id: str = "c1", name: str = "read", arguments: dict | None = None
) -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments or {"path": "a"})


async def test_run_started_and_assistant_output_sequence_and_truncation() -> None:
    store = _CapturingStore()
    rec = RunRecorder(store, _ctx())
    long_text = "x" * 3000

    await rec.run_started(user_message="hi", model="m", api_family="openai")
    await rec.assistant_output(
        step=1, content=long_text, finish_reason="stop", tool_call_count=0
    )

    assert store.created_runs and store.created_runs[0].run_id == "run_x"
    assert [e[2] for e in store.events] == ["user_input", "assistant_output"]
    assert [e[1] for e in store.events] == [1, 2]  # monotonic sequence
    # Assistant content is truncated; no raw 3000-char blob stored.
    assistant_payload = store.events[1][3]
    assert len(assistant_payload["content_summary"]) <= 1000
    assert assistant_payload["content_fingerprint"]


async def test_receipt_status_mapping_ok_error_timed_out_prepare_failed() -> None:
    store = _CapturingStore()
    rec = RunRecorder(store, _ctx())

    # ok
    await rec.begin_tool_call(_tool("c_ok"))
    await rec.end_tool_call(
        _tool("c_ok"),
        ToolExecutionResult.success(output="done"),
        attempt=1,
        phase="completed",
    )
    # timed_out
    await rec.begin_tool_call(_tool("c_to"))
    await rec.end_tool_call(
        _tool("c_to"),
        ToolExecutionResult.error(code="tool_timeout", message="slow", retryable=False),
        attempt=1,
        phase="failed",
    )
    # error (other)
    await rec.begin_tool_call(_tool("c_err"))
    await rec.end_tool_call(
        _tool("c_err"),
        ToolExecutionResult.error(code="boom", message="bad", retryable=False),
        attempt=1,
        phase="failed",
    )
    # prepare_failed (validation; no begin)
    await rec.end_tool_call(
        _tool("c_prep"),
        ToolExecutionResult.error(
            code="tool_not_registered", message="nope", retryable=False
        ),
        attempt=0,
        phase="prepare_failed",
    )

    statuses = {r.call_id: r.status for r in store.receipts}
    assert statuses == {
        "c_ok": "ok",
        "c_to": "timed_out",
        "c_err": "error",
        "c_prep": "error",
    }


async def test_receipt_sanitizes_output_and_arguments() -> None:
    store = _CapturingStore()
    rec = RunRecorder(store, _ctx())
    big_output = "Z" * 5000

    await rec.begin_tool_call(_tool("c1", arguments={"secret": "abc", "path": "a/b"}))
    await rec.end_tool_call(
        _tool("c1", arguments={"secret": "abc", "path": "a/b"}),
        ToolExecutionResult.success(output=big_output),
        attempt=1,
        phase="completed",
    )

    receipt = store.receipts[0]
    # Output is summarized + hashed, not stored in full.
    assert len(receipt.evidence["output_summary"]) <= 500
    assert receipt.evidence["output_hash"]
    # Arguments are fingerprinted, never stored by value.
    assert receipt.input_fingerprint
    assert "abc" not in receipt.evidence  # raw secret/value not in evidence
    tool_call_payload = store.events[0][3]
    assert tool_call_payload["argument_keys"] == ["path", "secret"]
    assert tool_call_payload["argument_fingerprint"]
    assert "abc" not in str(tool_call_payload)


async def test_terminal_finalizes_and_ensure_finalized_is_noop_after() -> None:
    store = _CapturingStore()
    rec = RunRecorder(store, _ctx())

    await rec.terminal(terminal_state="completed", reason="no_tool_calls")
    assert store.finalized[0][1] == TerminalState.COMPLETED
    # terminal event was appended before finalize
    assert store.events[-1][2] == "terminal"

    await rec.ensure_finalized()  # already finalized → no extra finalize
    assert len(store.finalized) == 1


async def test_ensure_finalized_marks_unhandled_when_no_terminal() -> None:
    store = _CapturingStore()
    rec = RunRecorder(store, _ctx())
    await rec.run_started(user_message="hi")
    # Run ends abruptly (e.g. unexpected exception) without terminal().
    await rec.ensure_finalized()
    assert store.finalized[0][1] == TerminalState.FAILED
    assert store.finalized[0][3] == "unhandled"


async def test_store_failures_never_propagate() -> None:
    """A broken store must not affect the run — recorder swallows and logs."""
    store = _ExplodingStore()
    rec = RunRecorder(store, _ctx())

    # None of these should raise despite the exploding store.
    await rec.run_started(user_message="hi")
    await rec.assistant_output(step=1, content="hello")
    await rec.begin_tool_call(_tool())
    await rec.end_tool_call(
        _tool(), ToolExecutionResult.success(output="ok"), attempt=1, phase="completed"
    )
    await rec.terminal(terminal_state="completed", reason="ok")
