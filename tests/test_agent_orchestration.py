"""Tests for the local agent orchestration MVP."""

from __future__ import annotations

import asyncio
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
from nahida_bot.agent.orchestration.models import (
    AgentRun,
    AgentRunKind,
    AgentRunStatus,
)
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import (
    AgentRunContext,
    SessionContext,
    current_agent_run,
    current_session,
)
from nahida_bot.db.engine import DatabaseEngine


class _FakeRunner:
    def __init__(
        self,
        result: AgentRunResult | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        # Default result carries the trusted ``completed`` terminal state that
        # the real loop emits on success. Tests that want to exercise other
        # terminal states (incomplete/failed/unverified) pass an explicitly
        # constructed ``AgentRunResult`` — those are used verbatim so the
        # orchestrator's fail-closed mapping is observable (issue #42).
        self.result = result or AgentRunResult(
            final_response="child result",
            steps=1,
            terminal_state="completed",
            terminal_reason="tool_calls_completed",
        )

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
        # Loop reports a trusted "completed" terminal state but no text — the
        # orchestrator must still mark this FAILED rather than inferring
        # success from the (absent) final response (issue #42).
        runner = _FakeRunner(
            AgentRunResult(final_response="", terminal_state="completed")
        )
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
        assert completed.terminal_state == "completed"
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_state, terminal_reason, expected_status, expected_status_prefix",
    [
        # completed run is the only path to SUCCEEDED (issue #42).
        ("completed", "tool_calls_completed", AgentRunStatus.SUCCEEDED, ""),
        # Incomplete (e.g. max_steps) must NOT be marked success even when the
        # loop left a non-empty fallback response behind.
        (
            "incomplete",
            "max_steps_reached",
            AgentRunStatus.FAILED,
            "Subagent did not complete (max_steps_reached)",
        ),
        # Explicit loop-level failure.
        (
            "failed",
            "provider_error",
            AgentRunStatus.FAILED,
            "Subagent run failed:",
        ),
        # Unverified (legacy executor / unexpected early return) is fail-closed.
        (
            "",
            "",
            AgentRunStatus.FAILED,
            "Subagent finished with unverified terminal state",
        ),
    ],
)
async def test_subagent_terminal_state_drives_task_status(
    tmp_path,
    terminal_state: str,
    terminal_reason: str,
    expected_status: AgentRunStatus,
    expected_status_prefix: str,
) -> None:
    """Issue #42: ledger inherits the loop's trusted terminal state.

    A non-empty fallback response must not be enough to mark a task
    succeeded — only a loop-reported ``completed`` may map to SUCCEEDED.
    """
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _FakeRunner(
            AgentRunResult(
                final_response="partial text should not imply success",
                steps=8,
                terminal_state=terminal_state,
                terminal_reason=terminal_reason,
                error="provider_error" if terminal_state == "failed" else None,
            )
        )
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
        )

        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:chat1",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            completed = await orchestrator.wait_for_task(
                task.task_id,
                timeout_seconds=1,
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        assert completed.status == expected_status
        if expected_status_prefix:
            assert completed.error.startswith(expected_status_prefix)
        # Trusted terminal state must be persisted on the ledger regardless of
        # the derived task status, so callers can distinguish the loop's verdict
        # from the task-status mapping.
        if terminal_state:
            assert completed.terminal_state == terminal_state
            assert completed.terminal_reason == terminal_reason
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_subagent_cancelled_marks_task_cancelled(tmp_path) -> None:
    """Issue #42: a cancelled run records the trusted ``cancelled`` terminal state.

    Covers the ``except asyncio.CancelledError`` branch in ``_run_subagent``,
    which must persist ``terminal_state='cancelled'`` on the ledger so a
    cancelled run is never confused with success.
    """
    from nahida_bot.agent.orchestration.service import AgentOrchestrator

    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:

        class _BlockingRunner:
            """A runner that blocks forever until its task is cancelled."""

            async def run(self, **kwargs: Any) -> AgentRunResult:
                await asyncio.Event().wait()  # never resolves
                return AgentRunResult(final_response="unreachable")

        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(_BlockingRunner()),
            task_store=SQLiteBackgroundTaskStore(engine),
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:chat1",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            run = orchestrator._registry.get_by_task(task.task_id)
            assert run is not None and run.asyncio_task is not None
            # Let the runner enter its try block (acquire the semaphore,
            # transition to RUNNING, and block inside the executor) before
            # cancelling, so the cancellation is handled by the
            # ``except asyncio.CancelledError`` branch.
            for _ in range(20):
                await asyncio.sleep(0)
                if run.status == AgentRunStatus.RUNNING:
                    break
            await asyncio.sleep(0.05)  # ensure executor.run is awaiting
            run.asyncio_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await orchestrator.wait_for_task(task.task_id, timeout_seconds=1)
        finally:
            current_session.reset(session_token)

        completed = await orchestrator._task_store.get(task.task_id)
        assert completed is not None
        assert completed.status == AgentRunStatus.CANCELLED
        assert completed.terminal_state == "cancelled"
        assert completed.terminal_reason == "cancelled"
    finally:
        await engine.close()


