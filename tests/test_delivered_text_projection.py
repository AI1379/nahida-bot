"""Tests for delivered_text memory projection (design §11) and sentinel stripping."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.core.session_runner import (
    SessionRunner,
    _extract_delivered_text,
    _extract_delivered_utterances,
    _visible_text,
)


def _tool_result_message(
    tool_name: str,
    output_obj: Any,
    *,
    call_id: str = "c1",
    is_error: bool = False,
) -> ContextMessage:
    """Build a tool-result ContextMessage like AgentLoop._build_tool_message."""
    envelope = {
        "status": "error" if is_error else "ok",
        "output": output_obj
        if isinstance(output_obj, str)
        else json.dumps(output_obj, ensure_ascii=False),
        "error": None,
        "logs": [],
    }
    return ContextMessage(
        role="tool",
        source=f"tool_result:{tool_name}",
        content=json.dumps(envelope, ensure_ascii=False),
        metadata={"tool_call_id": call_id, "tool_name": tool_name},
    )


# ── _visible_text: sentinel + envelope stripping ────────────────────────


def test_visible_text_strips_pure_sentinel() -> None:
    assert _visible_text("NO_REPLY") == ""
    assert _visible_text("HEARTBEAT_OK") == ""
    assert _visible_text("  NO_REPLY  \n") == ""


def test_visible_text_keeps_text_with_trailing_sentinel() -> None:
    assert _visible_text("hello\nNO_REPLY") == "hello"
    assert _visible_text("all good\nHEARTBEAT_OK") == "all good"


def test_visible_text_passes_through_plain_text() -> None:
    assert _visible_text("hello") == "hello"


# ── _extract_delivered_text: nested JSON parsing ────────────────────────


def test_extract_delivered_text_from_speak_payload() -> None:
    envelope = {
        "status": "ok",
        "output": json.dumps({"delivered_text": "你好呀"}),
        "error": None,
        "logs": [],
    }
    assert _extract_delivered_text(json.dumps(envelope)) == "你好呀"


def test_extract_delivered_text_handles_edge_cases() -> None:
    # empty / missing delivered_text
    out = json.dumps({"status": "ok"})
    assert _extract_delivered_text(json.dumps({"output": out})) == ""
    assert (
        _extract_delivered_text(
            json.dumps({"output": json.dumps({"delivered_text": "  "})})
        )
        == ""
    )
    # output not JSON
    assert _extract_delivered_text(json.dumps({"output": "plain string"})) == ""
    # envelope not JSON
    assert _extract_delivered_text("not json") == ""
    # output missing
    assert _extract_delivered_text(json.dumps({"status": "ok"})) == ""
    # output is a non-string (e.g. dict) — some tools return structured output
    assert _extract_delivered_text(json.dumps({"output": json.dumps([1, 2, 3])})) == ""


# ── _extract_delivered_utterances: from tool messages ───────────────────


def test_extract_delivered_utterances_picks_up_speak() -> None:
    msg = _tool_result_message("speak", {"status": "ok", "delivered_text": "你好"})
    utterances = _extract_delivered_utterances([msg])
    assert len(utterances) == 1
    text, meta = utterances[0]
    assert text == "你好"
    assert meta["tool"] == "speak"
    assert meta["spoken"] is True
    assert meta["tool_call_id"] == "c1"


def test_extract_delivered_utterances_skips_non_tool_and_empty() -> None:
    assistant_msg = ContextMessage(role="assistant", content="hi", source="agent")
    speak_ok = _tool_result_message("speak", {"delivered_text": "hi"})
    speak_empty = _tool_result_message("speak", {"status": "ok"})  # no delivered_text
    web_fetch = _tool_result_message(
        "web_fetch", {"content": "page"}
    )  # different tool, no field
    utterances = _extract_delivered_utterances(
        [assistant_msg, speak_ok, speak_empty, web_fetch]
    )
    assert len(utterances) == 1
    assert utterances[0][0] == "hi"


# ── _assistant_visible_turns: projection integration ────────────────────


def _runner() -> SessionRunner:
    return SessionRunner()


def test_voice_only_projects_delivered_text_without_no_reply() -> None:
    # speak delivered "你好", model final = NO_REPLY → only the spoken turn is remembered
    result = AgentRunResult(
        final_response="NO_REPLY",
        tool_messages=[_tool_result_message("speak", {"delivered_text": "你好呀"})],
    )
    turns = _runner()._assistant_visible_turns(result, include_message_context=False)
    assert len(turns) == 1
    assert turns[0].role == "assistant"
    assert turns[0].source == "tool_utterance"
    assert turns[0].content == "你好呀"
    assert turns[0].metadata is not None
    assert turns[0].metadata["tool"] == "speak"
    assert turns[0].metadata["spoken"] is True


def test_speak_plus_text_projects_both() -> None:
    result = AgentRunResult(
        final_response="补充说明",
        tool_messages=[_tool_result_message("speak", {"delivered_text": "你好"})],
    )
    turns = _runner()._assistant_visible_turns(result, include_message_context=False)
    assert [t.source for t in turns] == ["tool_utterance", "agent_response"]
    assert turns[0].content == "你好"
    assert turns[1].content == "补充说明"


def test_delivered_text_deduped_against_assistant_text() -> None:
    # spoken text == final text → the agent_response wins, delivered dropped
    result = AgentRunResult(
        final_response="你好",
        tool_messages=[_tool_result_message("speak", {"delivered_text": "你好"})],
    )
    turns = _runner()._assistant_visible_turns(result, include_message_context=False)
    assert len(turns) == 1
    assert turns[0].source == "agent_response"
    assert turns[0].content == "你好"


def test_no_delivered_text_just_assistant_reply() -> None:
    result = AgentRunResult(final_response="hello", tool_messages=[])
    turns = _runner()._assistant_visible_turns(result, include_message_context=False)
    assert len(turns) == 1
    assert turns[0].source == "agent_response"
    assert turns[0].content == "hello"


def test_no_assistant_output_and_no_delivered_returns_empty() -> None:
    result = AgentRunResult(final_response="NO_REPLY", tool_messages=[])
    turns = _runner()._assistant_visible_turns(result, include_message_context=False)
    assert turns == []


def test_degraded_speak_without_delivered_text_is_not_projected() -> None:
    # degrade path returns no delivered_text → must not pollute memory
    result = AgentRunResult(
        final_response="NO_REPLY",
        tool_messages=[
            _tool_result_message("speak", {"status": "degraded", "fallback": "text"})
        ],
    )
    turns = _runner()._assistant_visible_turns(result, include_message_context=False)
    assert turns == []


# ── _persist_turns: end-to-end memory recording ─────────────────────────


class _RecordingMemory:
    def __init__(self) -> None:
        self.turns: list[Any] = []

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        pass

    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        return {}

    async def get_recent(self, *a: Any, **kw: Any) -> list[Any]:
        return []

    async def append_turn(self, session_id: str, turn: Any, **kw: Any) -> int:
        self.turns.append(turn)
        return len(self.turns)


@pytest.mark.asyncio
async def test_persist_turns_records_delivered_text_as_assistant_turn() -> None:
    memory = _RecordingMemory()
    runner = SessionRunner(
        agent_loop=cast(Any, object()), memory_store=cast(Any, memory)
    )
    result = AgentRunResult(
        final_response="NO_REPLY",
        tool_messages=[_tool_result_message("speak", {"delivered_text": "你好呀"})],
    )

    await runner._persist_turns(
        "session_1",
        "用户消息",
        result,
        attachments=[],
        source_tag="user_input",
    )

    # user turn + one assistant (spoken) turn; NO_REPLY must NOT appear
    assert any(t.role == "user" for t in memory.turns)
    assistant_turns = [t for t in memory.turns if t.role == "assistant"]
    assert len(assistant_turns) == 1
    assert assistant_turns[0].content == "你好呀"
    assert assistant_turns[0].source == "tool_utterance"
    # the literal sentinel must never be recorded
    assert all(t.content != "NO_REPLY" for t in memory.turns)
