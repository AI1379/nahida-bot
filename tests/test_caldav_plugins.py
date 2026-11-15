"""Actual HTTP integration using two independent, disposable Radicale servers."""

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from nahida_bot.core.config import Settings
from nahida_bot.core.events import EventBus, EventContext
from nahida_bot.core.tasks import TaskManager
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_plugin_data_repo import (
    SQLitePluginDataRepository,
)
from nahida_bot.plugins.caldav.config import Connection
from nahida_bot.plugins.caldav.transport import CalDAVError, DavClient
from nahida_bot.plugins.manager import PluginManager, PluginState
from nahida_bot_sdk.services import PluginServiceContext
from scripts.caldav_local_server import LocalCalDAV

OWNER = "milky:user:123"
OTHER = "milky:user:456"
START = "2026-11-06T09:00:00+08:00"
END = "2026-11-06T10:00:00+08:00"


class Timers(TaskManager):
    def spawn_interval(self, name, func, *, owner, interval_seconds, initial_delay=0):
        return super().spawn_interval(
            name,
            func,
            owner=owner,
            interval_seconds=interval_seconds,
            initial_delay=3600,
        )


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    directory = tmp_path_factory.mktemp("caldav")
    with (
        LocalCalDAV(directory / "tasks", password="only-task-tests") as tasks,
        LocalCalDAV(directory / "events", password="only-event-tests") as events,
    ):
        yield tasks, events


@pytest.fixture
async def host(tmp_path, servers, monkeypatch):
    tasks, events = servers
    suffix = uuid4().hex
    task_path = tasks.create_collection("tasks-" + suffix, "VTODO")
    event_path = events.create_collection("events-" + suffix, "VEVENT")
    read_path = events.create_collection("readonly-" + suffix, "VEVENT")
    monkeypatch.setenv("TEST_CALDAV_TASK_PASSWORD", tasks.password)
    monkeypatch.setenv("TEST_CALDAV_EVENT_PASSWORD", events.password)
    engine = DatabaseEngine(str(tmp_path / "bot.db"))
    await engine.initialize()
    timers = Timers()
    manager = PluginManager(
        EventBus(
            EventContext(app=MagicMock(), settings=Settings(), logger=MagicMock())
        ),
        plugin_data_repo=SQLitePluginDataRepository(engine),
        task_manager=timers,
    )
    try:
        await manager.discover([Path("nahida_bot/plugins")])
        manager.apply_config(
            "caldav",
            {
                "enabled": True,
                "connections": {
                    "reminders": {
                        "url": tasks.url,
                        "username": tasks.username,
                        "password_env": "TEST_CALDAV_TASK_PASSWORD",
                        "allow_local_http": True,
                    },
                    "icloud-calendar": {
                        "url": events.url,
                        "username": events.username,
                        "password_env": "TEST_CALDAV_EVENT_PASSWORD",
                        "allow_local_http": True,
                    },
                },
                "bindings": {
                    OWNER: {
                        "tasks": {"connection": "reminders", "collection": task_path},
                        "calendar": {
                            "connection": "icloud-calendar",
                            "read_collections": [read_path],
                            "write_collection": event_path,
                        },
                    }
                },
            },
        )
        manager.apply_config(
            "todo", {"enabled": True, "backends": {OWNER: "caldav:tasks.v1"}}
        )
        manager.apply_config(
            "calendar", {"enabled": True, "backends": {OWNER: "caldav:calendar.v1"}}
        )
        manager.apply_config(
            "daily-brief",
            {"enabled": True, "sources": ["todo:tasks.v1", "calendar:events.v1"]},
        )
        for plugin in ("caldav", "todo", "calendar", "daily-brief"):
            await manager.enable(plugin)
            assert manager.get_record(plugin).state == PluginState.ENABLED, (
                manager.get_record(plugin).error_message
            )
        yield SimpleNamespace(
            manager=manager,
            tasks=tasks,
            events=events,
            task_path=task_path,
            event_path=event_path,
            read_path=read_path,
        )
    finally:
        await manager.shutdown_all()
        await timers.shutdown()
        await engine.close()