# --- issue #39: identity delegation to child runs --------------------------


class _SessionCapturingRunner:
    """Fake runner that records the child ``current_session`` it runs under."""

    def __init__(self) -> None:
        self.captured: SessionContext | None = None
        self.result = AgentRunResult(
            final_response="ok",
            terminal_state="completed",
            terminal_reason="tool_calls_completed",
        )

    async def run(self, **kwargs: Any) -> AgentRunResult:
        self.captured = current_session.get()
        return self.result


@pytest.mark.asyncio
async def test_child_run_inherits_parent_sender_account_key(tmp_path) -> None:
    """Issue #39: the child SessionContext carries the parent's sender identity.

    Without this propagation the AuthorizationGate treats every privileged
    child call as an unknown sender and rejects it, even when the parent is a
    declared admin.
    """
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _SessionCapturingRunner()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:chat1",
                sender_account_key="telegram:admin:1",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            await orchestrator.wait_for_task(task.task_id, timeout_seconds=1)
        finally:
            current_session.reset(session_token)

        assert runner.captured is not None
        # Child keeps the synthetic platform=agent routing but inherits the
        # auditable sender identity — it must NOT be reset to empty.
        assert runner.captured.platform == "agent"
        assert runner.captured.sender_account_key == "telegram:admin:1"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_child_run_with_unknown_sender_stays_fail_closed(tmp_path) -> None:
    """Issue #39: an unresolved parent sender stays empty in the child.

    The child does not invent an identity. Combined with the real
    AuthorizationGate below, privileged tools fail-closed for unknown senders.
    """
    from nahida_bot.identity.authorization import AuthorizationGate

    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _SessionCapturingRunner()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:chat1",
                # sender_account_key intentionally empty (identity unresolved).
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            await orchestrator.wait_for_task(task.task_id, timeout_seconds=1)
        finally:
            current_session.reset(session_token)

        assert runner.captured is not None
        assert runner.captured.sender_account_key == ""

        # The real gate, enabled with a declared admin, denies privileged
        # tools for the empty inherited sender — fail-closed.
        gate = AuthorizationGate(frozenset({"telegram:admin:1"}), enabled=True)
        from nahida_bot.identity.authorization import NotAuthorized

        with pytest.raises(NotAuthorized):
            gate.authorize("exec", runner.captured.sender_account_key)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_admin_sender_child_passes_authorization_gate(tmp_path) -> None:
    """Issue #39: an admin parent's inherited identity clears the gate.

    End-to-end check that the propagated sender_account_key is the same value
    the AuthorizationGate would have used for the parent — so privileged
    tools delegated to a child are executable by an admin parent but not by
    anyone else.
    """
    from nahida_bot.identity.authorization import AuthorizationGate

    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _SessionCapturingRunner()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
        )
        admin_key = "telegram:admin:1"
        gate = AuthorizationGate(frozenset({admin_key}), enabled=True)
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:chat1",
                sender_account_key=admin_key,
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            await orchestrator.wait_for_task(task.task_id, timeout_seconds=1)
        finally:
            current_session.reset(session_token)

        assert runner.captured is not None
        child_key = runner.captured.sender_account_key
        assert child_key == admin_key
        # Privileged tools delegated to the child now pass for the admin parent.
        gate.authorize("exec", child_key)
        gate.authorize("workspace_write", child_key)
    finally:
        await engine.close()


