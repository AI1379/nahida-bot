"""Tests for the WebAPI layer."""

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import nahida_bot.gateway.app as gateway_app_module
import nahida_bot.gateway.routes.kb as kb_routes
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
            "_config_yaml_path",
            "memory_store",
            "message_delivery_store",
            "message_router",
            "channel_registry",
            "scheduler_service",
            "webapi_service",
            "webhost_service",
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
    mock._config_yaml_path = None
    mock.memory_store = None
    mock.message_delivery_store = None
    mock.message_router = None
    mock.channel_registry = MagicMock()
    mock.channel_registry.get.return_value = None
    mock.channel_registry._channels = {}
    mock.scheduler_service = None
    mock.webapi_service = None
    from nahida_bot.gateway.services.webhost import WebHostService

    mock.webhost_service = WebHostService()
    mock.workspace_manager = None
    mock.plugin_manager = None
    mock._provider_manager = None
    mock._usage_ledger = None
    mock.started_at = None
    mock.request_shutdown = MagicMock()
    return mock


def _make_plugin_record(tmp_path, *, state: str = "enabled"):
    from nahida_bot.plugins.manager import PluginRecord, PluginState
    from nahida_bot.plugins.manifest import PluginManifest

    manifest = PluginManifest(
        id="demo.plugin",
        name="Demo Plugin",
        version="1.2.3",
        description="Demo plugin for tests",
        entrypoint="demo_plugin:DemoPlugin",
        load_phase="post-agent",
        config={"api_key": "secret-value", "mode": "test"},
        config_schema={"type": "object"},
    )
    return PluginRecord(
        manifest=manifest,
        plugin_dir=tmp_path / "plugins" / "demo",
        state=PluginState(state),
    )


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


async def test_plugin_webhook_dispatch_does_not_require_api_token(
    client_with_auth: AsyncClient,
) -> None:
    from nahida_bot_sdk import WebhookResponse

    app = client_with_auth._transport.app  # type: ignore[attr-defined]
    mock_app = app.state.application
    seen: dict[str, object] = {}

    async def _handler(request):
        seen["method"] = request.method
        seen["path"] = request.path
        seen["body"] = request.body
        seen["headers"] = dict(request.headers)
        return WebhookResponse(status_code=202, body="accepted")

    mock_app.webhost_service.register(
        plugin_id="test",
        path="github",
        handler=_handler,
        methods=("POST",),
    )

    resp = await client_with_auth.post(
        "/webhooks/github?x=1",
        content=b'{"ok": true}',
        headers={"X-Test-Header": "yes"},
    )

    assert resp.status_code == 202
    assert resp.text == "accepted"
    assert seen["method"] == "POST"
    assert seen["path"] == "github"
    assert seen["body"] == b'{"ok": true}'
    assert seen["headers"]["x-test-header"] == "yes"


async def test_plugin_webhook_unknown_path_returns_404(
    client_no_auth: AsyncClient,
) -> None:
    resp = await client_no_auth.post("/webhooks/missing", content=b"{}")
    assert resp.status_code == 404


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


# -- Plugins --------------------------------------------------------------


