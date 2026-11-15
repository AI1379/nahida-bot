"""Model compaction preserves continuity and fails back without losing raw history."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from nahida_bot.agent.compaction import ModelCompactor, SUMMARY_SOURCE
from nahida_bot.agent.context import (
    ContextBuilder,
    ContextMessage,
    ContextPart,
    build_context_budget,
)
from nahida_bot.agent.loop import AgentLoop, ToolExecutionResult, ToolExecutor
from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
from nahida_bot.agent.providers.base import (
    ChatProvider,
    ModelCapabilities,
    ProviderResponse,
    ToolCall,
    ToolDefinition,
    current_provider_request_context,
)
from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
from nahida_bot.agent.runtime.models import AgentRunContext
from nahida_bot.agent.runtime.transcript import (
    TranscriptProjector,
    transcript_to_payload,
)
from nahida_bot.agent.tokenization import CharacterEstimateTokenizer
from nahida_bot.core.config import ContextConfig
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_agent_run_repo import SQLiteAgentRunStore


class Provider(ChatProvider):
    name = "compaction-test"

    def __init__(
        self,
        summary="用户偏好自然的纳西妲语气；已确认操作结果，等待下一项请求。",
        responses=None,
    ):
        self.summary = summary
        self.responses = list(responses or [ProviderResponse(content="done")])
        self.compactions = []
        self.normal = []
        self.started = asyncio.Event()
        self.cancelled = False

    @property
    def tokenizer(self):
        return CharacterEstimateTokenizer()

    async def _chat_impl(
        self, *, messages, tools=None, timeout_seconds=None, model=None
    ):
        if messages[0].source == "compaction_instruction":
            self.compactions.append(
                (messages, tools, model, current_provider_request_context.get())
            )
            self.started.set()
            if self.summary == "BLOCK":
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled = True
            if isinstance(self.summary, Exception):
                raise self.summary
            if isinstance(self.summary, ProviderResponse):
                return self.summary
            return ProviderResponse(content=self.summary)
        self.normal.append(messages)
        return self.responses.pop(0)


def builder(**overrides):
    config = ContextConfig(**overrides)
    return ContextBuilder(
        budget=build_context_budget(config), tokenizer=CharacterEstimateTokenizer()
    )


@pytest.fixture
async def storage():
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        yield SQLiteMemoryStore(engine), SQLiteAgentRunStore(engine)
    finally:
        await engine.close()


async def seed(memory, runs, count, session="s", offset=0):
    await memory.ensure_session(session)
    for i in range(offset, count + offset):
        started = datetime(2026, 11, 5, tzinfo=UTC) + timedelta(minutes=i)
        user = f"user-{i}: 保留中文人设和约定" + "context " * 80
        await memory.append_turn(
            session, ConversationTurn("user", user, created_at=started)
        )
        await memory.append_turn(
            session, ConversationTurn("assistant", f"answer-{i}", created_at=started)
        )
        run_id = f"{session}-{i}"
        await runs.create_run(
            AgentRunContext(run_id=run_id, session_id=session),
            started_at=started.isoformat(),
        )
        await runs.save_transcript(
            run_id,
            transcript_to_payload(
                [
                    ContextMessage(role="user", source="user_input", content=user),
                    ContextMessage(
                        role="assistant", source="assistant", content=f"answer-{i}"
                    ),
                ]
            ),
        )


async def history(memory, runs, provider, *, session="s", max_messages=30, **config):
    runner = SessionRunner(
        memory_store=memory,
        transcript_projector=TranscriptProjector(runs),
        max_history_turns=max_messages,
        context_config=ContextConfig(**config),
    )
    records = await memory.get_recent(session, limit=800)
    return await runner._build_history_context(
        session,
        records,
        capabilities=ModelCapabilities(tool_calling=True),
        compaction_builder=builder(**config),
        compaction_provider=provider,
        compaction_model="selected-model",
    )


def test_application_budget_enables_model_compaction_by_default():
    assert builder().budget.model_compaction_enabled


@pytest.mark.asyncio
async def test_compactor_uses_data_only_input_and_selected_model():
    provider = Provider()
    message = ContextMessage(
        role="tool",
        source="tool_result:exec",
        content="status=success; path=/tmp/report",
        reasoning="PRIVATE REASONING",
        parts=[ContextPart(type="image_base64", data="PRIVATE IMAGE")],
        metadata={"tool_call_id": "call-1", "tool_calls": [{"id": "call-1"}]},
    )
    result = await ModelCompactor(builder()).summarize(
        [message], provider=provider, model="chosen"
    )
    assert result
    messages, tools, model, context = provider.compactions[0]
    assert tools == [] and model == "chosen" and not context.allow_builtin_tools
    assert "call-1" in messages[1].content and "/tmp/report" in messages[1].content
    assert "PRIVATE REASONING" not in messages[1].content
    assert "PRIVATE IMAGE" not in messages[1].content
    assert current_provider_request_context.get().allow_builtin_tools


@pytest.mark.asyncio
async def test_large_summary_input_is_chunked_and_merges_previous_summary():
    provider = Provider(summary="连续摘要")
    b = builder(compaction_max_input_chars=1024)
    result = await ModelCompactor(b).summarize(
        [ContextMessage(role="user", source="history", content="x" * 3000)],
        provider=provider,
        previous_summary="之前的决定",
    )
    assert result == "连续摘要"
    assert len(provider.compactions) > 1
    assert "之前的决定" in provider.compactions[0][0][1].content
    assert "连续摘要" in provider.compactions[1][0][1].content
    assert all(
        b._estimate_tokens(call[0]) < b.budget.soft_token_limit
        for call in provider.compactions
    )


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("provider failed"),
        "",
        ProviderResponse(content="partial", finish_reason="length"),
        ProviderResponse(content="refused", refusal="no"),
        ProviderResponse(
            content="tool",
            tool_calls=[ToolCall(call_id="x", name="exec", arguments={})],
        ),
        "x" * 12001,
    ],
)
@pytest.mark.asyncio
async def test_failed_compaction_falls_back_without_checkpointing(storage, failure):
    memory, runs = storage
    await seed(memory, runs, 25)
    result = await history(memory, runs, Provider(summary=failure))
    assert not any(message.source == SUMMARY_SOURCE for message in result)
    assert len(result) == 30 and result[-1].content == "answer-24"
    assert "context_compaction" not in await memory.get_session_meta("s")
    assert len(await memory.get_recent("s", limit=100)) == 50


@pytest.mark.asyncio
async def test_summary_survives_restart_and_updates_only_with_uncovered_history(
    storage,
):
    memory, runs = storage
    await seed(memory, runs, 25)
    provider = Provider()
    result = await history(memory, runs, provider)
    assert result[0].source == SUMMARY_SOURCE
    assert len(result) == 9
    assert (
        "user-0:" in provider.compactions[0][0][1].content
    )  # beyond old 20-run window
    assert not any("_history_timestamp" in (m.metadata or {}) for m in result)
    checkpoint = (await memory.get_session_meta("s"))["context_compaction"]
    calls = len(provider.compactions)
    # A fresh SessionRunner must reuse the persisted checkpoint without a model call.
    again = await history(memory, runs, provider)
    assert result == again and len(provider.compactions) == calls
    await seed(memory, runs, 13, offset=25)
    updated = await history(memory, runs, provider)
    assert updated[0].source == SUMMARY_SOURCE
    new_input = provider.compactions[calls][0][1].content
    assert checkpoint["summary"] in new_input
    assert "user-0:" not in new_input and "user-21:" in new_input
    assert (await memory.get_session_meta("s"))["context_compaction"][
        "through"
    ] > checkpoint["through"]


@pytest.mark.asyncio
async def test_failed_update_keeps_previous_summary_and_does_not_advance_checkpoint(
    storage,
):
    memory, runs = storage
    await seed(memory, runs, 25)
    await history(memory, runs, Provider())
    checkpoint = (await memory.get_session_meta("s"))["context_compaction"]
    await seed(memory, runs, 13, offset=25)
    result = await history(memory, runs, Provider(summary=RuntimeError("offline")))
    assert result[0].source == SUMMARY_SOURCE
    assert checkpoint["summary"] in result[0].content
    assert (await memory.get_session_meta("s"))["context_compaction"] == checkpoint
    assert result[-1].content == "answer-37"


@pytest.mark.asyncio
async def test_history_budget_never_checkpoints_only_part_of_one_run(storage):
    from nahida_bot.agent.compaction import HISTORY_TIMESTAMP
    from nahida_bot.core.session_compaction import (
        HistoryCompactionScope,
        SessionHistoryCompactor,
    )

    memory, _runs = storage
    await memory.ensure_session("s")
    messages = []
    for run, turn_count in [(0, 1), (1, 4), (2, 1)]:
        timestamp = (
            datetime(2026, 11, 5, tzinfo=UTC) + timedelta(minutes=run)
        ).isoformat()
        for turn in range(turn_count):
            for role in ("user", "assistant"):
                messages.append(
                    ContextMessage(
                        role=role,
                        source="history",
                        content=f"run-{run}-turn-{turn}-{role}",
                        metadata={HISTORY_TIMESTAMP: timestamp},
                    )
                )
    provider = Provider()
    scope = HistoryCompactionScope("s", "replay", "", 5)
    first = await SessionHistoryCompactor(memory, builder(), provider).compact(
        messages, scope
    )
    restored = await SessionHistoryCompactor(memory, builder(), provider).compact(
        messages, scope
    )
    assert first == restored
    assert restored[-1].content == "run-2-turn-0-assistant"
    assert len(provider.compactions) == 1


@pytest.mark.asyncio
async def test_checkpoints_are_session_local_and_reset_clears_summary_and_replay(
    storage,
):
    memory, runs = storage
    await seed(memory, runs, 25)
    await history(memory, runs, Provider())
    await seed(memory, runs, 1, session="other")
    other = await history(memory, runs, Provider(), session="other")
    assert not any(m.source == SUMMARY_SOURCE for m in other)
    await memory.update_session_meta("s", {"model": "keep-this"})
    await memory.clear_session("s")
    meta = await memory.get_session_meta("s")
    assert "context_compaction" not in meta and meta["model"] == "keep-this"
    assert await history(memory, runs, Provider()) == []


@pytest.mark.asyncio
async def test_checkpoint_write_failure_retains_original_history(storage, monkeypatch):
    memory, runs = storage
    await seed(memory, runs, 25)

    async def fail(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(memory, "update_session_meta", fail)
    result = await history(memory, runs, Provider())
    assert len(result) == 30 and not any(m.source == SUMMARY_SOURCE for m in result)


@pytest.mark.asyncio
async def test_timeout_falls_back_and_cancels_provider():
    provider = Provider(summary="BLOCK")
    result = await ModelCompactor(builder(compaction_timeout_seconds=0.02)).summarize(
        [ContextMessage(role="user", source="history", content="x" * 1000)],
        provider=provider,
    )
    assert result is None and provider.cancelled


@pytest.mark.asyncio
async def test_stop_interrupts_compaction_without_leaking_tasks():
    provider = Provider(summary="BLOCK")
    stop = asyncio.Event()
    task = asyncio.create_task(
        ModelCompactor(builder()).summarize(
            [ContextMessage(role="user", source="history", content="x" * 1000)],
            provider=provider,
            stop_event=stop,
        )
    )
    await provider.started.wait()
    stop.set()
    assert await task is None and provider.cancelled


@pytest.mark.asyncio
async def test_external_cancellation_propagates():
    provider = Provider(summary="BLOCK")
    task = asyncio.create_task(
        ModelCompactor(builder()).summarize(
            [ContextMessage(role="user", source="history", content="x" * 1000)],
            provider=provider,
        )
    )
    await provider.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.cancelled


@pytest.mark.asyncio
async def test_history_token_threshold_triggers_before_message_window(storage):
    memory, runs = storage
    await seed(memory, runs, 5)
    provider = Provider()
    result = await history(memory, runs, provider, compaction_history_tokens=500)
    assert provider.compactions and result[0].source == SUMMARY_SOURCE


@pytest.mark.asyncio
async def test_disabled_compaction_uses_original_window(storage):
    memory, runs = storage
    await seed(memory, runs, 25)
    provider = Provider()
    result = await history(memory, runs, provider, model_compaction_enabled=False)
    assert not provider.compactions and len(result) == 30


@pytest.mark.asyncio
async def test_legacy_history_also_uses_persisted_model_summary(storage):
    memory, runs = storage
    await seed(memory, runs, 25)
    provider = Provider()
    runner = SessionRunner(memory_store=memory, max_history_turns=30)
    records = await memory.get_recent("s", limit=100)
    result = await runner._build_history_context(
        "s", records, compaction_provider=provider, compaction_builder=builder()
    )
    assert result[0].source == SUMMARY_SOURCE and len(result) == 9
    assert (await memory.get_session_meta("s"))["context_compaction"][
        "mode"
    ] == "legacy"


@pytest.mark.asyncio
async def test_full_session_pipeline_uses_selected_model_and_reuses_summary(storage):
    memory, runs = storage
    await seed(memory, runs, 25)
    provider = Provider(
        responses=[
            ProviderResponse(content="first"),
            ProviderResponse(content="second"),
        ]
    )
    b = builder()
    loop = AgentLoop(provider=provider, context_builder=b, run_store=runs)
    manager = ProviderManager(
        [
            ProviderSlot(
                id="selected",
                provider=provider,
                context_builder=b,
                default_model="chosen-model",
            )
        ]
    )
    runner = SessionRunner(
        agent_loop=loop,
        memory_store=memory,
        provider_manager=manager,
        transcript_projector=TranscriptProjector(runs),
        max_history_turns=30,
    )
    first = await runner.run(
        user_message="new request", session_id="s", system_prompt="system"
    )
    assert first.final_response == "first" and provider.compactions
    assert provider.compactions[0][2] == "chosen-model"
    assert any(m.source == SUMMARY_SOURCE for m in provider.normal[0])
    assert provider.normal[0][-1].content.endswith("new request")
    calls = len(provider.compactions)
    second = await runner.run(
        user_message="followup", session_id="s", system_prompt="system"
    )
    assert second.final_response == "second" and len(provider.compactions) == calls


@pytest.mark.asyncio
async def test_group_scope_change_does_not_reuse_old_summary(storage):
    from nahida_bot.core.session_compaction import (
        HistoryCompactionScope,
        SessionHistoryCompactor,
    )

    memory, runs = storage
    await seed(memory, runs, 25)
    await history(memory, runs, Provider())
    result = await SessionHistoryCompactor(memory, builder(), Provider()).compact(
        [], HistoryCompactionScope("s", "replay", "a new group conversation", 30)
    )
    assert result == []


@pytest.mark.asyncio
async def test_loop_compacts_before_clipping_preserving_persona_and_live_request(
    tmp_path,
):
    (tmp_path / "SOUL.md").write_text(
        "你是纳西妲，温柔且有自己的判断。", encoding="utf-8"
    )
    provider = Provider()
    b = builder(max_tokens=4000, reserved_tokens=0)
    loop = AgentLoop(provider=provider, context_builder=b)
    result = await loop.run(
        user_message="当前请求必须完整保留",
        system_prompt="system",
        workspace_root=tmp_path,
        history_messages=[
            ContextMessage(role="user", source="history", content="old " * 6000)
        ],
    )
    assert result.final_response == "done" and provider.compactions
    prompt = provider.normal[0]
    assert any(m.source == "workspace_instruction:SOUL.md" for m in prompt)
    assert any(m.source == SUMMARY_SOURCE for m in prompt)
    assert prompt[-1].content == "当前请求必须完整保留"
    assert (
        "纳西妲" not in provider.compactions[0][0][1].content
    )  # persona wasn't compacted


class LargeTools(ToolExecutor):
    async def execute(self, tool_call):
        return ToolExecutionResult.success(output=tool_call.call_id + "x" * 7000)


@pytest.mark.asyncio
async def test_mid_run_compaction_preserves_latest_pair_and_original_transcript():
    provider = Provider(
        responses=[
            ProviderResponse(
                content="",
                tool_calls=[ToolCall(call_id="a", name="exec", arguments={})],
            ),
            ProviderResponse(
                content="",
                tool_calls=[ToolCall(call_id="b", name="exec", arguments={})],
            ),
            ProviderResponse(content="finished"),
        ]
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder(max_tokens=4000, reserved_tokens=0),
        tool_executor=LargeTools(),
    )
    result = await loop.run(
        user_message="keep the current request",
        system_prompt="system",
        tools=[
            ToolDefinition(
                name="exec", description="test", parameters={"type": "object"}
            )
        ],
    )
    assert result.final_response == "finished" and provider.compactions
    raw_tools = [m for m in result.ordered_transcript if m.role == "tool"]
    assert len(raw_tools) == 2 and all(len(m.content) > 7000 for m in raw_tools)
    prompt = provider.normal[-1]
    assert any(m.content == "keep the current request" for m in prompt)
    assert any(m.source == SUMMARY_SOURCE for m in prompt)
    calls = [m for m in prompt if (m.metadata or {}).get("tool_calls")]
    tools = [m for m in prompt if m.role == "tool"]
    assert len(calls) == len(tools) == 1
    assert tools[0].metadata["tool_call_id"] == "b"
