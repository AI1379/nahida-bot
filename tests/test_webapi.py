"""Tests for the WebAPI layer."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import nahida_bot.gateway.app as gateway_app_module
from nahida_bot.core.config import (
    Settings,
    WebAPIConfigModel,
    WebUIAuthConfigModel,
    WebUIConfigModel,
)
from nahida_bot.gateway.app import WebAPIApp


def _make_mock_app(
    *,
    auth_token: str = "",
    webui_password: str = "",
    webui_password_hash: str = "",
    login_rate_per_minute: int = 5,
    bind_session_to_ip: bool = True,
    is_started: bool = True,
    debug: bool = True,
) -> MagicMock:
    settings = Settings(
        app_name="Test WebAPI",
        debug=debug,
        db_path=":memory:",
        plugin_paths=[],
        discover_builtin_channels=False,
        webapi=WebAPIConfigModel(auth_token=auth_token),
        webui=WebUIConfigModel(
            auth=WebUIAuthConfigModel(
                admin_password=webui_password,
                admin_password_hash=webui_password_hash,
                login_rate_per_minute=login_rate_per_minute,
                bind_session_to_ip=bind_session_to_ip,
            ),
        ),
    )
    mock = MagicMock(
        spec=[
            "settings",
            "version",
            "is_started",
            "is_initialized",
            "memory_store",
            "message_router",
            "channel_registry",
            "scheduler_service",
            "webapi_service",
            "workspace_manager",
            "plugin_manager",
            "_provider_manager",
            "_usage_ledger",
            "started_at",
            "request_shutdown",
        ]
    )
    mock.settings = settings
    mock.version = "0.1-test"
    mock.is_started = is_started
    mock.is_initialized = True
    mock.memory_store = None
    mock.message_router = None
    mock.channel_registry = MagicMock()
    mock.channel_registry.get.return_value = None
    mock.channel_registry._channels = {}
    mock.scheduler_service = None
    mock.webapi_service = None
    mock.workspace_manager = None
    mock.plugin_manager = None
    mock._provider_manager = None
    mock._usage_ledger = None
    mock.started_at = None
    mock.request_shutdown = MagicMock()
    return mock


@pytest.fixture
def webapi_no_auth() -> WebAPIApp:
    return WebAPIApp(
        application=_make_mock_app(auth_token=""),
        host="127.0.0.1",
        port=6185,
    )


@pytest.fixture
def webapi_with_auth() -> WebAPIApp:
    return WebAPIApp(
        application=_make_mock_app(auth_token="test-secret"),
        host="127.0.0.1",
        port=6185,
        auth_token="test-secret",
    )


@pytest.fixture
def webapi_with_webui_password() -> WebAPIApp:
    return WebAPIApp(
        application=_make_mock_app(webui_password="admin-pass"),
        host="127.0.0.1",
        port=6185,
    )


@pytest.fixture
async def client_no_auth(webapi_no_auth) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=webapi_no_auth.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def client_with_auth(webapi_with_auth) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=webapi_with_auth.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def client_with_webui_password(
    webapi_with_webui_password,
) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=webapi_with_webui_password.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# -- Health ---------------------------------------------------------------


async def test_health_no_auth_required(client_with_auth: AsyncClient) -> None:
    resp = await client_with_auth.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["app_name"] == "Test WebAPI"
    assert data["started"] is True


async def test_health_degraded_when_not_started() -> None:
    app = WebAPIApp(
        application=_make_mock_app(is_started=False, auth_token=""),
        host="127.0.0.1",
        port=6185,
    )
    transport = ASGITransport(app=app.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
    assert resp.json()["started"] is False


# -- Auth -----------------------------------------------------------------


async def test_sessions_requires_auth(client_with_auth: AsyncClient) -> None:
    resp = await client_with_auth.get("/api/sessions")
    assert resp.status_code == 401


async def test_sessions_bearer_auth(client_with_auth: AsyncClient) -> None:
    resp = await client_with_auth.get(
        "/api/sessions",
        headers={"Authorization": "Bearer test-secret"},
    )
    # 503 because memory_store is None, but auth passed
    assert resp.status_code == 503


async def test_sessions_query_param_auth(client_with_auth: AsyncClient) -> None:
    resp = await client_with_auth.get("/api/sessions?token=test-secret")
    assert resp.status_code == 503  # auth passed, memory_store not initialized


async def test_webui_password_auth_flow(
    client_with_webui_password: AsyncClient,
) -> None:
    unauthenticated = await client_with_webui_password.get("/api/status")
    assert unauthenticated.status_code == 401

    bad_login = await client_with_webui_password.post(
        "/api/auth/login",
        json={"password": "wrong"},
    )
    assert bad_login.status_code == 401

    login = await client_with_webui_password.post(
        "/api/auth/login",
        json={"password": "admin-pass"},
    )
    assert login.status_code == 200
    assert login.json()["authenticated"] is True

    session = await client_with_webui_password.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True

    authed = await client_with_webui_password.get("/api/status")
    assert authed.status_code == 200

    logout = await client_with_webui_password.post("/api/auth/logout")
    assert logout.status_code == 200

    after_logout = await client_with_webui_password.get("/api/status")
    assert after_logout.status_code == 401


async def test_webui_login_is_rate_limited() -> None:
    webapi = WebAPIApp(
        application=_make_mock_app(
            webui_password="admin-pass",
            login_rate_per_minute=2,
        ),
        host="127.0.0.1",
        port=6185,
    )
    transport = ASGITransport(app=webapi.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/auth/login", json={"password": "wrong"})
        second = await client.post("/api/auth/login", json={"password": "wrong"})
        third = await client.post("/api/auth/login", json={"password": "admin-pass"})

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429


async def test_webui_password_hash_auth_flow() -> None:
    from nahida_bot.gateway.services.webui_auth import hash_password_pbkdf2

    webapi = WebAPIApp(
        application=_make_mock_app(
            webui_password_hash=hash_password_pbkdf2(
                "admin-pass",
                salt="test-salt",
                iterations=1_000,
            ),
        ),
        host="127.0.0.1",
        port=6185,
    )
    transport = ASGITransport(app=webapi.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"password": "admin-pass"},
        )
        authed = await client.get("/api/status")

    assert login.status_code == 200
    assert authed.status_code == 200


async def test_webui_session_is_bound_to_client_ip() -> None:
    webapi = WebAPIApp(
        application=_make_mock_app(webui_password="admin-pass"),
        host="127.0.0.1",
        port=6185,
    )
    first_transport = ASGITransport(
        app=webapi.fastapi_app,
        client=("198.51.100.10", 12345),
    )
    second_transport = ASGITransport(
        app=webapi.fastapi_app,
        client=("198.51.100.11", 12345),
    )

    async with AsyncClient(
        transport=first_transport,
        base_url="http://test",
    ) as first_client:
        login = await first_client.post(
            "/api/auth/login",
            json={"password": "admin-pass"},
        )
        cookies = first_client.cookies

    async with AsyncClient(
        transport=second_transport,
        base_url="http://test",
        cookies=cookies,
    ) as second_client:
        resp = await second_client.get("/api/status")

    assert login.status_code == 200
    assert resp.status_code == 401


async def test_bearer_token_still_works_when_webui_password_is_configured() -> None:
    webapi = WebAPIApp(
        application=_make_mock_app(
            auth_token="script-token",
            webui_password="admin-pass",
        ),
        host="127.0.0.1",
        port=6185,
        auth_token="script-token",
    )
    transport = ASGITransport(app=webapi.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/status",
            headers={"Authorization": "Bearer script-token"},
        )

    assert resp.status_code == 200


async def test_send_requires_auth(client_with_auth: AsyncClient) -> None:
    resp = await client_with_auth.post(
        "/api/send",
        json={"platform": "telegram", "chat_id": "123", "text": "hi"},
    )
    assert resp.status_code == 401


async def test_cron_list_requires_auth(client_with_auth: AsyncClient) -> None:
    resp = await client_with_auth.get("/api/cron?platform=t&chat_id=1")
    assert resp.status_code == 401


async def test_cron_create_requires_auth(client_with_auth: AsyncClient) -> None:
    resp = await client_with_auth.post(
        "/api/cron",
        json={"platform": "t", "chat_id": "1", "prompt": "hi", "mode": "once"},
    )
    assert resp.status_code == 401


async def test_no_auth_means_open(client_no_auth: AsyncClient) -> None:
    resp = await client_no_auth.get("/api/sessions")
    # 503 = auth passed (no token needed), but memory_store not initialized
    assert resp.status_code == 503


# -- WebUI ----------------------------------------------------------------


async def test_webui_bootstrap_reports_root_base(
    client_with_auth: AsyncClient,
) -> None:
    resp = await client_with_auth.get("/api/webui/bootstrap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["api_base"] == "/api"
    assert data["webui_base"] == "/"
    assert data["auth"]["required"] is True
    assert data["auth"]["mode"] == "bearer"


async def test_webui_bootstrap_reports_password_mode(
    client_with_webui_password: AsyncClient,
) -> None:
    resp = await client_with_webui_password.get("/api/webui/bootstrap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth"]["required"] is True
    assert data["auth"]["mode"] == "password"
    assert data["auth"]["session_cookie"] is True


async def test_webui_root_mount_serves_spa_without_masking_api(
    tmp_path,
    monkeypatch,
) -> None:
    webui_dist = tmp_path / "webui-dist"
    webui_dist.mkdir()
    (webui_dist / "index.html").write_text(
        "<!doctype html><html><body>root webui</body></html>",
        encoding="utf-8",
    )
    (webui_dist / "favicon.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        encoding="utf-8",
    )

    monkeypatch.setattr(gateway_app_module, "_WEBUI_SEARCH_PATHS", [webui_dist])
    webapi = WebAPIApp(application=_make_mock_app(), host="127.0.0.1", port=6185)
    transport = ASGITransport(app=webapi.fastapi_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root = await client.get("/")
        assert root.status_code == 200
        assert "root webui" in root.text

        spa = await client.get("/config")
        assert spa.status_code == 200
        assert "root webui" in spa.text

        asset = await client.get("/favicon.svg")
        assert asset.status_code == 200
        assert asset.text.startswith("<svg")

        missing_api = await client.get("/api/not-found")
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"].startswith("application/json")


# -- Sessions ------------------------------------------------------------


async def test_sessions_returns_503_when_no_memory(client_no_auth: AsyncClient) -> None:
    resp = await client_no_auth.get("/api/sessions")
    assert resp.status_code == 503
    assert "Memory store" in resp.json()["detail"]


async def test_session_history_returns_503_when_no_memory(
    client_no_auth: AsyncClient,
) -> None:
    resp = await client_no_auth.get("/api/sessions/test:123")
    assert resp.status_code == 503


async def test_sessions_returns_list(client_no_auth: AsyncClient) -> None:
    from nahida_bot.agent.memory.models import SessionSummary

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_memory = AsyncMock()
    mock_memory.list_sessions.return_value = [
        SessionSummary(
            session_id="telegram:private:123",
            workspace_id=None,
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            metadata={},
        )
    ]
    mock_app.memory_store = mock_memory

    resp = await client_no_auth.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == "telegram:private:123"
    assert data["sessions"][0]["session_key_kind"] == "typed"
    assert data["sessions"][0]["turn_count"] == 5


# -- Send Message --------------------------------------------------------


async def test_send_returns_503_when_no_router(client_no_auth: AsyncClient) -> None:
    resp = await client_no_auth.post(
        "/api/send",
        json={"target": "telegram:private:123", "text": "hello"},
    )
    assert resp.status_code == 503


async def test_send_returns_404_when_channel_not_found(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "telegram:private:123"
    mock_app.message_router = mock_router

    resp = await client_no_auth.post(
        "/api/send",
        json={"target": "telegram:private:123", "text": "hello"},
    )
    assert resp.status_code == 404
    assert "telegram" in resp.json()["detail"]


async def test_send_success(client_no_auth: AsyncClient) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]

    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "telegram:private:123:abc"
    mock_app.message_router = mock_router

    mock_channel = AsyncMock()
    mock_channel.send_message = AsyncMock(return_value="msg-456")
    mock_app.channel_registry.get.return_value = mock_channel

    resp = await client_no_auth.post(
        "/api/send",
        json={"target": "telegram:private:123", "text": "hello"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "sent"
    assert data["session_id"] == "telegram:private:123:abc"
    mock_channel.send_message.assert_called_once()
    sent_target, sent_message = mock_channel.send_message.call_args.args
    assert sent_target == "123"
    assert sent_message.extra["chat_address"] == "telegram:private:123"


async def test_send_with_typed_target_resolves_typed_session(
    client_no_auth: AsyncClient,
) -> None:
    from nahida_bot.core.chat_address import ChatAddress

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]

    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "telegram:private:123:abc"
    mock_app.message_router = mock_router

    mock_channel = AsyncMock()
    mock_channel.send_message = AsyncMock(return_value="msg-456")
    mock_app.channel_registry.get.return_value = mock_channel

    resp = await client_no_auth.post(
        "/api/send",
        json={"target": "telegram:private:123", "text": "hello"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "telegram:private:123:abc"
    address = mock_router.get_active_session_id.call_args.args[0]
    assert isinstance(address, ChatAddress)
    assert address.target_type == "private"
    assert address.target_id == "123"
    mock_channel.send_message.assert_called_once()
    sent_target, sent_message = mock_channel.send_message.call_args.args
    assert sent_target == "123"
    assert sent_message.extra["chat_address"] == "telegram:private:123"


async def test_send_rejects_legacy_platform_chat_id_payload(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "telegram:private:123"
    mock_app.message_router = mock_router

    resp = await client_no_auth.post(
        "/api/send",
        json={"platform": "telegram", "chat_id": "123", "text": "hello"},
    )
    assert resp.status_code == 400


# -- Cron -----------------------------------------------------------------


async def test_cron_list_returns_503_when_no_scheduler(
    client_no_auth: AsyncClient,
) -> None:
    resp = await client_no_auth.get("/api/cron?platform=t&chat_id=1")
    assert resp.status_code == 503


async def test_cron_list_returns_jobs(client_no_auth: AsyncClient) -> None:
    from nahida_bot.scheduler.models import CronJob

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.list_jobs.return_value = [
        CronJob(
            job_id="abc123",
            platform="telegram",
            chat_id="123",
            session_key="telegram:private:123",
            prompt="check news",
            mode="interval",
            fire_at=None,
            interval_seconds=3600,
            cron_expression=None,
            max_runs=None,
            run_count=5,
            is_active=True,
            created_at="2026-01-01T00:00:00",
            next_fire_at="2026-01-01T01:00:00",
            last_fired_at="2026-01-01T00:00:00",
            workspace_id=None,
            claimed_at=None,
            failure_count=0,
            last_error=None,
            session_mode="main",
            chat_type="private",
        )
    ]
    mock_app.scheduler_service = mock_scheduler

    resp = await client_no_auth.get("/api/cron?platform=telegram&chat_id=123")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["job_id"] == "abc123"
    assert data["jobs"][0]["mode"] == "interval"


async def test_cron_list_with_typed_target_passes_chat_type(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.list_jobs.return_value = []
    mock_app.scheduler_service = mock_scheduler

    resp = await client_no_auth.get("/api/cron?target=telegram:private:123")
    assert resp.status_code == 200
    address = mock_scheduler.list_jobs.call_args.args[0]
    assert address.channel == "telegram"
    assert address.target_type == "private"
    assert address.target_id == "123"


async def test_cron_create_success(client_no_auth: AsyncClient) -> None:
    from nahida_bot.scheduler.models import CronJob

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.create_job.return_value = CronJob(
        job_id="new-job-1",
        platform="telegram",
        chat_id="123",
        session_key="telegram:private:123",
        prompt="hello",
        mode="once",
        fire_at="2026-06-01T00:00:00",
        interval_seconds=None,
        cron_expression=None,
        max_runs=1,
        run_count=0,
        is_active=True,
        created_at="2026-01-01T00:00:00",
        next_fire_at="2026-06-01T00:00:00",
        last_fired_at=None,
        workspace_id=None,
        claimed_at=None,
        failure_count=0,
        last_error=None,
        session_mode="main",
        chat_type="private",
    )
    mock_app.scheduler_service = mock_scheduler

    resp = await client_no_auth.post(
        "/api/cron",
        json={
            "target": "telegram:private:123",
            "prompt": "hello",
            "mode": "once",
            "fire_at": "2026-06-01T00:00:00",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["job_id"] == "new-job-1"
    assert data["status"] == "created"


async def test_cron_mutations_notify_event_broadcaster(
    client_no_auth: AsyncClient,
) -> None:
    from nahida_bot.scheduler.models import CronJob

    class _Broadcaster:
        def __init__(self) -> None:
            self.events: list[tuple[str, str]] = []

        def notify_cron_updated(self, job_id: str, action: str) -> None:
            self.events.append((job_id, action))

    job = CronJob(
        job_id="job-1",
        platform="telegram",
        chat_id="123",
        session_key="telegram:private:123",
        prompt="hello",
        mode="once",
        fire_at="2026-06-01T00:00:00",
        interval_seconds=None,
        cron_expression=None,
        max_runs=1,
        run_count=0,
        is_active=True,
        created_at="2026-01-01T00:00:00",
        next_fire_at="2026-06-01T00:00:00",
        last_fired_at=None,
        workspace_id=None,
        claimed_at=None,
        failure_count=0,
        last_error=None,
        session_mode="main",
        chat_type="private",
    )
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.create_job.return_value = job
    mock_scheduler.update_job.return_value = job
    mock_scheduler.cancel_job.return_value = True
    mock_scheduler.delete_job.return_value = True
    mock_app.scheduler_service = mock_scheduler

    broadcaster = _Broadcaster()
    client_no_auth._transport.app.state.event_broadcaster = broadcaster  # type: ignore[attr-defined]

    await client_no_auth.post(
        "/api/cron",
        json={
            "target": "telegram:private:123",
            "prompt": "hello",
            "mode": "once",
            "fire_at": "2026-06-01T00:00:00",
        },
    )
    await client_no_auth.patch("/api/cron/job-1", json={"prompt": "updated"})
    await client_no_auth.post("/api/cron/job-1/cancel")
    await client_no_auth.delete("/api/cron/job-1")

    assert broadcaster.events == [
        ("job-1", "created"),
        ("job-1", "updated"),
        ("job-1", "cancelled"),
        ("job-1", "deleted"),
    ]


async def test_cron_create_validation_error(client_no_auth: AsyncClient) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.create_job.side_effect = ValueError("Invalid cron expression")
    mock_app.scheduler_service = mock_scheduler

    resp = await client_no_auth.post(
        "/api/cron",
        json={
            "target": "telegram:private:123",
            "prompt": "hello",
            "mode": "cron",
            "cron_expression": "bad",
        },
    )
    assert resp.status_code == 400
    assert "Invalid cron" in resp.json()["detail"]


async def test_cron_create_rejects_legacy_platform_chat_id_payload(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_app.scheduler_service = mock_scheduler

    resp = await client_no_auth.post(
        "/api/cron",
        json={
            "platform": "telegram",
            "chat_id": "123",
            "prompt": "hello",
            "mode": "once",
            "fire_at": "2026-06-01T00:00:00",
        },
    )
    assert resp.status_code == 400


async def test_cron_create_named_session_rejects_invalid_name(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_app.scheduler_service = AsyncMock()

    resp = await client_no_auth.post(
        "/api/cron",
        json={
            "target": "telegram:private:123",
            "prompt": "hello",
            "mode": "once",
            "fire_at": "2026-06-01T00:00:00",
            "session_mode": "named",
            "session_name": "bad name!",
        },
    )
    assert resp.status_code == 422


async def test_cron_create_named_session_rejects_missing_name(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_app.scheduler_service = AsyncMock()

    resp = await client_no_auth.post(
        "/api/cron",
        json={
            "target": "telegram:private:123",
            "prompt": "hello",
            "mode": "once",
            "fire_at": "2026-06-01T00:00:00",
            "session_mode": "named",
        },
    )
    assert resp.status_code == 422


async def test_cron_create_rejects_session_name_for_non_named_mode(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_app.scheduler_service = AsyncMock()

    resp = await client_no_auth.post(
        "/api/cron",
        json={
            "target": "telegram:private:123",
            "prompt": "hello",
            "mode": "once",
            "fire_at": "2026-06-01T00:00:00",
            "session_mode": "main",
            "session_name": "oops",
        },
    )
    assert resp.status_code == 422


# -- CORS -----------------------------------------------------------------


async def test_cors_headers(client_no_auth: AsyncClient) -> None:
    resp = await client_no_auth.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert "access-control-allow-origin" in resp.headers