async def call(host, service, action, arguments=None, actor=OWNER):
    return await host.manager.service_registry.call(
        service,
        action,
        arguments or {},
        PluginServiceContext(actor, node_id="test-desktop"),
        desktop=True,
    )


async def add_task(host, **values):
    return (
        await call(
            host,
            "todo:tasks.v1",
            "create",
            {
                "title": "写报告",
                "planned_on": "2026-11-06",
                "operation_id": "task-create",
                **values,
            },
        )
    )["task"]


async def add_event(host, **values):
    return (
        await call(
            host,
            "calendar:events.v1",
            "create",
            {
                "title": "写报告时间块",
                "start": START,
                "end": END,
                "operation_id": "event-create",
                **values,
            },
        )
    )["event"]


async def test_task_and_calendar_use_different_servers_credentials_and_collections(
    host,
):
    task = await add_task(
        host,
        due_at="2026-11-06T11:00:00+08:00",
        remind_at="2026-11-06T10:45:00+08:00",
        description="中文备注",
    )
    event = await add_event(host, task_id=task["id"])
    assert task["id"].startswith("ext_") and event["id"].startswith("evt_")
    assert event["task_id"] == task["id"]
    assert datetime.fromisoformat(task["due_at"]) == datetime.fromisoformat(
        "2026-11-06T03:00:00+00:00"
    )
    with host.tasks.client() as client:
        response = client.request(
            "REPORT",
            host.task_path,
            content='<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:"><D:prop><C:calendar-data/></D:prop><C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VTODO"/></C:comp-filter></C:filter></C:calendar-query>',
            headers={"Content-Type": "application/xml"},
        )
        assert (
            "VALARM" in response.text
            and "中文备注" in response.text
            and "VEVENT" not in response.text
        )
    assert (
        await call(host, "calendar:events.v1", "list", {"start": START, "end": END})
    )["items"][0]["id"] == event["id"]


async def test_remote_completion_and_native_edits_are_read_fresh(host):
    task = await add_task(host)
    done = (
        await call(
            host,
            "todo:tasks.v1",
            "update",
            {
                "task_id": task["id"],
                "expected_revision": task["revision"],
                "operation_id": "complete",
                "status": "done",
            },
        )
    )["task"]
    assert done["status"] == "done"
    assert (await call(host, "todo:tasks.v1", "today", {"date": "2026-11-06"}))[
        "items"
    ] == []
    with pytest.raises(CalDAVError, match="revision_conflict"):
        await call(
            host,
            "todo:tasks.v1",
            "update",
            {
                "task_id": task["id"],
                "expected_revision": task["revision"],
                "operation_id": "stale",
                "status": "open",
            },
        )
    assert (await add_task(host))["status"] == "done"


async def test_event_crud_retry_and_conflict_detection(host):
    event = await add_event(host)
    assert (await add_event(host))["id"] == event["id"]
    with pytest.raises(CalDAVError, match="calendar_conflict"):
        await add_event(host, operation_id="overlap")
    updated = (
        await call(
            host,
            "calendar:events.v1",
            "update",
            {
                "event_id": event["id"],
                "expected_revision": event["revision"],
                "operation_id": "rename",
                "title": "更新后的标题",
            },
        )
    )["event"]
    assert updated["title"] == "更新后的标题"
    with pytest.raises(CalDAVError, match="revision_conflict"):
        await call(
            host,
            "calendar:events.v1",
            "delete",
            {
                "event_id": event["id"],
                "expected_revision": event["revision"],
                "operation_id": "delete",
            },
        )
    args = {
        "event_id": event["id"],
        "expected_revision": updated["revision"],
        "operation_id": "delete",
    }
    assert (await call(host, "calendar:events.v1", "delete", args))["deleted"]
    assert (await call(host, "calendar:events.v1", "delete", args))["deleted"]


