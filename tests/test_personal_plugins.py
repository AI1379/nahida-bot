"""Personal plugin integration against real host lifecycle and SQLite storage."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nahida_bot.core.config import Settings
from nahida_bot.core.events import EventBus, EventContext
from nahida_bot.core.tasks import TaskManager
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_plugin_data_repo import (
    SQLitePluginDataRepository,
)
from nahida_bot.plugins.daily_brief.models import BriefPreferences
from nahida_bot.plugins.manager import PluginManager, PluginState
from nahida_bot.plugins.services import ServiceEntry
from nahida_bot_sdk.desktop import DesktopSurfaceContext
from nahida_bot_sdk.services import PluginServiceContext

OWNER = "milky:user:123"
OTHER = "milky:user:456"
CONTEXT = PluginServiceContext(OWNER, node_id="pc")


class PausedTimers(TaskManager):
    def spawn_interval(self, name, func, *, owner, interval_seconds, initial_delay=0):
        return super().spawn_interval(
            name,
            func,
            owner=owner,
            interval_seconds=interval_seconds,
            initial_delay=3600,
        )


@pytest.fixture
async def host(tmp_path):
    db = DatabaseEngine(str(tmp_path / "plugins.db"))
    await db.initialize()
    tasks = PausedTimers()
    sender = AsyncMock(return_value="message-1")
    channels = SimpleNamespace(
        get=lambda name: (
            SimpleNamespace(send_message=sender) if name == "milky" else None
        )
    )
    manager = PluginManager(
        EventBus(
            EventContext(app=MagicMock(), settings=Settings(), logger=MagicMock())
        ),
        plugin_data_repo=SQLitePluginDataRepository(db),
        task_manager=tasks,
        channel_registry=channels,
    )
    try:
        await manager.discover([Path("nahida_bot/plugins")])
        for name in ("todo", "notification-router", "daily-brief"):
            await manager.enable(name)
            assert manager.get_record(name).state == PluginState.ENABLED, (
                manager.get_record(name).error_message
            )
        state = {"endpoints": [], "deliveries": [], "timeout": False}

        async def desktop(operation, arguments, context):
            if operation == "endpoints":
                return {"endpoints": state["endpoints"]}
            state["deliveries"].append(arguments)
            if state["timeout"]:
                raise TimeoutError("lost receipt")
            return {"ok": True, "receipt": {"status": "accepted"}}

        manager.service_registry.register(ServiceEntry("host.desktop", "v1", desktop))
        yield SimpleNamespace(
            manager=manager, db=db, tasks=tasks, sender=sender, state=state
        )
    finally:
        await manager.shutdown_all()
        await tasks.shutdown()
        await db.close()


async def call(host, service, operation, arguments=None, context=CONTEXT):
    return await host.manager.service_registry.call(
        service, operation, arguments or {}, context, desktop=bool(context.node_id)
    )


async def create_task(host, **overrides):
    result = await call(
        host,
        "todo:tasks.v1",
        "create",
        {
            "title": "写设计文档",
            "planned_on": "2026-10-30",
            "operation_id": "create-1",
            **overrides,
        },
    )
    return result["task"]


async def submit(host, key="brief", **overrides):
    request = {
        "key": key,
        "title": "今天",
        "body": "今天的任务",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        **overrides,
    }
    return await call(
        host,
        "notification-router:notifications.v1",
        "submit",
        request,
        PluginServiceContext(OWNER, caller_plugin_id="daily-brief"),
    )


def instance(host, name):
    return host.manager.get_record(name).instance


async def test_tasks_are_scoped_idempotent_and_revision_checked(host):
    first, repeated = await asyncio.gather(create_task(host), create_task(host))
    assert first["id"] == repeated["id"]
    assert (
        await call(
            host,
            "todo:tasks.v1",
            "list",
            context=PluginServiceContext(OTHER, node_id="other"),
        )
    )["items"] == []
    update = {
        "task_id": first["id"],
        "expected_revision": 1,
        "operation_id": "done-1",
        "status": "done",
    }
    completed = await call(host, "todo:tasks.v1", "update", update)
    assert completed["task"]["revision"] == 2
    assert await call(host, "todo:tasks.v1", "update", update) == completed
    assert (await create_task(host))["status"] == "done"
    with pytest.raises(ValueError, match="Operation ID"):
        await call(host, "todo:tasks.v1", "update", {**update, "status": "open"})
    with pytest.raises(ValueError, match="changed"):
        await call(
            host,
            "todo:tasks.v1",
            "update",
            {**update, "operation_id": "late-1", "status": "open"},
        )
    with pytest.raises(ValueError):
        await call(
            host,
            "todo:tasks.v1",
            "create",
            {"owner": OTHER, "title": "spoof", "operation_id": "spoof"},
        )


async def test_today_distinguishes_deadlines_from_unscheduled_tasks(host):
    await create_task(host)
    await create_task(
        host, operation_id="overdue", planned_on=None, due_date="2026-10-29"
    )
    await create_task(host, operation_id="inbox", planned_on=None)
    items = (await call(host, "todo:tasks.v1", "today", {"date": "2026-10-30"}))[
        "items"
    ]
    assert len(items) == 2
    assert items[0]["due_date"] == "2026-10-29"


async def test_brief_is_fixed_snapshot_and_survives_reenable(host):
    task = await create_task(host)
    preferences = BriefPreferences(enabled=True)
    now = datetime(2026, 10, 30, 0, 5, tzinfo=UTC)
    brief_plugin = instance(host, "daily-brief")
    await brief_plugin.run_due(OWNER, preferences, now)
    snapshot = await brief_plugin.api.plugin_data_get(
        brief_plugin.brief_key(OWNER, now.date())
    )
    assert "写设计文档" in snapshot["body"]
    await call(
        host,
        "todo:tasks.v1",
        "update",
        {
            "task_id": task["id"],
            "expected_revision": 1,
            "operation_id": "done",
            "status": "done",
        },
    )
    await host.manager.disable("daily-brief")
    await host.manager.enable("daily-brief")
    brief_plugin = instance(host, "daily-brief")
    await brief_plugin.run_due(OWNER, preferences, now + timedelta(minutes=2))
    assert (
        await brief_plugin.api.plugin_data_get(
            brief_plugin.brief_key(OWNER, now.date())
        )
        == snapshot
    )
    assert (
        len((await call(host, "notification-router:notifications.v1", "list"))["items"])
        == 1
    )
    assert (await call(host, "todo:tasks.v1", "today", {"date": "2026-10-30"}))[
        "items"
    ] == []


async def test_brief_skips_past_window_and_records_provider_failure(host):
    brief = instance(host, "daily-brief")
    await brief.run_due(
        OWNER, BriefPreferences(enabled=True), datetime(2026, 10, 30, 6, tzinfo=UTC)
    )
    assert await brief.api.plugin_data_list("brief:") == {}
    await host.manager.disable("todo")
    body = await brief.build_body(OWNER, datetime(2026, 10, 30).date())
    assert "暂时无法读取" in body
    assert "今天没有安排" not in body


async def test_desktop_delivery_and_duplicate_workers_do_not_broadcast(host):
    host.state["endpoints"] = [
        {
            "node_id": "pc",
            "fresh": True,
            "state": {"locked": False, "activity": "active"},
        }
    ]
    await call(
        host,
        "notification-router:notifications.v1",
        "configure",
        {"fallback_enabled": True},
    )
    await submit(host)
    router = instance(host, "notification-router")
    await asyncio.gather(router.tick(), router.tick())
    assert len(host.state["deliveries"]) == 1
    host.sender.assert_not_called()
    assert (await call(host, "notification-router:notifications.v1", "list"))["items"][
        0
    ]["status"] == "accepted"


@pytest.mark.parametrize(
    "state",
    [
        {"locked": True, "activity": "active"},
        {"locked": False, "activity": "idle"},
        {},
    ],
)
async def test_locked_idle_unknown_route_only_to_owners_private_account(host, state):
    host.state["endpoints"] = [{"node_id": "pc", "fresh": bool(state), "state": state}]
    await call(
        host,
        "notification-router:notifications.v1",
        "configure",
        {"fallback_enabled": True},
    )
    await submit(host)
    await instance(host, "notification-router").tick()
    host.sender.assert_awaited_once()
    assert host.sender.call_args.args[0] == "123"
    assert host.sender.call_args.args[1].extra["chat_address"] == "milky:private:123"


async def test_dnd_is_not_bypassed_through_fallback(host):
    host.state["endpoints"] = [
        {
            "node_id": "pc",
            "fresh": True,
            "state": {"locked": False, "activity": "active", "do_not_disturb": True},
        }
    ]
    await call(
        host,
        "notification-router:notifications.v1",
        "configure",
        {"fallback_enabled": True},
    )
    await submit(host)
    await instance(host, "notification-router").tick()
    host.sender.assert_not_called()
    assert (await call(host, "notification-router:notifications.v1", "list"))["items"][
        0
    ]["reason"] == "do_not_disturb"


async def test_unknown_desktop_result_never_falls_back(host):
    host.state["endpoints"] = [
        {
            "node_id": "pc",
            "fresh": True,
            "state": {"locked": False, "activity": "active"},
        }
    ]
    host.state["timeout"] = True
    await call(
        host,
        "notification-router:notifications.v1",
        "configure",
        {"fallback_enabled": True},
    )
    await submit(host)
    router = instance(host, "notification-router")
    await router.tick()
    host.state["endpoints"] = []
    records = await router.api.plugin_data_list("notification:")
    key, record = next(iter(records.items()))
    await router.delivery._process(
        key, record, datetime.now(UTC) + timedelta(seconds=40)
    )
    host.sender.assert_not_called()
    assert len(host.state["deliveries"]) == 1


async def test_disabling_removes_services_and_timers_but_preserves_tasks(host):
    await create_task(host)
    await host.manager.disable("todo")
    with pytest.raises(ValueError, match="unavailable"):
        await call(host, "todo:tasks.v1", "list")
    assert all(task.status != "running" for task in host.tasks.list_tasks(owner="todo"))
    await host.manager.enable("todo")
    assert len((await call(host, "todo:tasks.v1", "list"))["items"]) == 1


async def test_surface_cannot_derive_identity_from_client_metadata(host):
    await create_task(host, planned_on=datetime.now().date().isoformat())
    plugin = instance(host, "todo")
    assert (
        await plugin.surface(
            DesktopSurfaceContext(node_id="pc", metadata={"actor_account_key": OWNER})
        )
        is None
    )
    view = await plugin.surface(
        DesktopSurfaceContext(node_id="pc", actor_account_key=OWNER)
    )
    assert len(view.items) == 1


async def test_desktop_cannot_call_transport_or_submit_arbitrary_notifications(host):
    with pytest.raises(PermissionError):
        await call(host, "host.desktop:v1", "deliver", {})
    with pytest.raises(PermissionError):
        await call(host, "notification-router:notifications.v1", "submit", {})
