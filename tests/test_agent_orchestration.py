"""Tests for the local agent orchestration MVP."""

from __future__ import annotations

from typing import Any

import pytest

from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord
from nahida_bot.agent.orchestration import (
    AgentOrchestrator,
    LocalAgentRunExecutor,
    SQLiteBackgroundTaskStore,
    SubagentSpec,
)
from nahida_bot.agent.orchestration.models import AgentRunStatus
from nahida_bot.core.context import (
    AgentRunContext,
    SessionContext,
    current_agent_run,
    current_session,
)
from nahida_bot.db.engine import DatabaseEngine


class _FakeRunner:
    def __init__(self, result: AgentRunResult | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result or AgentRunResult(final_response="child result", steps=1)

    async def run(self, **kwargs: Any) -> AgentRunResult:
        self.calls.append(kwargs)
        return self.result


class _FakeMemory:
    def __init__(self) -> None:
        self.turns: list[tuple[str, Any]] = []
        self.session_meta: dict[str, dict[str, Any]] = {}
        self.recent: dict[str, list[MemoryRecord]] = {}

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        self.session_meta.setdefault(session_id, {})

    async def update_session_meta(
        self, session_id: str, updates: dict[str, Any]
    ) -> None:
        self.session_meta.setdefault(session_id, {}).update(updates)

    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        return dict(self.session_meta.get(session_id, {}))

    async def append_turn(self, session_id: str, turn: Any) -> int:
        self.turns.append((session_id, turn))
        return len(self.turns)

    async def get_recent(self, session_id: str, *, limit: int = 50) -> list[Any]:
        return list(self.recent.get(session_id, []))[-limit:]


@pytest.mark.asyncio
async def test_spawn_subagent_runs_child_session_and_writes_completion(
    tmp_path,
) -> None:
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _FakeRunner()
        memory = _FakeMemory()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
            memory_store=memory,
        )

        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:chat1",
                workspace_id="default",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(
                SubagentSpec(
                    task="research this",
                    provider_id="p1",
                    model="model-a",
                    reasoning_effort="high",
                    tool_allowlist=("workspace_read",),
                    tool_denylist=("exec",),
                )
            )
            completed = await orchestrator.wait_for_task(
                task.task_id,
                timeout_seconds=1,
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        assert completed.status == AgentRunStatus.SUCCEEDED
        assert completed.summary == "child result"
        assert completed.child_session_id == "telegram:chat1:subagent:" + task.task_id
        assert runner.calls[0]["session_id"] == completed.child_session_id
        assert runner.calls[0]["provider_id"] == "p1"
        assert runner.calls[0]["model"] == "model-a"
        assert runner.calls[0]["reasoning_effort"] == "high"
        assert runner.calls[0]["tool_allowlist"] == frozenset({"workspace_read"})
        assert "agent_spawn" in runner.calls[0]["tool_filter"]
        assert "exec" in runner.calls[0]["tool_filter"]
        assert completed.child_session_id is not None
        assert (
            memory.session_meta[completed.child_session_id]["task_id"] == task.task_id
        )
        assert memory.session_meta[completed.child_session_id]["provider_id"] == "p1"
        assert memory.session_meta[completed.child_session_id]["model"] == "model-a"
        assert (
            memory.session_meta[completed.child_session_id]["runtime"]["reasoning"][
                "effort"
            ]
            == "high"
        )
        assert memory.turns[0][0] == "telegram:chat1"
        assert memory.turns[0][1].source == "subagent_completed"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_subagent_cannot_spawn_nested_subagent(tmp_path) -> None:
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(_FakeRunner()),
            task_store=SQLiteBackgroundTaskStore(engine),
        )
        session_token = current_session.set(
            SessionContext(
                platform="agent",
                chat_id="task1",
                session_id="parent:subagent:task1",
            )
        )
        run_token = current_agent_run.set(
            AgentRunContext(
                run_id="run_child",
                task_id="task_child",
                session_id="parent:subagent:task1",
                requester_session_id="parent",
                depth=1,
            )
        )
        try:
            with pytest.raises(PermissionError, match="cannot spawn"):
                await orchestrator.spawn_subagent(SubagentSpec(task="nested"))
        finally:
            current_agent_run.reset(run_token)
            current_session.reset(session_token)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_subagent_empty_response_is_failed(tmp_path) -> None:
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _FakeRunner(AgentRunResult(final_response=""))
        memory = _FakeMemory()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
            memory_store=memory,
        )

        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:chat1",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="research this"))
            completed = await orchestrator.wait_for_task(
                task.task_id,
                timeout_seconds=1,
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        assert completed.status == AgentRunStatus.FAILED
        assert completed.error == "Subagent completed without a final response."
        assert memory.turns[0][1].metadata["status"] == "failed"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_subagent_fork_context_seeds_child_session(tmp_path) -> None:
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _FakeRunner()
        memory = _FakeMemory()
        memory.recent["telegram:chat1"] = [
            MemoryRecord(
                turn_id=1,
                session_id="telegram:chat1",
                turn=ConversationTurn(
                    role="user",
                    content="parent request",
                    source="user_input",
                    metadata={"k": "v"},
                ),
            ),
            MemoryRecord(
                turn_id=2,
                session_id="telegram:chat1",
                turn=ConversationTurn(
                    role="assistant",
                    content="parent answer",
                    source="agent_response",
                ),
            ),
        ]
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
            memory_store=memory,
        )

        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:chat1",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(
                SubagentSpec(task="research this", context_mode="fork")
            )
            completed = await orchestrator.wait_for_task(
                task.task_id,
                timeout_seconds=1,
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        assert completed.status == AgentRunStatus.SUCCEEDED
        seeded = [
            turn
            for session_id, turn in memory.turns
            if session_id == completed.child_session_id
        ]
        assert [turn.content for turn in seeded] == ["parent request", "parent answer"]
        assert seeded[0].source == "subagent_fork:user_input"
        assert seeded[0].metadata["forked_from_session"] == "telegram:chat1"
    finally:
        await engine.close()