# --- issue #43: unified child tool policy ----------------------------------


def test_system_denylist_covers_nested_agent_and_delivery_tools() -> None:
    """Issue #43: the single system denylist blocks the right capabilities."""
    from nahida_bot.agent.orchestration.policy import OrchestrationPolicy

    policy = OrchestrationPolicy()
    denied = policy.system_tool_denylist
    # Nested-agent / orchestration-control tools.
    for name in ("agent_spawn", "agent_yield", "agent_wait", "agent_stop"):
        assert name in denied
    # Cross-session write.
    assert "sessions_send" in denied
    # Channel delivery is meaningless in the synthetic child context.
    assert "message" in denied
    # Identity administration can never be delegated.
    assert "identity_manage" in denied


def test_compute_child_tool_filter_system_denylist_wins() -> None:
    """Issue #43: parent cannot enable a system-denied tool via allowlist."""
    from nahida_bot.agent.orchestration.policy import OrchestrationPolicy

    policy = OrchestrationPolicy()
    spec = SubagentSpec(
        task="work",
        # Parent tries to re-enable nested spawning and channel delivery.
        tool_allowlist=("agent_spawn", "message", "workspace_read", "exec"),
        tool_denylist=("memory_read",),
    )
    allowlist, denylist = policy.compute_child_tool_filter(spec)

    # The system denylist is unioned with the spec denylist.
    assert "agent_spawn" in denylist
    assert "message" in denylist
    assert "memory_read" in denylist
    # And the same tools are stripped from the effective allowlist, so the
    # parent cannot widen the child's capabilities.
    assert "agent_spawn" not in allowlist
    assert "message" not in allowlist
    # Non-denied requested tools survive.
    assert "workspace_read" in allowlist
    assert "exec" in allowlist


def test_compute_child_tool_filter_empty_spec_means_no_restriction() -> None:
    """Issue #43: an empty allowlist means "no restriction", not "no tools"."""
    from nahida_bot.agent.orchestration.policy import OrchestrationPolicy

    policy = OrchestrationPolicy()
    allowlist, denylist = policy.compute_child_tool_filter(SubagentSpec(task="x"))
    assert allowlist == frozenset()
    assert denylist == policy.system_tool_denylist


@pytest.mark.asyncio
async def test_orchestrator_uses_policy_filter_not_legacy_denylist(tmp_path) -> None:
    """Issue #43: the service delegates to OrchestrationPolicy.compute_child_tool_filter.

    Verifies the service no longer keeps a parallel denylist and that the
    effective denylist includes both the system denylist and the spec
    denylist (e.g. ``message`` and a spec-denied ``exec``).
    """
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _FakeRunner()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
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
                SubagentSpec(
                    task="work",
                    tool_allowlist=("workspace_read",),
                    tool_denylist=("exec",),
                )
            )
            await orchestrator.wait_for_task(task.task_id, timeout_seconds=1)
        finally:
            current_session.reset(session_token)

        tool_filter = runner.calls[0]["tool_filter"]
        # System denylist member that the legacy service denylist MISSED —
        # this is the #43 regression marker.
        assert "message" in tool_filter
        assert "identity_manage" in tool_filter
        # And the spec denylist is still applied.
        assert "exec" in tool_filter
        # The effective allowlist preserves the non-denied requested tool.
        assert runner.calls[0]["tool_allowlist"] == frozenset({"workspace_read"})
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_all_denied_requested_tools_keep_restrictive_empty_allowlist(
    tmp_path,
) -> None:
    """A non-empty requested allowlist must stay restrictive after filtering."""
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        runner = _FakeRunner()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="chat1",
                session_id="telegram:private:chat1",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(
                SubagentSpec(task="work", tool_allowlist=("agent_spawn",))
            )
            await orchestrator.wait_for_task(task.task_id, timeout_seconds=1)
        finally:
            current_session.reset(session_token)

        assert runner.calls[0]["tool_allowlist"] == frozenset()
    finally:
        await engine.close()


