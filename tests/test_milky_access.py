"""A closed QQ chat must not reach observers, sends, or scheduled execution."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from nahida_bot.channels.milky.client import MilkyClient
from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.channels.milky.plugin import MilkyPlugin
from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.events import MessageObserved, MessageReceived
from nahida_bot.core.outbound import ChannelAccessDenied, send_channel_message
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.plugins.base import Attachment, OutboundMessage
from nahida_bot.plugins.manifest import PluginManifest
from nahida_bot.scheduler.models import CronJob
from nahida_bot.scheduler.repository import CronRepository
from nahida_bot.scheduler.service import SchedulerService

from .helpers import RecordingMockBotAPI

pytestmark = pytest.mark.asyncio


def _plugin(**overrides):
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=PluginManifest(
            id="milky",
            name="Milky",
            version="0.1.0",
            entrypoint="nahida_bot.channels.milky.plugin:MilkyPlugin",
            config={
                "access_mode": "allowlist",
                "group_context_capture": True,
                "enable_poke_tool": True,
                **overrides,
            },
        ),
    )
    plugin._self_id = 999
    client = AsyncMock(spec=MilkyClient)
    client.send_group_message.return_value = {"message_seq": 22}
    client.send_private_message.return_value = {"message_seq": 11}
    plugin._client = client
    plugin._ensure_runtime_client()
    return plugin, api, client


def _message(scene="friend", peer=10001, text="hello"):
    return {
        "event_type": "message_receive",
        "data": {
            "message_scene": scene,
            "peer_id": peer,
            "sender_id": 10001,
            "message_seq": 123,
            "segments": [{"type": "text", "data": {"text": text}}],
        },
    }


@pytest.mark.parametrize("scene,peer", [("friend", 10001), ("group", 20001)])
async def test_excluded_messages_never_reach_observers_or_downloads(scene, peer):
    plugin, api, client = _plugin()
    event = _message(scene, peer)
    event["data"]["segments"].append(
        {"type": "forward", "data": {"forward_id": "remote-forward"}}
    )
    await plugin.handle_inbound_event(event)
    assert api.published_events == []
    assert not plugin._pending_files
    assert not client.mock_calls


async def test_allowed_group_accepts_members_without_private_access():
    plugin, api, _client = _plugin(allowed_groups=["20001"])
    await plugin.handle_inbound_event(_message("group", 20001))
    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageObserved)
    event = _message("group", 20001)
    event["data"]["segments"].insert(0, {"type": "mention", "data": {"user_id": 999}})
    await plugin.handle_inbound_event(event)
    assert isinstance(api.published_events[-1], MessageReceived)
    count = len(api.published_events)
    await plugin.handle_inbound_event(_message("friend", 10001))
    assert len(api.published_events) == count


async def test_allowed_private_peer_can_use_commands():
    plugin, api, _client = _plugin(allowed_friends=["10001"])
    await plugin.handle_inbound_event(_message(text="/status"))
    assert isinstance(api.published_events[0], MessageReceived)


@pytest.mark.parametrize(
    "event_type,data",
    [
        ("friend_nudge", {"user_id": 10001, "is_self_receive": True}),
        ("group_nudge", {"group_id": 20001, "sender_id": 10001, "receiver_id": 999}),
        (
            "group_message_reaction",
            {
                "group_id": 20001,
                "user_id": 10001,
                "message_seq": 7,
                "face_id": "76",
                "is_add": True,
            },
        ),
        (
            "friend_file_upload",
            {
                "user_id": 10001,
                "file_id": "file-1",
                "file_name": "report.pdf",
                "file_size": 1024,
                "file_hash": "abc",
            },
        ),
        (
            "group_file_upload",
            {
                "group_id": 20001,
                "user_id": 10001,
                "file_id": "file-1",
                "file_name": "report.pdf",
                "file_size": 1024,
            },
        ),
    ],
)
async def test_excluded_side_events_do_not_trigger_or_cache(event_type, data):
    plugin, api, client = _plugin()
    await plugin.handle_inbound_event(
        {"event_type": event_type, "self_id": 999, "data": data}
    )
    assert api.published_events == []
    assert not plugin._pending_files
    assert not client.mock_calls


@pytest.mark.parametrize("target_type", ["private", "group"])
@pytest.mark.parametrize("shared_send", [False, True])
async def test_outbound_denial_precedes_uploads_and_lifecycle(target_type, shared_send):
    plugin, _api, client = _plugin()
    registry = ChannelRegistry()
    registry.register(plugin)
    message = OutboundMessage(
        text="notification",
        extra={"chat_address": f"milky:{target_type}:10001"},
        attachments=[Attachment(type="document", path="never-read.pdf")],
    )
    bus = AsyncMock()
    with pytest.raises(ChannelAccessDenied):
        if shared_send:
            await send_channel_message(
                registry, "milky", "10001", message, event_bus=bus
            )
        else:
            await plugin.send_message("10001", message)
    assert not client.mock_calls
    bus.publish.assert_not_awaited()


async def test_outbound_allowlist_is_rechecked_after_removal():
    plugin, _api, client = _plugin(allowed_friends=["10001"])
    message = OutboundMessage(
        text="hello", extra={"chat_address": "milky:private:10001"}
    )
    assert await plugin.send_message("10001", message) == "11"
    plugin.config.allowed_friends.clear()
    with pytest.raises(ChannelAccessDenied):
        await plugin.send_message("10001", message)
    client.send_private_message.assert_awaited_once()


@pytest.mark.parametrize(
    "target,extra",
    [
        ("milky:group:20001", {}),
        ("group:20001", {}),
        ("20001", {"chat_address": "milky:group:20001"}),
        ("20001", {"message_scene": "group", "peer_id": 20001}),
    ],
)
async def test_shared_send_checks_resolved_group_route(target, extra):
    plugin, _api, client = _plugin(allowed_groups=["20001"])
    registry = ChannelRegistry()
    registry.register(plugin)
    assert (
        await send_channel_message(
            registry, "milky", target, OutboundMessage(text="hello", extra=extra)
        )
        == "22"
    )
    client.send_group_message.assert_awaited_once()
    client.send_private_message.assert_not_awaited()


async def test_empty_allowlist_also_blocks_outbound_pokes():
    plugin, _api, client = _plugin()
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="10001",
            session_id="milky:private:10001",
            chat_address=ChatAddress.parse("milky:private:10001"),
            user_id="10001",
        )
    )
    try:
        assert "not allowed" in await plugin._poke_tool.send()
    finally:
        current_session.reset(token)
    assert not client.mock_calls


@pytest.mark.parametrize("executor_type", ["agent", "script_then_agent"])
@pytest.mark.parametrize("excluded_origin", [False, True])
async def test_cron_suspends_without_running_or_notifying(
    monkeypatch, executor_type, excluded_origin
):
    plugin, _api, client = _plugin(allowed_friends=["10001"] if excluded_origin else [])
    registry = ChannelRegistry()
    registry.register(plugin)
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        repo = CronRepository(engine)
        now = datetime.now(UTC)
        job = CronJob(
            job_id="excluded",
            platform="milky",
            chat_id="10001",
            session_key="milky:private:10001",
            chat_type="private",
            prompt="send update",
            mode="interval",
            fire_at=None,
            interval_seconds=600,
            cron_expression=None,
            max_runs=None,
            run_count=0,
            is_active=True,
            created_at=now.isoformat(),
            next_fire_at=now.isoformat(),
            last_fired_at=None,
            workspace_id=None,
            executor_type=executor_type,
            script_command="must never run",
            created_from_chat_address="milky:private:10002" if excluded_origin else "",
        )
        await repo.insert_job(job)
        claimed = await repo.claim_due_jobs(
            (now + timedelta(seconds=1)).isoformat(), limit=1
        )
        execute = AsyncMock()
        monkeypatch.setattr("nahida_bot.scheduler.service.execute_script", execute)
        runner = AsyncMock()
        service = SchedulerService(repo, runner=runner, channel_registry=registry)
        service._send_error = AsyncMock()
        await service._fire_job(claimed[0])
        stored = await repo.get_job(job.job_id)
        assert stored is not None
        assert not stored.is_active
        assert stored.claimed_at is None
        assert stored.last_error == "chat_access_denied"
        assert stored.run_count == stored.failure_count == 0
        assert stored.last_fired_at is None
        runner.run.assert_not_awaited()
        execute.assert_not_awaited()
        service._send_error.assert_not_awaited()
        assert not client.mock_calls
    finally:
        await engine.close()


async def test_allowed_group_cron_runs_for_member_without_private_access():
    plugin, _api, client = _plugin(allowed_groups=["20001"])
    registry = ChannelRegistry()
    registry.register(plugin)
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        repo = CronRepository(engine)
        now = datetime.now(UTC).isoformat()
        job = CronJob(
            job_id="allowed",
            platform="milky",
            chat_id="20001",
            session_key="milky:group:20001",
            chat_type="group",
            prompt="send update",
            mode="once",
            fire_at=now,
            interval_seconds=None,
            cron_expression=None,
            max_runs=None,
            run_count=0,
            is_active=True,
            created_at=now,
            next_fire_at=now,
            last_fired_at=None,
            workspace_id=None,
            created_by_user_id="10001",
            created_from_chat_address="milky:group:20001",
        )
        await repo.insert_job(job)
        runner = AsyncMock()
        runner.run.return_value = AgentRunResult(final_response="done")
        service = SchedulerService(repo, runner=runner, channel_registry=registry)
        await service._fire_job(job)
        stored = await repo.get_job(job.job_id)
        assert stored is not None
        assert stored.run_count == 1
        assert stored.failure_count == 0
        assert stored.last_error is None
        runner.run.assert_awaited_once()
        client.send_group_message.assert_awaited_once()
        client.send_private_message.assert_not_awaited()
    finally:
        await engine.close()
