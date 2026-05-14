"""Tests for cron scheduler persistence and execution semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from nahida_bot.agent.context import ContextBuilder, ContextMessage
from nahida_bot.agent.loop import AgentRunResult, LoopEvent
from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
from nahida_bot.agent.providers.base import (
    ChatProvider,
    ProviderResponse,
    ToolDefinition,
)
from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
from nahida_bot.agent.providers.router import ModelRouter
from nahida_bot.agent.tokenization import Tokenizer
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.scheduler.models import CronJob, SchedulerConfig
from nahida_bot.scheduler.repository import CronRepository
from nahida_bot.scheduler.service import SchedulerService


async def _repo() -> tuple[DatabaseEngine, CronRepository]:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    return engine, CronRepository(engine)


def _job(*, job_id: str = "job1", next_fire_at: str | None = None) -> CronJob:
    now = datetime.now(UTC).isoformat()
    return CronJob(
        job_id=job_id,
        platform="telegram",
        chat_id="c1",
        session_key="telegram:c1",
        prompt="say hi",
        mode="once",
        fire_at=next_fire_at or now,
        interval_seconds=None,
        cron_expression=None,
        max_runs=None,
        run_count=0,
        is_active=True,
        created_at=now,
        next_fire_at=next_fire_at or now,
        last_fired_at=None,
        workspace_id=None,
    )


class _Agent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.last_kwargs: dict[str, object] = {}

    async def run(self, **kwargs: object) -> object:
        return await self._collect(self.run_stream(**kwargs))

    async def run_stream(self, **kwargs: object) -> Any:
        self.calls += 1
        self.last_kwargs = kwargs
        if self.fail:
            raise RuntimeError("boom")
        yield LoopEvent(type="done", final_response="done")

    @staticmethod
    async def _collect(stream: Any) -> AgentRunResult:
        async for event in stream:
            if event.type == "done":
                return AgentRunResult(
                    final_response=event.final_response or "",
                    assistant_messages=list(event.assistant_messages or []),
                    tool_messages=list(event.tool_messages or []),
                    steps=event.steps,
                    trace_id=event.trace_id,
                    error=event.error,
                )
        return AgentRunResult(final_response="")


class _DreamProvider(ChatProvider):
    name = "dream"
    api_family = "openai-completions"

    def __init__(self) -> None:
        self.calls = 0
        self.model: str | None = None

    @property
    def tokenizer(self) -> Tokenizer | None:
        return None

    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        self.calls += 1
        self.model = model
        return ProviderResponse(
            content="""{
              "add": [
                {
                  "kind": "preference",
                  "title": "语言偏好",
                  "content": "用户偏好用中文讨论项目实现。",
                  "confidence": 0.9,
                  "importance": 0.8,
                  "evidence": "用户要求用中文继续讨论。"
                }
              ],
              "archive": []
            }"""
        )


class _Channel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, OutboundMessage]] = []

    async def send_message(self, chat_id: str, message: OutboundMessage) -> str:
        self.sent.append((chat_id, message))
        return "m1"


class _Channels:
    def __init__(self, channel: _Channel) -> None:
        self.channel = channel

    def get(self, platform: str) -> _Channel | None:
        if platform == "telegram":
            return self.channel
        return None


def _make_service(
    engine: DatabaseEngine,
    repo: CronRepository,
    *,
    agent: Any = None,
    channel: _Channel | None = None,
    config: SchedulerConfig | None = None,
) -> SchedulerService:
    runner = SessionRunner(agent_loop=agent)
    return SchedulerService(
        repo,
        runner=runner,
        channel_registry=cast(Any, _Channels(channel)) if channel else None,
        config=config,
    )


@pytest.mark.asyncio
async def test_claim_due_jobs_is_atomic_and_hides_claimed_jobs() -> None:
    engine, repo = await _repo()
    try:
        due_at = datetime.now(UTC).isoformat()
        await repo.insert_job(_job(next_fire_at=due_at))

        first = await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=10)
        second = await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=10)

        assert [j.job_id for j in first] == ["job1"]
        assert second == []
        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.claimed_at is not None
        assert stored.run_count == 0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_fire_job_completes_once_job_after_success() -> None:
    engine, repo = await _repo()
    try:
        await repo.insert_job(_job())
        claimed = await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1)
        channel = _Channel()
        service = _make_service(
            engine,
            repo,
            agent=_Agent(),
            channel=channel,
            config=SchedulerConfig(job_timeout_seconds=1),
        )

        await service._fire_job(claimed[0])

        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.is_active is False
        assert stored.claimed_at is None
        assert stored.run_count == 1
        assert channel.sent[0][1].text == "done"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_fire_job_failure_releases_claim_for_retry_without_counting_run() -> None:
    engine, repo = await _repo()
    try:
        await repo.insert_job(_job())
        claimed = await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1)
        channel = _Channel()
        service = _make_service(
            engine,
            repo,
            agent=_Agent(fail=True),
            channel=channel,
            config=SchedulerConfig(
                job_timeout_seconds=1,
                failure_retry_seconds=60,
                max_consecutive_failures=3,
            ),
        )

        await service._fire_job(claimed[0])

        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.is_active is True
        assert stored.claimed_at is None
        assert stored.run_count == 0
        assert stored.failure_count == 1
        assert stored.last_error is not None
        assert "RuntimeError" in stored.last_error
        assert "boom" in stored.last_error
        assert "[Scheduler] Scheduled task failed." == channel.sent[0][1].text
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_scheduler_runs_memory_dreaming_as_internal_periodic_job() -> None:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        memory = SQLiteMemoryStore(engine)
        await memory.ensure_session("s1", workspace_id="default")
        await memory.append_turn(
            "s1",
            ConversationTurn(
                role="user",
                content="我们之后继续用中文讨论这个项目的实现。",
                source="user_input",
            ),
        )
        await memory.append_turn(
            "s1",
            ConversationTurn(
                role="assistant",
                content="好的，之后默认用中文讨论。",
                source="agent_response",
            ),
        )
        provider = _DreamProvider()
        pm = ProviderManager(
            [
                ProviderSlot(
                    id="dream",
                    provider=provider,
                    context_builder=ContextBuilder(),
                    default_model="dream-model",
                    available_models=["dream-model"],
                )
            ],
            default_id="dream",
        )
        router = ModelRouter(pm)
        runner = SessionRunner(
            memory_store=memory,
            provider_manager=pm,
            model_router=router,
        )
        service = SchedulerService(
            CronRepository(engine),
            runner=runner,
            config=SchedulerConfig(
                memory_dreaming_enabled=True,
                memory_dreaming_recent_turn_limit=10,
                memory_dreaming_model="dream-model",
            ),
        )

        applied = await service._run_memory_dreaming_once()
        repeated = await service._run_memory_dreaming_once()
        results = await memory.search_items("中文讨论")
        meta = await memory.get_session_meta("s1")

        assert applied == 1
        assert repeated == 0
        assert provider.calls == 1
        assert provider.model == "dream-model"
        assert results
        assert int(meta["memory_dream_last_turn_id"]) >= 2
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_update_and_delete_job() -> None:
    engine, repo = await _repo()
    try:
        service = _make_service(engine, repo)
        fire_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        job = await service.create_job(
            platform="telegram",
            chat_id="c1",
            prompt="old",
            mode="once",
            fire_at=fire_at,
        )

        updated = await service.update_job(
            job.job_id,
            prompt="new",
            mode="interval",
            interval_seconds=120,
            max_runs=2,
        )
        assert updated.prompt == "new"
        assert updated.mode == "interval"
        assert updated.interval_seconds == 120
        assert updated.max_runs == 2

        assert await service.delete_job(job.job_id) is True
        assert await service.get_job(job.job_id) is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_stale_claims_recovered_on_start() -> None:
    engine, repo = await _repo()
    try:
        # Insert a job and simulate a stale claim
        await repo.insert_job(_job())
        await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1)

        # Verify the claim was set in the DB
        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.claimed_at is not None

        # Starting the service should release the stale claim
        service = _make_service(engine, repo)
        await service.start()
        await service.stop()

        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.claimed_at is None
        assert stored.is_active is True
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cron_mode_creates_and_fires() -> None:
    engine, repo = await _repo()
    try:
        service = _make_service(engine, repo)
        # "every minute" expression — next fire is within a minute
        job = await service.create_job(
            platform="telegram",
            chat_id="c1",
            prompt="cron test",
            mode="cron",
            cron_expression="* * * * *",
            max_runs=2,
        )
        assert job.mode == "cron"
        assert job.cron_expression == "* * * * *"
        assert job.max_runs == 2
        assert job.is_active is True
        # next_fire_at should be in the future
        next_dt = datetime.fromisoformat(job.next_fire_at)
        assert next_dt > datetime.now(UTC) - timedelta(seconds=1)

        # Simulate a fire: claim, fire, complete — should schedule next
        claimed = await repo.claim_due_jobs(
            (datetime.now(UTC) + timedelta(minutes=1)).isoformat(), limit=1
        )
        assert len(claimed) == 1
        channel = _Channel()
        svc = _make_service(
            engine,
            repo,
            agent=_Agent(),
            channel=channel,
            config=SchedulerConfig(job_timeout_seconds=5),
        )
        await svc._fire_job(claimed[0])
        stored = await repo.get_job(job.job_id)
        assert stored is not None
        assert stored.run_count == 1
        assert stored.is_active is True  # still active (max_runs=2, only 1 done)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cron_mode_update_and_invalid_expression() -> None:
    engine, repo = await _repo()
    try:
        service = _make_service(engine, repo)
        job = await service.create_job(
            platform="telegram",
            chat_id="c1",
            prompt="cron test",
            mode="cron",
            cron_expression="0 9 * * *",
        )

        # Update to a different cron expression
        updated = await service.update_job(
            job.job_id,
            cron_expression="0 12 * * 1-5",
        )
        assert updated.cron_expression == "0 12 * * 1-5"
        assert updated.mode == "cron"

        # Invalid cron expression should raise
        with pytest.raises(ValueError, match="Invalid cron expression"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="bad cron",
                mode="cron",
                cron_expression="not-a-cron",
            )

        # Missing cron_expression should raise
        with pytest.raises(ValueError, match="cron_expression is required"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="no expr",
                mode="cron",
            )

        with pytest.raises(ValueError, match="standard 5-field syntax"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="six fields",
                mode="cron",
                cron_expression="* * * * * *",
            )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cron_mode_respects_min_interval_config() -> None:
    engine, repo = await _repo()
    try:
        service = _make_service(
            engine,
            repo,
            config=SchedulerConfig(min_interval_seconds=120),
        )

        with pytest.raises(ValueError, match="interval must be >= 120 seconds"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="too often",
                mode="cron",
                cron_expression="* * * * *",
            )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_quota_enforced_atomically() -> None:
    engine, repo = await _repo()
    try:
        config = SchedulerConfig(max_jobs_per_chat=2)
        service = _make_service(engine, repo, config=config)

        fire_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        await service.create_job(
            platform="telegram", chat_id="c1", prompt="j1", mode="once", fire_at=fire_at
        )
        await service.create_job(
            platform="telegram", chat_id="c1", prompt="j2", mode="once", fire_at=fire_at
        )

        with pytest.raises(ValueError, match="limit reached"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="j3",
                mode="once",
                fire_at=fire_at,
            )
    finally:
        await engine.close()