def seed(host, body, *, read_only=False):
    path = (host.read_path if read_only else host.event_path) + uuid4().hex + ".ics"
    data = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Tests//EN\r\nBEGIN:VEVENT\r\nUID:"
        + uuid4().hex
        + "\r\nDTSTAMP:20261105T000000Z\r\n"
        + body
        + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    with host.events.client() as client:
        response = client.put(
            path, content=data.encode(), headers={"Content-Type": "text/calendar"}
        )
        response.raise_for_status()
    return path


async def test_availability_counts_read_only_recurring_events_and_exceptions(host):
    seed(
        host,
        "SUMMARY:每日会议\r\nDTSTART:20261105T010000Z\r\nDTEND:20261105T020000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\nEXDATE:20261107T010000Z",
        read_only=True,
    )
    result = await call(
        host,
        "calendar:events.v1",
        "availability",
        {"start": START, "end": "2026-11-06T12:00:00+08:00", "duration_minutes": 60},
    )
    assert result["free_windows"] == [
        {"start": "2026-11-06T10:00:00+08:00", "end": "2026-11-06T12:00:00+08:00"}
    ]
    event = (
        await call(host, "calendar:events.v1", "list", {"start": START, "end": END})
    )["items"][0]
    with pytest.raises(CalDAVError, match="write_binding"):
        await call(
            host,
            "calendar:events.v1",
            "update",
            {
                "event_id": event["id"],
                "expected_revision": event["revision"],
                "operation_id": "wrong-target",
                "title": "不能改",
            },
        )


async def test_all_day_events_and_transparent_events(host):
    await add_event(host, start="2026-11-06", end="2026-11-07", busy=False)
    result = await call(
        host, "calendar:events.v1", "availability", {"start": START, "end": END}
    )
    assert len(result["free_windows"]) == 1
    await add_event(
        host, operation_id="busy-all-day", start="2026-11-06", end="2026-11-07"
    )
    assert (
        await call(
            host, "calendar:events.v1", "availability", {"start": START, "end": END}
        )
    )["free_windows"] == []


async def test_source_permissions_and_local_fallback_are_explicit(host):
    await add_task(host)
    assert (await call(host, "todo:tasks.v1", "list", actor=OTHER))["items"] == []
    with pytest.raises(ValueError, match="No calendar source"):
        await call(
            host,
            "calendar:events.v1",
            "list",
            {"start": START, "end": END},
            actor=OTHER,
        )
    await host.manager.disable("caldav")
    with pytest.raises(ValueError, match="unavailable"):
        await add_task(host, operation_id="provider-off")
    local = await call(
        host,
        "todo:tasks.v1",
        "create",
        {"title": "本地任务", "operation_id": "local"},
        actor=OTHER,
    )
    assert local["task"]["source"] == "local"


async def test_brief_combines_tasks_and_calendar_with_local_times(host):
    await add_task(host)
    await add_event(host)
    plugin = host.manager.get_record("daily-brief").instance
    body = await plugin.build_body(OWNER, date(2026, 11, 6))
    assert "【待办】" in body and "【日程】" in body
    assert "09:00–10:00" in body and "写报告" in body


async def test_discovery_and_redirect_scope(servers):
    tasks, _ = servers
    path = tasks.create_collection("discover-" + uuid4().hex, "VTODO")
    config = Connection(
        url=tasks.url,
        username=tasks.username,
        password_env="TEST_UNUSED",
        allow_local_http=True,
    )
    async with DavClient(config, tasks.password) as client:
        result = await client.discover()
        assert any(
            item["url"].endswith(path) and "VTODO" in item["components"]
            for item in result
        )
        with pytest.raises(CalDAVError, match="cross_origin"):
            client.url("https://example.invalid/steal")
        with pytest.raises(CalDAVError, match="outside_collection"):
            client.within("/another-user/task.ics", client.url(path))


