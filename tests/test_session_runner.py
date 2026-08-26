"""Tests for SessionRunner active-run tracking and graceful stop."""

from __future__ import annotations

import asyncio

import pytest

from nahida_bot.core.session_runner import ActiveRunTracker


@pytest.mark.asyncio
async def test_request_stop_sets_event_without_cancelling_task() -> None:
    """Stopping a run signals stop_event and leaves the task alive to finish.

    Regression guard for the /stop context-loss bug: ``request_stop``
    previously called ``task.cancel()``, which injected ``CancelledError``
    mid-run so SessionRunner skipped ``_persist_turns`` and dropped the user
    message plus any partial reply from history. The graceful stop path only
    works if the task survives long enough to reach the loop's ``stop_event``
    check and emit its ``done`` event, which is what persists the turn.
    """
    tracker = ActiveRunTracker()
    stop_event = asyncio.Event()

    async def worker() -> str:
        # Emulate the agent loop blocking on its next step boundary.
        await stop_event.wait()
        return "completed-gracefully"

    task = asyncio.create_task(worker())
    tracker.start("session-1", task, stop_event)

    assert tracker.request_stop("session-1") is True
    assert stop_event.is_set()

    # Before the fix this awaited task raised CancelledError.
    result = await task
    assert result == "completed-gracefully"
    assert not task.cancelled()


@pytest.mark.asyncio
async def test_request_stop_unknown_session_returns_false() -> None:
    tracker = ActiveRunTracker()
    assert tracker.request_stop("unknown") is False


@pytest.mark.asyncio
async def test_expired_cached_path_does_not_block_url_resolution(tmp_path) -> None:
    """A removed cache file must fall back to its still-valid URL."""
    from nahida_bot.plugins.base import InboundAttachment
    from nahida_bot.core.session_runner import SessionRunner

    runner = SessionRunner()
    attachment = InboundAttachment(
        kind="image",
        platform_id="image-1",
        url="https://example.test/image.png",
        path=str(tmp_path / "expired.png"),
    )

    result = await runner._download_platform_attachment_if_needed(attachment)

    assert result.path == ""
    assert result.url == attachment.url


def test_explicit_empty_tool_allowlist_exposes_no_tools() -> None:
    """An explicit empty allowlist is restrictive, unlike ``None``."""
    from nahida_bot.core.session_runner import SessionRunner
    from nahida_bot.plugins.registry import ToolEntry, ToolRegistry

    async def handler() -> str:
        return "ok"

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="exec",
            description="execute",
            parameters={"type": "object"},
            handler=handler,
            plugin_id="test",
        )
    )
    runner = SessionRunner(tool_registry=registry)

    assert [tool.name for tool in runner._collect_tools(None)] == ["exec"]
    assert runner._collect_tools(None, tool_allowlist=frozenset()) == []


@pytest.mark.asyncio
async def test_desktop_announce_tool_is_visible_only_to_cron_runs() -> None:
    from nahida_bot.agent.loop import LoopEvent
    from nahida_bot.core.session_runner import SessionRunner
    from nahida_bot.plugins.registry import ToolEntry, ToolRegistry

    async def handler() -> str:
        return "ok"

    class _CapturingAgent:
        def __init__(self) -> None:
            self.tool_names: list[list[str]] = []

        async def run_stream(self, **kwargs):
            self.tool_names.append([tool.name for tool in kwargs["tools"]])
            yield LoopEvent(type="done", final_response="done")

    registry = ToolRegistry()
    for name in ("desktop_announce", "workspace_read"):
        registry.register(
            ToolEntry(
                name=name,
                description=name,
                parameters={"type": "object"},
                handler=handler,
                plugin_id="test",
            )
        )
    agent = _CapturingAgent()
    runner = SessionRunner(agent_loop=agent, tool_registry=registry)

    await runner.run(
        user_message="normal",
        session_id="normal",
        system_prompt="test",
    )
    await runner.run(
        user_message="scheduled",
        session_id="cron",
        system_prompt="test",
        source_tag="cron_trigger",
    )

    assert agent.tool_names == [
        ["workspace_read"],
        ["desktop_announce", "workspace_read"],
    ]


# ── Phase 5 regression: ordered_transcript forwarding ──────────────────


class _FakeAgentLoop:
    """Yields a fixed event stream, ignoring run_stream kwargs."""

    def __init__(self, events):
        self._events = events

    async def run_stream(self, **_):
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_run_forwards_ordered_transcript_from_done_event() -> None:
    """Regression: SessionRunner rebuilt AgentRunResult from the done event
    field-by-field and dropped ``ordered_transcript``, silently no-op'ing
    Phase 5 transcript persistence (``transcript_persisted`` stayed 0 across
    every run because ``_persist_transcript`` early-returned on the empty
    default). The done event carries the transcript; the result must too."""
    from nahida_bot.agent.context import ContextMessage
    from nahida_bot.agent.loop import LoopEvent
    from nahida_bot.core.session_runner import SessionRunner

    user_msg = ContextMessage(role="user", source="user_input", content="hi")
    runner = SessionRunner(
        agent_loop=_FakeAgentLoop(
            [
                LoopEvent(
                    type="done",
                    final_response="hello",
                    ordered_transcript=[user_msg],
                    trace_id="trace-1",
                )
            ]
        )
    )
    result = await runner.run(user_message="hi", session_id="s1", system_prompt="x")
    assert result.trace_id == "trace-1"
    # Before the fix this was the default empty list.
    assert result.ordered_transcript
    assert result.ordered_transcript[0].content == "hi"


def test_build_system_prompt_injects_mention_instruction_for_capable_channel() -> None:
    from nahida_bot.core.message_context import MENTION_INSTRUCTION
    from nahida_bot.core.session_runner import SessionRunner
    from nahida_bot.plugins.base import MessageContext

    runner = SessionRunner()
    context = MessageContext(channel="milky", chat_type="group", chat_id="1")

    prompt = runner._build_system_prompt("base", context)

    assert MENTION_INSTRUCTION in prompt


def test_build_system_prompt_skips_mention_instruction_for_other_platforms() -> None:
    from nahida_bot.core.session_runner import SessionRunner
    from nahida_bot.plugins.base import MessageContext

    runner = SessionRunner()
    context = MessageContext(channel="telegram", chat_type="group", chat_id="1")

    prompt = runner._build_system_prompt("base", context)

    assert "[CQ:at,qq=" not in prompt