# --- issue #41: stable delivery target + channel delivery ------------------


class _RecordingDeliverer:
    """Stand-in CompletionDeliverer that records calls and can fail on demand."""

    def __init__(self, *, succeed: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self._succeed = succeed

    async def deliver(
        self,
        *,
        task: Any,
        status: Any,
        summary: str,
        error: str,
    ) -> bool:
        self.calls.append(
            {
                "task_id": task.task_id,
                "status": status,
                "summary": summary,
                "error": error,
                "target": dict(task.delivery_target or {}),
            }
        )
        return self._succeed


@pytest.mark.asyncio
async def test_spawn_captures_delivery_target_from_real_channel(tmp_path) -> None:
    """Issue #41: spawn snapshots the originating channel for later delivery."""
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        deliverer = _RecordingDeliverer()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(_FakeRunner()),
            task_store=SQLiteBackgroundTaskStore(engine),
            completion_deliverer=deliverer,
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="42",
                session_id="telegram:private:42",
                workspace_id="default",
                chat_address=ChatAddress(
                    channel="telegram",
                    target_type="private",
                    target_id="42",
                ),
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            await orchestrator.wait_for_task(task.task_id, timeout_seconds=1)
        finally:
            current_session.reset(session_token)

        stored = await orchestrator._task_store.get(task.task_id)
        assert stored is not None
        assert stored.delivery_target == {
            "channel": "telegram",
            "target": "42",
            "chat_address": "telegram:private:42",
        }
        # The deliverer was called once for the completion.
        assert len(deliverer.calls) == 1
        assert deliverer.calls[0]["status"] == AgentRunStatus.SUCCEEDED
        assert deliverer.calls[0]["summary"] == "child result"
        assert deliverer.calls[0]["target"] == {
            "channel": "telegram",
            "target": "42",
            "chat_address": "telegram:private:42",
        }
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_spawn_skips_delivery_target_for_synthetic_session(tmp_path) -> None:
    """Issue #41: synthetic / nested sessions have no real channel to deliver to."""
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
                session_id="agent:internal:task1",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            await orchestrator.wait_for_task(task.task_id, timeout_seconds=1)
        finally:
            current_session.reset(session_token)

        stored = await orchestrator._task_store.get(task.task_id)
        assert stored is not None
        # No real channel ⇒ no delivery target; completion stays in memory.
        assert stored.delivery_target is None
        assert stored.delivered_at is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_delivery_is_at_most_once(tmp_path) -> None:
    """Issue #41: duplicate completion callbacks never double-deliver."""
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        deliverer = _RecordingDeliverer()
        runner = _FakeRunner()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(runner),
            task_store=SQLiteBackgroundTaskStore(engine),
            completion_deliverer=deliverer,
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="42",
                session_id="telegram:private:42",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            completed = await orchestrator.wait_for_task(
                task.task_id, timeout_seconds=1
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        assert len(deliverer.calls) == 1

        # Simulate a duplicate completion callback (e.g. a retry sweep firing
        # after the run already terminalized). ``_deliver_completion`` must
        # short-circuit because ``delivered_at`` is now set on the ledger.
        from nahida_bot.agent.orchestration.models import AgentRun

        ghost = AgentRun(
            run_id="ghost",
            kind=AgentRunKind.SUBAGENT,
            session_id=completed.child_session_id or "",
            parent_run_id=None,
            requester_session_id=completed.requester_session_id,
            task_id=task.task_id,
        )
        await orchestrator._deliver_completion(
            ghost, AgentRunStatus.SUCCEEDED, "child result", ""
        )
        assert len(deliverer.calls) == 1  # no second delivery
        assert completed.delivered_at is not None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_concurrent_delivery_callbacks_send_once(tmp_path) -> None:
    """The database claim must be acquired before the external send."""

    class _BlockingDeliverer:
        def __init__(self) -> None:
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def deliver(self, **kwargs: Any) -> bool:
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return True

    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        deliverer = _BlockingDeliverer()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(_FakeRunner()),
            task_store=SQLiteBackgroundTaskStore(engine),
            completion_deliverer=deliverer,
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="42",
                session_id="telegram:private:42",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(
                SubagentSpec(task="work", notify_policy="silent")
            )
            completed = await orchestrator.wait_for_task(
                task.task_id, timeout_seconds=1
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        ghost = AgentRun(
            run_id="ghost",
            kind=AgentRunKind.SUBAGENT,
            session_id=completed.child_session_id or "",
            parent_run_id=None,
            requester_session_id=completed.requester_session_id,
            task_id=task.task_id,
        )
        first = asyncio.create_task(
            orchestrator._deliver_completion(
                ghost, AgentRunStatus.SUCCEEDED, "child result", ""
            )
        )
        await asyncio.wait_for(deliverer.entered.wait(), timeout=1)
        second = asyncio.create_task(
            orchestrator._deliver_completion(
                ghost, AgentRunStatus.SUCCEEDED, "child result", ""
            )
        )
        await asyncio.wait_for(second, timeout=1)
        deliverer.release.set()
        await asyncio.wait_for(first, timeout=1)

        assert deliverer.calls == 1
        stored = await orchestrator._task_store.get(task.task_id)
        assert stored is not None
        assert stored.delivery_claimed_at is None
        assert stored.delivered_at is not None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_delivery_failure_leaves_task_undelivered(tmp_path) -> None:
    """Issue #41: a failed send leaves delivered_at empty for a later retry."""
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        deliverer = _RecordingDeliverer(succeed=False)
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(_FakeRunner()),
            task_store=SQLiteBackgroundTaskStore(engine),
            completion_deliverer=deliverer,
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="42",
                session_id="telegram:private:42",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(SubagentSpec(task="work"))
            completed = await orchestrator.wait_for_task(
                task.task_id, timeout_seconds=1
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        assert len(deliverer.calls) == 1  # the deliverer was tried
        assert completed.delivered_at is None  # but not marked delivered
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_silent_notify_policy_skips_channel_delivery(tmp_path) -> None:
    """Issue #41: ``notify_policy=silent`` keeps the result queryable but quiet."""
    engine = DatabaseEngine(tmp_path / "tasks.sqlite3")
    await engine.initialize()
    try:
        deliverer = _RecordingDeliverer()
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(_FakeRunner()),
            task_store=SQLiteBackgroundTaskStore(engine),
            completion_deliverer=deliverer,
        )
        session_token = current_session.set(
            SessionContext(
                platform="telegram",
                chat_id="42",
                session_id="telegram:private:42",
            )
        )
        try:
            task = await orchestrator.spawn_subagent(
                SubagentSpec(task="work", notify_policy="silent")
            )
            completed = await orchestrator.wait_for_task(
                task.task_id, timeout_seconds=1
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        # Silent ⇒ no channel delivery, but the task is still terminal and
        # queryable via the ledger.
        assert deliverer.calls == []
        assert completed.delivered_at is None
        assert completed.status == AgentRunStatus.SUCCEEDED
        assert completed.summary == "child result"
    finally:
        await engine.close()