async def test_calendar_remains_usable_when_task_credentials_are_missing(
    host, monkeypatch
):
    monkeypatch.delenv("TEST_CALDAV_TASK_PASSWORD")
    with pytest.raises(CalDAVError, match="credential_unavailable"):
        await add_task(host)
    event = await add_event(host)
    assert event["title"] == "写报告时间块"


async def test_native_unknown_fields_and_alarms_survive_personal_event_edit(host):
    path = seed(
        host,
        "SUMMARY:原始标题\r\nDTSTART:20261106T010000Z\r\nDTEND:20261106T020000Z\r\nX-NATIVE-METADATA:keep-me\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nDESCRIPTION:原生提醒\r\nEND:VALARM",
    )
    event = (
        await call(host, "calendar:events.v1", "list", {"start": START, "end": END})
    )["items"][0]
    await call(
        host,
        "calendar:events.v1",
        "update",
        {
            "event_id": event["id"],
            "expected_revision": event["revision"],
            "operation_id": "edit",
            "title": "新标题",
        },
    )
    with host.events.client() as client:
        value = client.get(path).text
    assert (
        "X-NATIVE-METADATA:keep-me" in value
        and "BEGIN:VALARM" in value
        and "新标题" in value
    )


async def test_meeting_and_recurring_series_mutations_are_explicitly_rejected(host):
    seed(
        host,
        "SUMMARY:会议邀请\r\nDTSTART:20261106T010000Z\r\nDTEND:20261106T020000Z\r\nATTENDEE:mailto:person@example.invalid",
    )
    event = (
        await call(host, "calendar:events.v1", "list", {"start": START, "end": END})
    )["items"][0]
    with pytest.raises(CalDAVError, match="meeting_invitation"):
        await call(
            host,
            "calendar:events.v1",
            "delete",
            {
                "event_id": event["id"],
                "expected_revision": event["revision"],
                "operation_id": "delete-meeting",
            },
        )


async def test_recurring_calendar_uses_event_timezone_across_dst(host):
    seed(
        host,
        "SUMMARY:纽约会议\r\nDTSTART;TZID=America/New_York:20261030T090000\r\nDTEND;TZID=America/New_York:20261030T100000\r\nRRULE:FREQ=DAILY;COUNT=5",
    )
    result = await call(
        host,
        "calendar:events.v1",
        "availability",
        {"start": "2026-11-02T14:00:00Z", "end": "2026-11-02T15:00:00Z"},
    )
    assert result["free_windows"] == []


async def test_local_and_external_tasks_coexist_without_migration(host):
    plugin = host.manager.get_record("todo").instance
    from nahida_bot.plugins.todo.models import TaskCreate

    local = await plugin.tasks.local.create(
        OWNER, TaskCreate(title="旧的本地事项", operation_id="old")
    )
    external = await add_task(host)
    items = (await call(host, "todo:tasks.v1", "list"))["items"]
    assert {item["id"] for item in items} == {local.id, external["id"]}


async def test_agent_tools_use_current_actor_and_return_retry_key(host, monkeypatch):
    import json
    from nahida_bot.core.context import SessionContext, current_session

    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="123",
            session_id="milky:private:123",
            sender_account_key=OWNER,
        )
    )
    try:
        registry = host.manager.tool_registry
        task = json.loads(
            await registry.get("todo_add").handler(
                title="聊天中添加", planned_on="2026-11-06"
            )
        )["task"]
        assert task["source"] == "caldav"
        event = json.loads(
            await registry.get("calendar_create").handler(
                title="通过工具安排", start=START, end=END
            )
        )
        assert event["event"]["title"] == "通过工具安排"
        assert event["operation_id"]
        monkeypatch.delenv("TEST_CALDAV_EVENT_PASSWORD")
        error = await registry.get("calendar_create").handler(
            title="连接失败", start=START, end=END
        )
        assert "credential_unavailable" in error and "operation_id=" in error
        assert host.events.password not in error
    finally:
        current_session.reset(token)