async def test_plugins_list_returns_sanitized_manifest(
    client_no_auth: AsyncClient,
    tmp_path,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    record = _make_plugin_record(tmp_path)
    manager = MagicMock()
    manager.list_plugins.return_value = [record]
    mock_app.plugin_manager = manager

    resp = await client_no_auth.get("/api/plugins")

    assert resp.status_code == 200
    plugin = resp.json()["plugins"][0]
    assert plugin["id"] == "demo.plugin"
    assert plugin["state"] == "enabled"
    assert plugin["has_config"] is True
    assert plugin["config_keys"] == ["api_key", "mode"]
    assert "secret-value" not in resp.text


async def test_plugins_returns_503_without_manager(client_no_auth: AsyncClient) -> None:
    resp = await client_no_auth.get("/api/plugins")

    assert resp.status_code == 503


async def test_plugin_enable_action_returns_new_state(
    client_no_auth: AsyncClient,
    tmp_path,
) -> None:
    from nahida_bot.plugins.manager import PluginState

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    record = _make_plugin_record(tmp_path, state="loaded")
    manager = MagicMock()
    manager.get_record.return_value = record

    async def enable(plugin_id: str) -> None:
        assert plugin_id == "demo.plugin"
        record.state = PluginState.ENABLED

    manager.enable = AsyncMock(side_effect=enable)
    mock_app.plugin_manager = manager

    resp = await client_no_auth.post("/api/plugins/demo.plugin/enable")

    assert resp.status_code == 200
    assert resp.json() == {
        "plugin_id": "demo.plugin",
        "action": "enable",
        "state": "enabled",
        "status": "ok",
    }


async def test_plugin_action_state_error_returns_409(
    client_no_auth: AsyncClient,
    tmp_path,
) -> None:
    from nahida_bot.core.exceptions import PluginStateError

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    record = _make_plugin_record(tmp_path)
    manager = MagicMock()
    manager.get_record.return_value = record
    manager.enable = AsyncMock(side_effect=PluginStateError("bad transition"))
    mock_app.plugin_manager = manager

    resp = await client_no_auth.post("/api/plugins/demo.plugin/enable")

    assert resp.status_code == 409
    assert "bad transition" in resp.json()["detail"]


# -- Memory ---------------------------------------------------------------


async def test_memory_items_route_creates_lists_and_archives(
    client_no_auth: AsyncClient,
) -> None:
    from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
    from nahida_bot.db.engine import DatabaseEngine

    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_app.memory_store = store

    try:
        create_resp = await client_no_auth.post(
            "/api/memory/items",
            json={
                "title": "language preference",
                "content": "User prefers Chinese for architecture discussion.",
                "kind": "preference",
                "scope_type": "global",
                "scope_id": "__global__",
                "sensitivity": "public",
            },
        )
        assert create_resp.status_code == 201
        item_id = create_resp.json()["item_id"]

        list_resp = await client_no_auth.get("/api/memory/items?q=Chinese")
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert [item["item_id"] for item in items] == [item_id]
        assert items[0]["source"] == "webui"
        assert items[0]["sensitivity"] == "public"

        archive_resp = await client_no_auth.delete(f"/api/memory/items/{item_id}")
        assert archive_resp.status_code == 200
        assert archive_resp.json() == {"item_id": item_id, "status": "archived"}

        after_resp = await client_no_auth.get("/api/memory/items?q=Chinese")
        assert after_resp.status_code == 200
        assert after_resp.json()["items"] == []
    finally:
        await engine.close()


async def test_memory_items_route_supports_independent_scope_wildcards(
    client_no_auth: AsyncClient,
) -> None:
    from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
    from nahida_bot.db.engine import DatabaseEngine

    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_app.memory_store = store

    try:
        global_id = await store.append_item(
            title="global",
            content="Shared architecture decision",
            kind="decision",
        )
        chat_a_id = await store.append_item(
            title="chat a",
            content="Alice prefers concise Chinese reports",
            kind="preference",
            scope_type="chat",
            scope_id="telegram:private:1",
        )
        chat_b_id = await store.append_item(
            title="chat b",
            content="Bob prefers detailed English reports",
            kind="preference",
            scope_type="chat",
            scope_id="telegram:private:2",
        )

        all_resp = await client_no_auth.get(
            "/api/memory/items", params={"scope_type": "", "scope_id": ""}
        )
        assert all_resp.status_code == 200
        assert {item["item_id"] for item in all_resp.json()["items"]} == {
            global_id,
            chat_a_id,
            chat_b_id,
        }

        chat_resp = await client_no_auth.get(
            "/api/memory/items", params={"scope_type": "chat", "scope_id": ""}
        )
        assert {item["item_id"] for item in chat_resp.json()["items"]} == {
            chat_a_id,
            chat_b_id,
        }

        id_resp = await client_no_auth.get(
            "/api/memory/items",
            params={"scope_type": "", "scope_id": "telegram:private:1"},
        )
        assert [item["item_id"] for item in id_resp.json()["items"]] == [chat_a_id]

        search_resp = await client_no_auth.get(
            "/api/memory/items",
            params={"q": "reports", "scope_type": "", "scope_id": ""},
        )
        assert {item["item_id"] for item in search_resp.json()["items"]} == {
            chat_a_id,
            chat_b_id,
        }
    finally:
        await engine.close()


async def test_memory_candidates_and_turns_routes(
    client_no_auth: AsyncClient,
) -> None:
    from nahida_bot.agent.memory.models import ConversationTurn
    from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
    from nahida_bot.db.engine import DatabaseEngine

    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_app.memory_store = store

    try:
        await store.ensure_session("telegram:private:123")
        await store.append_turn(
            "telegram:private:123",
            ConversationTurn(
                role="user",
                content="Please remember that I prefer concise reports.",
                source="user_input",
                metadata={"chat_address": "telegram:private:123"},
            ),
        )
        await store.append_candidate(
            candidate_id="cand_1",
            title="report preference",
            content="User prefers concise reports.",
            kind="preference",
            status="pending",
        )
        await store.append_candidate(
            candidate_id="cand_2",
            title="chat report preference",
            content="A chat-scoped candidate.",
            kind="preference",
            status="pending",
            scope_type="chat",
            scope_id="telegram:private:123",
        )

        candidates_resp = await client_no_auth.get("/api/memory/candidates")
        assert candidates_resp.status_code == 200
        candidates = candidates_resp.json()["candidates"]
        assert candidates[0]["candidate_id"] == "cand_1"
        assert candidates[0]["status"] == "pending"

        all_candidates_resp = await client_no_auth.get(
            "/api/memory/candidates",
            params={"scope_type": "", "scope_id": ""},
        )
        assert {
            candidate["candidate_id"]
            for candidate in all_candidates_resp.json()["candidates"]
        } == {"cand_1", "cand_2"}

        chat_candidates_resp = await client_no_auth.get(
            "/api/memory/candidates",
            params={"scope_type": "chat", "scope_id": ""},
        )
        assert [
            candidate["candidate_id"]
            for candidate in chat_candidates_resp.json()["candidates"]
        ] == ["cand_2"]

        turns_resp = await client_no_auth.get(
            "/api/memory/turns?q=concise&chat_address=telegram:private:123"
        )
        assert turns_resp.status_code == 200
        turns = turns_resp.json()["turns"]
        assert len(turns) == 1
        assert turns[0]["session_id"] == "telegram:private:123"
        assert turns[0]["role"] == "user"
    finally:
        await engine.close()


async def test_memory_project_route_filters_restricted_items(
    client_no_auth: AsyncClient,
    tmp_path,
) -> None:
    from nahida_bot.agent.memory.markdown import MEMORY_SUMMARY_FILE
    from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
    from nahida_bot.db.engine import DatabaseEngine
    from nahida_bot.workspace.manager import WorkspaceManager

    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    workspace_manager = WorkspaceManager(tmp_path / "workspace-state")
    workspace_manager.initialize()
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_app.memory_store = store
    mock_app.workspace_manager = workspace_manager

    try:
        await store.append_item(title="public", content="Public memory for projection.")
        await store.append_item(
            title="secret",
            content="Secret memory must not be projected.",
            sensitivity="secret_like",
            sensitivity_source="explicit",
        )

        resp = await client_no_auth.post("/api/memory/project", json={})

        assert resp.status_code == 200
        assert resp.json()["status"] == "projected"
        summary = (
            workspace_manager.workspace_path("default") / MEMORY_SUMMARY_FILE
        ).read_text(encoding="utf-8")
        assert "Public memory for projection." in summary
        assert "Secret memory must not be projected." not in summary
    finally:
        await engine.close()


# -- Knowledge Base -------------------------------------------------------


async def test_kb_collections_route_uses_plugin_summary_data(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    kb_plugin = MagicMock()
    kb_plugin.list_collection_summaries = AsyncMock(
        return_value=[
            {
                "name": "python_docs",
                "document_count": 3,
                "created_at": "2026-06-13T00:00:00+00:00",
            }
        ]
    )
    manager = MagicMock()
    manager.get_record.return_value = SimpleNamespace(instance=kb_plugin)
    mock_app.plugin_manager = manager

    resp = await client_no_auth.get("/api/kb/collections")

    assert resp.status_code == 200
    assert resp.json() == {
        "collections": [
            {
                "name": "python_docs",
                "document_count": 3,
                "created_at": "2026-06-13T00:00:00+00:00",
            }
        ]
    }
    kb_plugin.list_collection_summaries.assert_awaited_once()


async def test_kb_import_text_invalid_collection_returns_400(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    kb_plugin = MagicMock()
    kb_plugin.import_content = AsyncMock(
        side_effect=ValueError(
            "Collection name must contain only letters, digits, and underscores."
        )
    )
    manager = MagicMock()
    manager.get_record.return_value = SimpleNamespace(instance=kb_plugin)
    mock_app.plugin_manager = manager

    resp = await client_no_auth.post(
        "/api/kb/collections/bad-name/import-text",
        data={"source": "Guide", "content": "hello", "content_type": "text"},
    )

    assert resp.status_code == 400
    assert "underscores" in resp.json()["detail"]


async def test_kb_import_file_converts_document_before_ingestion(
    client_no_auth: AsyncClient,
    monkeypatch,
) -> None:
    from nahida_bot.plugins.knowledge_base.document_conversion import (
        ConvertedDocument,
    )

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    kb_plugin = MagicMock()
    kb_plugin.import_content = AsyncMock(return_value=2)
    manager = MagicMock()
    manager.get_record.return_value = SimpleNamespace(instance=kb_plugin)
    mock_app.plugin_manager = manager

    monkeypatch.setattr(
        kb_routes,
        "convert_document_bytes",
        lambda data, filename: ConvertedDocument(
            content="# Converted report",
            content_type="markdown",
        ),
    )

    resp = await client_no_auth.post(
        "/api/kb/collections/reports/import-file",
        files={"file": ("quarterly.pdf", b"%PDF-test", "application/pdf")},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "collection": "reports",
        "source": "quarterly.pdf",
        "chunks": 2,
    }
    kb_plugin.import_content.assert_awaited_once()
    call = kb_plugin.import_content.await_args
    assert call.args == ("reports",)
    assert call.kwargs["source_id"] == "quarterly"
    assert call.kwargs["content"] == "# Converted report"
    assert call.kwargs["content_type"] == "markdown"
    assert call.kwargs["extra_metadata"]["original_content_type"] == "application/pdf"


async def test_kb_import_file_missing_optional_dependency_returns_503(
    client_no_auth: AsyncClient,
    monkeypatch,
) -> None:
    from nahida_bot.plugins.knowledge_base.document_conversion import (
        DocumentImportDependencyError,
    )

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    kb_plugin = MagicMock()
    manager = MagicMock()
    manager.get_record.return_value = SimpleNamespace(instance=kb_plugin)
    mock_app.plugin_manager = manager

    def raise_missing_dependency(data: bytes, filename: str):
        raise DocumentImportDependencyError(
            "Run `uv sync --extra document-import` and restart Nahida Bot."
        )

    monkeypatch.setattr(
        kb_routes,
        "convert_document_bytes",
        raise_missing_dependency,
    )

    resp = await client_no_auth.post(
        "/api/kb/collections/reports/import-file",
        files={"file": ("quarterly.pdf", b"%PDF-test", "application/pdf")},
    )

    assert resp.status_code == 503
    assert "document-import" in resp.json()["detail"]


async def test_kb_import_file_rejects_oversized_upload(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    kb_plugin = MagicMock()
    manager = MagicMock()
    manager.get_record.return_value = SimpleNamespace(instance=kb_plugin)
    mock_app.plugin_manager = manager

    resp = await client_no_auth.post(
        "/api/kb/collections/reports/import-file",
        files={
            "file": (
                "large.pdf",
                b"x" * (25 * 1024 * 1024 + 1),
                "application/pdf",
            )
        },
    )

    assert resp.status_code == 413
    assert "25 MiB" in resp.json()["detail"]


async def test_kb_import_files_returns_partial_results(
    client_no_auth: AsyncClient,
    monkeypatch,
) -> None:
    from nahida_bot.plugins.knowledge_base.document_conversion import (
        ConvertedDocument,
        DocumentConversionError,
    )

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    kb_plugin = MagicMock()
    kb_plugin.import_content = AsyncMock(side_effect=[2, 1])
    manager = MagicMock()
    manager.get_record.return_value = SimpleNamespace(instance=kb_plugin)
    mock_app.plugin_manager = manager

    def convert(data: bytes, filename: str) -> ConvertedDocument:
        if filename == "broken.pdf":
            raise DocumentConversionError("Failed to convert 'broken.pdf'.")
        return ConvertedDocument(
            content=f"# {filename}",
            content_type="markdown",
        )

    monkeypatch.setattr(kb_routes, "convert_document_bytes", convert)

    resp = await client_no_auth.post(
        "/api/kb/collections/reports/import-files",
        files=[
            ("files", ("first.pdf", b"first", "application/pdf")),
            ("files", ("broken.pdf", b"broken", "application/pdf")),
            (
                "files",
                (
                    "second.docx",
                    b"second",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            ),
        ],
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "collection": "reports",
        "imported_files": 2,
        "failed_files": 1,
        "chunks": 3,
        "results": [
            {
                "source": "first.pdf",
                "status": "imported",
                "chunks": 2,
                "error": "",
            },
            {
                "source": "broken.pdf",
                "status": "failed",
                "chunks": 0,
                "error": "Failed to convert 'broken.pdf'.",
            },
            {
                "source": "second.docx",
                "status": "imported",
                "chunks": 1,
                "error": "",
            },
        ],
    }
    assert kb_plugin.import_content.await_count == 2


async def test_kb_import_files_rejects_more_than_200_documents(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    kb_plugin = MagicMock()
    manager = MagicMock()
    manager.get_record.return_value = SimpleNamespace(instance=kb_plugin)
    mock_app.plugin_manager = manager

    resp = await client_no_auth.post(
        "/api/kb/collections/reports/import-files",
        files=[
            ("files", (f"{index}.txt", b"text", "text/plain")) for index in range(201)
        ],
    )

    assert resp.status_code == 400
    assert "At most 200 documents" in resp.json()["detail"]


async def test_kb_create_collection_conflict_returns_409(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    kb_plugin = MagicMock()
    kb_plugin.create_collection = AsyncMock(
        side_effect=ValueError("Collection 'python_docs' already exists")
    )
    manager = MagicMock()
    manager.get_record.return_value = SimpleNamespace(instance=kb_plugin)
    mock_app.plugin_manager = manager

    resp = await client_no_auth.post("/api/kb/collections/python_docs/create")

    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


# -- Config ---------------------------------------------------------------


async def test_config_current_includes_flattened_redacted_values(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
app_name: Demo Bot
webapi:
  host: 127.0.0.1
  auth_token: secret-token
providers:
  deepseek:
    api_key: ds-key
    base_url: https://example.invalid
    models:
      - name: deepseek-chat
integrations:
  - name: demo
    api_key: list-secret
""",
        encoding="utf-8",
    )
    app = _make_mock_app()
    app._config_yaml_path = str(config_path)
    webapi = WebAPIApp(application=app, host="127.0.0.1", port=6185)
    transport = ASGITransport(app=webapi.fastapi_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/config/current")

    assert resp.status_code == 200
    data = resp.json()
    entries = {entry["path"]: entry for entry in data["entries"]}

    assert "secret-token" not in data["content"]
    assert "list-secret" not in data["content"]
    assert entries["app_name"]["value"] == "Demo Bot"
    assert entries["webapi.host"]["value"] == "127.0.0.1"
    assert entries["webapi.auth_token"]["value"] == "***"
    assert entries["providers.deepseek.api_key"]["value"] == "***"
    assert entries["providers.deepseek.models[0].name"]["value"] == "deepseek-chat"
    assert entries["integrations[0].api_key"]["value"] == "***"


async def test_config_document_returns_structured_redacted_data(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
app_name: Demo Bot
providers:
  default:
    type: openai-compatible
    api_key: secret-key
    base_url: https://old.example
    models:
      - demo-model
default_provider: default
multimodal:
  image_fallback_mode: "off"
""",
        encoding="utf-8",
    )
    app = _make_mock_app()
    app._config_yaml_path = str(config_path)
    webapi = WebAPIApp(application=app, host="127.0.0.1", port=6185)
    transport = ASGITransport(app=webapi.fastapi_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/config/document")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["providers"]["default"]["api_key"] == "secret-key"
    assert data["redacted_data"]["providers"]["default"]["api_key"] == "***"
    assert "providers.default.api_key" in data["redacted_paths"]
    assert "secret-key" not in data["content"]


async def test_config_patch_preserves_unmodified_secret(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
app_name: Demo Bot
debug: false
providers:
  default:
    type: openai-compatible
    api_key: secret-key
    base_url: https://old.example
    models:
      - demo-model
default_provider: default
multimodal:
  image_fallback_mode: "off"
""",
        encoding="utf-8",
    )
    app = _make_mock_app()
    app._config_yaml_path = str(config_path)
    webapi = WebAPIApp(application=app, host="127.0.0.1", port=6185)
    transport = ASGITransport(app=webapi.fastapi_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        document = await client.get("/api/config/document")
        patch = await client.patch(
            "/api/config/current",
            json={
                "expected_checksum": document.json()["checksum"],
                "changes": [
                    {"path": "debug", "value": True},
                    {
                        "path": "providers.default.base_url",
                        "value": "https://new.example",
                    },
                ],
            },
        )

    assert patch.status_code == 200
    saved = config_path.read_text(encoding="utf-8")
    assert "debug: true" in saved
    assert "https://new.example" in saved
    assert "secret-key" in saved
    assert "***" not in saved
    assert (tmp_path / "config_backups").exists()


async def test_config_patch_rejects_redacted_secret_placeholder(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
providers:
  default:
    type: openai-compatible
    api_key: secret-key
    models:
      - demo-model
default_provider: default
multimodal:
  image_fallback_mode: "off"
""",
        encoding="utf-8",
    )
    app = _make_mock_app()
    app._config_yaml_path = str(config_path)
    webapi = WebAPIApp(application=app, host="127.0.0.1", port=6185)
    transport = ASGITransport(app=webapi.fastapi_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        document = await client.get("/api/config/document")
        patch = await client.patch(
            "/api/config/current",
            json={
                "expected_checksum": document.json()["checksum"],
                "changes": [
                    {
                        "path": "providers.default.api_key",
                        "value": "***",
                        "secret_action": "replace",
                    }
                ],
            },
        )

    assert patch.status_code == 409
    assert "secret-key" in config_path.read_text(encoding="utf-8")


# -- Files ----------------------------------------------------------------


async def test_file_upload_and_raw_image_preview(tmp_path) -> None:
    from nahida_bot.workspace.manager import WorkspaceManager

    manager = WorkspaceManager(tmp_path / "workspace-state")
    manager.initialize()
    app = _make_mock_app()
    app.workspace_manager = manager
    webapi = WebAPIApp(application=app, host="127.0.0.1", port=6185)
    transport = ASGITransport(app=webapi.fastapi_app)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01"
        b"\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        workspaces = await client.get("/api/workspaces")
        upload = await client.post(
            "/api/files/upload",
            data={"path": "images/pixel.png", "workspace_id": "default"},
            files={"file": ("pixel.png", png, "image/png")},
        )
        conflict = await client.post(
            "/api/files/upload",
            data={"path": "images/pixel.png", "workspace_id": "default"},
            files={"file": ("pixel.png", png, "image/png")},
        )
        listing = await client.get("/api/files?workspace_id=default&path=images")
        raw = await client.get(
            "/api/files/raw?workspace_id=default&path=images/pixel.png"
        )
        text = await client.get(
            "/api/files/content?workspace_id=default&path=images/pixel.png"
        )
        unsupported = await client.post(
            "/api/files/upload",
            data={"path": "bad.exe", "workspace_id": "default"},
            files={"file": ("bad.exe", b"nope", "application/octet-stream")},
        )

    assert workspaces.status_code == 200
    assert workspaces.json()["active"] == "default"
    assert upload.status_code == 200
    assert upload.json()["path"] == "images/pixel.png"
    assert conflict.status_code == 409
    assert listing.status_code == 200
    assert listing.json()["entries"][0]["name"] == "pixel.png"
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")
    assert raw.content == png
    assert text.status_code == 400
    assert unsupported.status_code == 400


async def test_file_content_accepts_common_code_files(tmp_path) -> None:
    from nahida_bot.workspace.manager import WorkspaceManager

    manager = WorkspaceManager(tmp_path / "workspace-state")
    manager.initialize()
    app = _make_mock_app()
    app.workspace_manager = manager
    webapi = WebAPIApp(application=app, host="127.0.0.1", port=6185)
    transport = ASGITransport(app=webapi.fastapi_app)
    samples = {
        "scripts/task.py": "print('hello')\n",
        "scripts/run.sh": "#!/usr/bin/env bash\necho hello\n",
        "src/main.cpp": "#include <iostream>\nint main() { return 0; }\n",
        "web/app.js": "export const answer = 42;\n",
        "src/main.rs": 'fn main() { println!("hello"); }\n',
        "Dockerfile": "FROM python:3.12-slim\n",
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path, content in samples.items():
            created = await client.post(
                "/api/files/create",
                json={
                    "path": path,
                    "content": content,
                    "workspace_id": "default",
                },
            )
            read = await client.get(
                "/api/files/content",
                params={"workspace_id": "default", "path": path},
            )

            assert created.status_code == 200
            assert read.status_code == 200
            assert read.json()["content"] == content


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


async def test_session_history_includes_metadata_and_sentinel(
    client_no_auth: AsyncClient,
) -> None:
    from datetime import UTC, datetime

    from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_memory = AsyncMock()
    mock_memory.get_recent.return_value = [
        MemoryRecord(
            turn_id=7,
            session_id="telegram:private:123",
            turn=ConversationTurn(
                role="assistant",
                content="NO_REPLY",
                source="agent_response",
                metadata={"reason": "silent"},
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    ]
    mock_app.memory_store = mock_memory

    resp = await client_no_auth.get("/api/sessions/telegram:private:123")

    assert resp.status_code == 200
    turn = resp.json()["turns"][0]
    assert turn["metadata"] == {"reason": "silent"}
    assert turn["sentinel_action"] == "NO_REPLY"
    assert turn["sentinel_suppressed"] is True


async def test_delivery_group_and_detail_endpoints(client_no_auth: AsyncClient) -> None:
    from nahida_bot.db.repositories.sqlite_message_delivery_repo import (
        MessageDelivery,
        MessageDeliveryGroup,
    )

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_store = AsyncMock()
    mock_store.list_groups.return_value = [
        MessageDeliveryGroup(
            target_chat_address="telegram:private:123",
            platform="telegram",
            target_type="private",
            target_id="123",
            count=2,
            last_created_at="2026-01-01T00:00:00+00:00",
            last_source="message_tool",
        )
    ]
    mock_store.list_for_target.return_value = [
        MessageDelivery(
            delivery_id="d1",
            target_chat_address="telegram:private:123",
            platform="telegram",
            target_type="private",
            target_id="123",
            source="message_tool",
            delivery_mode="notify",
            status="sent",
            message_id="m1",
            text="hello",
            created_at="2026-01-01T00:00:00+00:00",
        )
    ]
    mock_app.message_delivery_store = mock_store

    groups_resp = await client_no_auth.get("/api/sessions/delivery-groups")
    detail_resp = await client_no_auth.get(
        "/api/sessions/deliveries?target=telegram:private:123"
    )

    assert groups_resp.status_code == 200
    assert groups_resp.json()["groups"][0]["target_chat_address"] == (
        "telegram:private:123"
    )
    assert detail_resp.status_code == 200
    delivery = detail_resp.json()["deliveries"][0]
    assert delivery["delivery_id"] == "d1"
    assert delivery["source"] == "message_tool"


async def test_session_search_mixes_turns_and_deliveries(
    client_no_auth: AsyncClient,
) -> None:
    from datetime import UTC, datetime

    from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord
    from nahida_bot.db.repositories.sqlite_message_delivery_repo import MessageDelivery

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_memory = AsyncMock()
    mock_memory.search_turns.return_value = [
        MemoryRecord(
            turn_id=1,
            session_id="telegram:private:123",
            turn=ConversationTurn(
                role="user",
                content="hello from turn",
                source="user_input",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    ]
    mock_store = AsyncMock()
    mock_store.search.return_value = [
        MessageDelivery(
            delivery_id="d1",
            target_chat_address="telegram:private:123",
            platform="telegram",
            target_type="private",
            target_id="123",
            source="webapi_send",
            delivery_mode="notify",
            status="sent",
            text="hello from delivery",
            created_at="2026-01-02T00:00:00+00:00",
        )
    ]
    mock_app.memory_store = mock_memory
    mock_app.message_delivery_store = mock_store

    resp = await client_no_auth.get("/api/sessions/search?q=hello")

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [item["result_type"] for item in results] == ["delivery", "turn"]
    assert results[0]["target_chat_address"] == "telegram:private:123"
    mock_memory.search_turns.assert_awaited_once()
    mock_store.search.assert_awaited_once()


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


async def test_send_success_records_delivery_audit(
    client_no_auth: AsyncClient,
) -> None:
    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]

    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "telegram:private:123:abc"
    mock_app.message_router = mock_router

    mock_channel = AsyncMock()
    mock_channel.send_message = AsyncMock(return_value="msg-456")
    mock_app.channel_registry.get.return_value = mock_channel
    mock_store = AsyncMock()
    mock_app.message_delivery_store = mock_store

    resp = await client_no_auth.post(
        "/api/send",
        json={"target": "telegram:private:123", "text": "hello"},
    )

    assert resp.status_code == 200
    mock_store.record.assert_awaited_once()
    call = mock_store.record.await_args.kwargs
    assert call["target_chat_address"] == "telegram:private:123"
    assert call["source_session_id"] == "telegram:private:123:abc"
    assert call["source"] == "webapi_send"
    assert call["message_id"] == "msg-456"
    assert call["text"] == "hello"


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
            is_active=False,
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
    assert data["jobs"][0]["next_fire_at"] is None
    assert data["jobs"][0]["last_fired_at"] == "2026-01-01T00:00:00"


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


async def test_cron_create_accepts_fresh_session_mode(
    client_no_auth: AsyncClient,
) -> None:
    from nahida_bot.scheduler.models import CronJob

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.create_job.return_value = CronJob(
        job_id="fresh-job",
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
        session_mode="fresh",
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
            "session_mode": "fresh",
        },
    )

    assert resp.status_code == 201
    assert mock_scheduler.create_job.await_args.kwargs["session_mode"] == "fresh"


async def test_cron_update_accepts_session_mode_and_name(
    client_no_auth: AsyncClient,
) -> None:
    from nahida_bot.scheduler.models import CronJob

    mock_app = client_no_auth._transport.app.state.application  # type: ignore[attr-defined]
    mock_scheduler = AsyncMock()
    mock_scheduler.update_job.return_value = CronJob(
        job_id="named-job",
        platform="telegram",
        chat_id="123",
        session_key="telegram:private:123",
        prompt="hello",
        mode="interval",
        fire_at=None,
        interval_seconds=120,
        cron_expression=None,
        max_runs=None,
        run_count=0,
        is_active=True,
        created_at="2026-01-01T00:00:00",
        next_fire_at="2026-06-01T00:00:00",
        last_fired_at=None,
        workspace_id=None,
        claimed_at=None,
        failure_count=0,
        last_error=None,
        session_mode="named",
        session_name="daily-summary",
        chat_type="private",
    )
    mock_app.scheduler_service = mock_scheduler

    resp = await client_no_auth.patch(
        "/api/cron/named-job",
        json={
            "session_mode": "named",
            "session_name": "daily-summary",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["session_mode"] == "named"
    assert data["session_name"] == "daily-summary"
    kwargs = mock_scheduler.update_job.await_args.kwargs
    assert kwargs["session_mode"] == "named"
    assert kwargs["session_name"] == "daily-summary"


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
    mock_scheduler.activate_job.return_value = job
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
    await client_no_auth.post("/api/cron/job-1/activate")
    await client_no_auth.post("/api/cron/job-1/cancel")
    await client_no_auth.delete("/api/cron/job-1")

    assert broadcaster.events == [
        ("job-1", "created"),
        ("job-1", "updated"),
        ("job-1", "activated"),
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
