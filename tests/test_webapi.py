"""Tests for the WebAPI layer."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from nahida_bot.core.config import Settings, WebAPIConfigModel
from nahida_bot.gateway.app import WebAPIApp


def _make_mock_app(
    *,
    auth_token: str = "",
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
    )
    mock = MagicMock(
        spec=[
            "settings",
            "is_started",
            "is_initialized",
            "memory_store",
            "message_router",
            "channel_registry",
            "scheduler_service",
        ]
    )
    mock.settings = settings
    mock.is_started = is_started
    mock.is_initialized = True
    mock.memory_store = None
    mock.message_router = None
    mock.channel_registry = MagicMock()
    mock.channel_registry.get.return_value = None
    mock.scheduler_service = None
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
async def client_no_auth(webapi_no_auth) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=webapi_no_auth.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def client_with_auth(webapi_with_auth) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=webapi_with_auth.fastapi_app)
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
            session_id="telegram:123",
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
    assert data["sessions"][0]["session_id"] == "telegram:123"
    assert data["sessions"][0]["turn_count"] == 5


# -- Send Message --------------------------------------------------------


async def test_send_returns_503_when_no_router(client_no_auth: AsyncClient) -> None:
    resp = await client_no_auth.post(
        "/api/send",
        json={"platform": "telegram", "chat_id": "123", "text": "hello"},
    )
    assert resp.status_code == 503


async def test_send_returns_404_when_channel_not_found(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "telegram:123"
    mock_app.message_router = mock_router

    resp = await client_no_auth.post(
        "/api/send",
        json={"platform": "telegram", "chat_id": "123", "text": "hello"},
    )
    assert resp.status_code == 404
    assert "telegram" in resp.json()["detail"]


async def test_send_success(client_no_auth: AsyncClient) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]

    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "telegram:123"
    mock_app.message_router = mock_router

    mock_channel = AsyncMock()
    mock_channel.send_message = AsyncMock(return_value="msg-456")
    mock_app.channel_registry.get.return_value = mock_channel

    resp = await client_no_auth.post(
        "/api/send",
        json={"platform": "telegram", "chat_id": "123", "text": "hello"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "sent"
    assert data["session_id"] == "telegram:123"
    mock_channel.send_message.assert_called_once()


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
            session_key="telegram:123",
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
        )
    ]
    mock_app.scheduler_service = mock_scheduler

    resp = await client_no_auth.get("/api/cron?platform=telegram&chat_id=123")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["job_id"] == "abc123"
    assert data["jobs"][0]["mode"] == "interval"


async def test_cron_create_success(client_no_auth: AsyncClient) -> None:
    from nahida_bot.scheduler.models import CronJob

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.create_job.return_value = CronJob(
        job_id="new-job-1",
        platform="telegram",
        chat_id="123",
        session_key="telegram:123",
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
    )
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
    assert resp.status_code == 201
    data = resp.json()
    assert data["job_id"] == "new-job-1"
    assert data["status"] == "created"


async def test_cron_create_validation_error(client_no_auth: AsyncClient) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.create_job.side_effect = ValueError("Invalid cron expression")
    mock_app.scheduler_service = mock_scheduler

    resp = await client_no_auth.post(
        "/api/cron",
        json={
            "platform": "telegram",
            "chat_id": "123",
            "prompt": "hello",
            "mode": "cron",
            "cron_expression": "bad",
        },
    )
    assert resp.status_code == 400
    assert "Invalid cron" in resp.json()["detail"]


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
