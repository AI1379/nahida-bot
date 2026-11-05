"""Committed-turn maintenance releases the foreground and keeps stable evidence."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.core.task_runtime import deadline_scope, remaining_seconds
from nahida_bot.core.task_runtime.postprocess import PostprocessClosed, PostprocessQueue


async def test_queue_is_fifo_single_writer_and_worker_failure_isolated():
    queue = PostprocessQueue()
    order = []
    active = 0

    async def work(index):
        nonlocal active
        active += 1
        assert active == 1
        try:
            await asyncio.sleep(0)
            order.append(index)
            if index == 1:
                raise RuntimeError("failed maintenance")
        finally:
            active -= 1

    for index in range(3):
        await queue.submit(
            lambda index=index: work(index), size_bytes=1, label=str(index)
        )
    await queue.close()
    assert order == [0, 1, 2]
    assert queue.snapshot["completed"] == 2 and queue.snapshot["failed"] == 1
    assert queue.snapshot["pending_bytes"] == 0


async def test_full_queue_applies_backpressure_and_preserves_work():
    queue = PostprocessQueue(max_pending=1, max_pending_bytes=10)
    started, release = asyncio.Event(), asyncio.Event()
    done = []

    async def first():
        started.set()
        await release.wait()
        done.append("first")

    async def second():
        done.append("second")

    await queue.submit(first, size_bytes=10, label="first")
    await started.wait()
    submit = asyncio.create_task(queue.submit(second, size_bytes=1, label="second"))
    await asyncio.sleep(0)
    assert not submit.done()
    assert queue.snapshot["pending_bytes"] == 10
    release.set()
    await submit
    await queue.close()
    assert done == ["first", "second"]


async def test_oversized_job_drains_and_waits_instead_of_accumulating_large_payloads():
    queue = PostprocessQueue(max_pending_bytes=1)
    started, release = asyncio.Event(), asyncio.Event()

    async def oversized():
        started.set()
        await release.wait()

    task = asyncio.create_task(queue.submit(oversized, size_bytes=2, label="large"))
    await started.wait()
    assert not task.done()
    release.set()
    await task
    await queue.close()


async def test_shutdown_cancels_pending_work_and_wakes_blocked_submitters():
    queue = PostprocessQueue(max_pending=1)
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    await queue.submit(work, size_bytes=1, label="running")
    await started.wait()
    blocked = asyncio.create_task(queue.submit(work, size_bytes=1, label="blocked"))
    await asyncio.sleep(0)
    await queue.close(timeout=0)
    with pytest.raises(PostprocessClosed):
        await blocked
    assert cleaned.is_set()
    assert queue.snapshot["cancelled"] == 1
    assert queue.snapshot["running"] == queue.snapshot["pending_bytes"] == 0


async def test_worker_does_not_inherit_turn_identity_or_deadline():
    queue = PostprocessQueue()
    observed = []

    async def work():
        observed.append((current_session.get(), remaining_seconds()))

    token = current_session.set(
        SessionContext(platform="test", chat_id="chat", session_id="s")
    )
    try:
        with deadline_scope(0.001):
            await queue.submit(work, size_bytes=1, label="s")
    finally:
        current_session.reset(token)
    await queue.close()
    assert observed == [(None, None)]


async def test_turn_commit_finishes_while_memory_work_is_still_running(monkeypatch):
    queue = PostprocessQueue()
    turns = []

    async def append(session_id, turn):
        turns.append(turn)
        return len(turns)

    memory = SimpleNamespace(append_turn=append)
    runner = SessionRunner(memory_store=memory, memory_postprocessor=queue)
    started, release = asyncio.Event(), asyncio.Event()

    async def consolidate(**kwargs):
        assert len(turns) == 2
        started.set()
        await release.wait()

    monkeypatch.setattr(runner, "_consolidate_memory_after_turn", consolidate)
    try:
        await runner._persist_turns(
            "s",
            "user",
            AgentRunResult(final_response="reply"),
            attachments=[],
            source_tag="user_input",
        )
        await started.wait()
        assert [turn.role for turn in turns] == ["user", "assistant"]
        assert queue.snapshot["running"] == 1
    finally:
        release.set()
        await queue.close()


async def test_queue_captures_original_account_and_evidence_before_next_turn():
    queue = PostprocessQueue()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocker():
        entered.set()
        await release.wait()

    await queue.submit(blocker, size_bytes=1, label="block")
    await entered.wait()
    runner = SessionRunner(memory_store=object(), memory_postprocessor=queue)
    consolidate = AsyncMock(return_value=0)
    runner._memory_consolidator = SimpleNamespace(consolidate_turn=consolidate)
    metadata = {"message_context": {"sender_id": "alice"}}
    records = [
        MemoryRecord(
            42,
            "test:private:chat",
            ConversationTurn("user", "original", metadata=metadata),
        )
    ]
    token = current_session.set(
        SessionContext(
            platform="test",
            chat_id="chat",
            session_id="test:private:chat",
            sender_account_key="test:user:alice",
            person_id="alice",
        )
    )
    try:
        await runner._submit_memory_after_turn(
            session_id="test:private:chat",
            user_message="original",
            assistant_message="reply",
            workspace_id=None,
            workspace_root=None,
            records=records,
        )
    finally:
        current_session.reset(token)
    metadata["message_context"]["sender_id"] = "bob"
    records.clear()
    release.set()
    await queue.close()
    consolidate.assert_awaited_once()
    arguments = consolidate.call_args.kwargs
    assert arguments["sender_account_key"] == "test:user:alice"
    assert arguments["person_id"] == "alice"
    assert arguments["records"][0].turn_id == 42
    assert arguments["records"][0].turn.content == "original"


async def test_next_turn_runs_before_previous_memory_maintenance_finishes(monkeypatch):
    from tests.test_message_router import _MockAgentLoop, _MockMemoryStore

    queue = PostprocessQueue()
    agent = _MockAgentLoop()
    memory = _MockMemoryStore()
    runner = SessionRunner(
        agent_loop=agent, memory_store=memory, memory_postprocessor=queue
    )
    started, release = asyncio.Event(), asyncio.Event()
    completed = []

    async def consolidate(**kwargs):
        started.set()
        await release.wait()
        completed.append(kwargs["user_message"])

    monkeypatch.setattr(runner, "_consolidate_memory_after_turn", consolidate)
    try:
        await runner.run(
            user_message="first", session_id="test:private:chat", system_prompt="sys"
        )
        await started.wait()
        async with asyncio.timeout(2):
            await runner.run(
                user_message="second",
                session_id="test:private:chat",
                system_prompt="sys",
            )
        assert len(agent.calls) == 2 and not completed
        assert [
            turn.content
            for turn in memory.sessions["test:private:chat"]
            if turn.role == "user"
        ] == ["first", "second"]
    finally:
        release.set()
        await queue.close()
    assert completed == ["first", "second"]


@pytest.mark.parametrize("started", [True, False])
async def test_application_stops_maintenance_before_provider_and_database(started):
    from nahida_bot.core.app import Application
    from nahida_bot.core.config import Settings

    app = Application(Settings())
    app._started = started
    entered, finished = asyncio.Event(), asyncio.Event()

    async def work():
        entered.set()
        try:
            await asyncio.sleep(0.02)
        finally:
            finished.set()

    async def close_resource():
        assert finished.is_set()
        assert app.memory_postprocessor.snapshot["running"] == 0

    await app.memory_postprocessor.submit(work, size_bytes=1, label="turn")
    await entered.wait()
    app._providers_to_close = [SimpleNamespace(close=close_resource)]
    app._db_engine = SimpleNamespace(close=close_resource)
    await app.stop()
    assert finished.is_set() and app.memory_postprocessor.closed


async def test_orchestrator_shutdown_cancels_running_and_queued_producers(tmp_path):
    from nahida_bot.agent.orchestration import (
        AgentOrchestrator,
        LocalAgentRunExecutor,
        OrchestrationConfig,
        SQLiteBackgroundTaskStore,
        SubagentSpec,
    )
    from nahida_bot.db.engine import DatabaseEngine

    entered = asyncio.Event()

    class Runner:
        async def run(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    engine = DatabaseEngine(tmp_path / "shutdown.db")
    await engine.initialize()
    store = SQLiteBackgroundTaskStore(engine)
    orchestrator = AgentOrchestrator(
        executor=LocalAgentRunExecutor(Runner()),
        task_store=store,
        config=OrchestrationConfig(subagent_concurrency=1),
    )
    token = current_session.set(
        SessionContext(platform="test", chat_id="chat", session_id="s")
    )
    try:
        first = await orchestrator.spawn_subagent(
            SubagentSpec(task="first", notify_policy="silent")
        )
        await entered.wait()
        second = await orchestrator.spawn_subagent(
            SubagentSpec(task="second", notify_policy="silent")
        )
        await asyncio.sleep(0)
        await orchestrator.shutdown(timeout=0)
        assert (await store.get(first.task_id)).status.value == "cancelled"
        assert (await store.get(second.task_id)).status.value == "cancelled"
        assert not orchestrator._registry.all_runs
        with pytest.raises(RuntimeError, match="shutting down"):
            await orchestrator.spawn_subagent(SubagentSpec(task="late"))
    finally:
        current_session.reset(token)
        await orchestrator.shutdown(timeout=0)
        await engine.close()


async def test_application_abort_does_not_wait_for_maintenance_grace_period():
    from nahida_bot.core.app import Application
    from nahida_bot.core.config import Settings

    app = Application(Settings())
    app._started = True
    entered, cleaned, abort = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    await app.memory_postprocessor.submit(work, size_bytes=1, label="waiting")
    await entered.wait()
    abort.set()
    async with asyncio.timeout(2):
        await app.stop(abort_event=abort)
    assert cleaned.is_set()


async def test_explicit_archive_cannot_be_undone_by_older_queued_consolidation():
    from unittest.mock import MagicMock
    from nahida_bot.plugins.api_bridge import RealBotAPI

    queue = PostprocessQueue()
    entered, release = asyncio.Event(), asyncio.Event()
    facts = set()

    async def old_consolidation():
        entered.set()
        await release.wait()
        facts.add("fact")

    async def archive(item_id, **kwargs):
        assert item_id in facts
        facts.remove(item_id)
        return True

    api = object.__new__(RealBotAPI)
    api._permissions = SimpleNamespace(check_memory_write=lambda: None)
    api._memory = object()
    api._memory_service_cache = SimpleNamespace(archive_item_for_context=archive)
    api._event_bus = SimpleNamespace(
        context=SimpleNamespace(app=SimpleNamespace(memory_postprocessor=queue))
    )
    api._logger = MagicMock()
    await queue.submit(old_consolidation, size_bytes=1, label="old")
    await entered.wait()
    task = asyncio.create_task(api.memory_archive("fact"))
    try:
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        assert await task
        await queue.join()
        assert not facts
    finally:
        release.set()
        await queue.close()


async def test_explicit_mutation_keeps_new_maintenance_behind_the_edit():
    queue = PostprocessQueue()
    order = []

    async def newer():
        order.append("newer")

    async with queue.mutation():
        submit = asyncio.create_task(queue.submit(newer, size_bytes=1, label="new"))
        await asyncio.sleep(0)
        assert not submit.done()
        order.append("explicit")
    await submit
    await queue.close()
    assert order == ["explicit", "newer"]
