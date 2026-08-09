"""Tests for the public ``/api/nodes/pairing/complete`` endpoint.

The pairing completion endpoint is intentionally not behind ``require_token``:
the pairing token itself is the credential, and requiring WebUI admin auth on
top of it would defeat the out-of-band pairing flow (admin mints the pairing
token, gives it to the desktop user, desktop exchanges it without needing
admin rights).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from nahida_bot.gateway.app import WebAPIApp
from nahida_bot.gateway.services.node_auth import NodeAuthService


def _make_mock_app() -> MagicMock:
    """Build a minimal mock application suitable for WebAPIApp wiring."""
    from nahida_bot.core.config import (
        Settings,
        WebAPIConfigModel,
        WebUIAuthConfigModel,
        WebUIConfigModel,
    )

    settings = Settings(
        app_name="Test WebAPI",
        debug=True,
        db_path=":memory:",
        plugin_paths=[],
        discover_builtin_channels=False,
        webapi=WebAPIConfigModel(auth_token=""),
        webui=WebUIConfigModel(
            auth=WebUIAuthConfigModel(),
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
        ],
    )
    mock.settings = settings
    mock.version = "0.1-test"
    mock.is_started = True
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


@pytest.fixture
def webapi_app() -> WebAPIApp:
    return WebAPIApp(
        application=_make_mock_app(),
        host="127.0.0.1",
        port=6185,
    )


@pytest.fixture
async def client(webapi_app: WebAPIApp) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=webapi_app.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_pairing_complete_is_public_for_valid_token(
    client: AsyncClient, webapi_app: WebAPIApp
) -> None:
    auth: NodeAuthService = webapi_app.node_auth  # type: ignore[assignment]
    pairing_token, _ = await auth.issue_pairing_token(
        node_id="desktop-local",
        display_name="Nahida Desktop",
    )

    resp = await client.post(
        "/api/nodes/pairing/complete",
        json={"pairing_token": pairing_token},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["node_id"] == "desktop-local"
    assert body["node_token"].startswith("nt_")
    # Issued node token must verify against the same service.
    principal = await auth.verify(body["node_token"])
    assert principal is not None
    assert principal.node_id == "desktop-local"
    assert principal.token_type == "node"


async def test_pairing_complete_rejects_consumed_token(
    client: AsyncClient, webapi_app: WebAPIApp
) -> None:
    auth: NodeAuthService = webapi_app.node_auth  # type: ignore[assignment]
    pairing_token, _ = await auth.issue_pairing_token(node_id="desktop-local")

    first = await client.post(
        "/api/nodes/pairing/complete",
        json={"pairing_token": pairing_token},
    )
    second = await client.post(
        "/api/nodes/pairing/complete",
        json={"pairing_token": pairing_token},
    )

    assert first.status_code == 200
    assert second.status_code == 400


async def test_pairing_complete_rejects_bogus_token(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/nodes/pairing/complete",
        json={"pairing_token": "np_bogus.nope"},
    )
    assert resp.status_code == 400


async def test_node_management_routes_still_require_auth(
    webapi_app: WebAPIApp,
) -> None:
    # Rebuild the app with admin auth configured, then verify the admin
    # management routes reject unauthenticated requests while the public
    # pairing/complete endpoint still accepts a valid pairing token.
    from nahida_bot.core.config import (
        Settings,
        WebAPIConfigModel,
        WebUIAuthConfigModel,
        WebUIConfigModel,
    )

    settings = Settings(
        app_name="Test WebAPI",
        debug=True,
        db_path=":memory:",
        plugin_paths=[],
        discover_builtin_channels=False,
        webapi=WebAPIConfigModel(auth_token="admin-secret"),
        webui=WebUIConfigModel(auth=WebUIAuthConfigModel()),
    )
    mock = _make_mock_app()
    mock.settings = settings
    secured = WebAPIApp(
        application=mock,
        host="127.0.0.1",
        port=6185,
        auth_token="admin-secret",
    )
    transport = ASGITransport(app=secured.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        unauth_list = await c.get("/api/nodes")
        assert unauth_list.status_code == 401

        unauth_start = await c.post(
            "/api/nodes/pairing/start",
            json={"node_id": "desktop-local"},
        )
        assert unauth_start.status_code == 401

        # Public pairing/complete still works without admin auth.
        pairing_token, _ = await secured.node_auth.issue_pairing_token(  # type: ignore[attr-defined]
            node_id="desktop-local"
        )
        complete = await c.post(
            "/api/nodes/pairing/complete",
            json={"pairing_token": pairing_token},
        )
        assert complete.status_code == 200
        assert complete.json()["node_token"].startswith("nt_")


async def test_full_pairing_dance_matches_desktop_flow(
    webapi_app: WebAPIApp,
) -> None:
    """Mirror the Desktop `pairDevice` orchestrator against a no-auth gateway.

    Bootstrap reports auth not required -> pairing/start works without a
    bearer -> pairing/complete consumes the pairing token -> node token
    verifies and works against /api/nodes/ws.
    """
    transport = ASGITransport(app=webapi_app.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        bootstrap = await c.get("/api/webui/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["auth"]["required"] is False

        start = await c.post(
            "/api/nodes/pairing/start",
            json={
                "node_id": "desktop-local",
                "display_name": "Nahida Desktop",
            },
        )
        assert start.status_code == 200
        pairing_token = start.json()["pairing_token"]
        assert pairing_token.startswith("np_")

        complete = await c.post(
            "/api/nodes/pairing/complete",
            json={"pairing_token": pairing_token},
        )
        assert complete.status_code == 200
        node_token = complete.json()["node_token"]
        assert node_token.startswith("nt_")

        auth: NodeAuthService = webapi_app.node_auth  # type: ignore[assignment]
        principal = await auth.verify(node_token)
        assert principal is not None
        assert principal.token_type == "node"


async def test_pairing_dance_with_admin_bearer(webapi_app: WebAPIApp) -> None:
    """Mirror the Desktop `pairDevice` orchestrator against an auth-gated gateway."""
    from nahida_bot.core.config import (
        Settings,
        WebAPIConfigModel,
        WebUIAuthConfigModel,
        WebUIConfigModel,
    )

    settings = Settings(
        app_name="Test WebAPI",
        debug=True,
        db_path=":memory:",
        plugin_paths=[],
        discover_builtin_channels=False,
        webapi=WebAPIConfigModel(auth_token="admin-secret"),
        webui=WebUIConfigModel(auth=WebUIAuthConfigModel()),
    )
    mock = _make_mock_app()
    mock.settings = settings
    secured = WebAPIApp(
        application=mock,
        host="127.0.0.1",
        port=6185,
        auth_token="admin-secret",
    )
    transport = ASGITransport(app=secured.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        bootstrap = await c.get("/api/webui/bootstrap")
        assert bootstrap.json()["auth"]["required"] is True
        assert bootstrap.json()["auth"]["mode"] == "bearer"

        # pairing/start without bearer is rejected.
        unauth = await c.post(
            "/api/nodes/pairing/start",
            json={"node_id": "desktop-local"},
        )
        assert unauth.status_code == 401

        # pairing/start with the right bearer mints the one-shot token.
        start = await c.post(
            "/api/nodes/pairing/start",
            headers={"Authorization": "Bearer admin-secret"},
            json={"node_id": "desktop-local"},
        )
        assert start.status_code == 200
        pairing_token = start.json()["pairing_token"]

        # pairing/complete remains public.
        complete = await c.post(
            "/api/nodes/pairing/complete",
            json={"pairing_token": pairing_token},
        )
        assert complete.status_code == 200
        assert complete.json()["node_token"].startswith("nt_")


async def test_pairing_binds_actor_account_and_conversation(
    webapi_app: WebAPIApp,
) -> None:
    """The Desktop pairDevice dance must persist actor/conversation bindings.

    Without an actor_account_key the node input sink refuses message submit
    (`node credential is not bound to an actor account`). This test mirrors
    the Desktop flow: pair with an explicit actor + independent desktop
    conversation lane, then verify the issued node token principal carries
    both through.
    """
    transport = ASGITransport(app=webapi_app.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        start = await c.post(
            "/api/nodes/pairing/start",
            json={
                "node_id": "desktop-local",
                "display_name": "Nahida Desktop",
                "actor_account_key": "telegram:user:12345",
                "conversation_id": "desktop:private:desktop-local",
            },
        )
        assert start.status_code == 200, start.text
        pairing_token = start.json()["pairing_token"]

        complete = await c.post(
            "/api/nodes/pairing/complete",
            json={"pairing_token": pairing_token},
        )
        assert complete.status_code == 200, complete.text
        body = complete.json()
        assert body["node_token"].startswith("nt_")
        # Server echoes the bindings back so the desktop can show them.
        assert body["actor_account_key"] == "telegram:user:12345"
        assert body["conversation_id"] == "desktop:private:desktop-local"

        auth: NodeAuthService = webapi_app.node_auth  # type: ignore[assignment]
        principal = await auth.verify(body["node_token"])
        assert principal is not None
        assert principal.token_type == "node"
        assert principal.actor_account_key == "telegram:user:12345"
        assert principal.conversation_id == "desktop:private:desktop-local"


async def test_pairing_rejects_malformed_actor_account_key(
    webapi_app: WebAPIApp,
) -> None:
    """AccountKey.parse guards against binding a bogus identity."""
    transport = ASGITransport(app=webapi_app.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/nodes/pairing/start",
            json={
                "node_id": "desktop-local",
                "actor_account_key": "not-a-real-account-key",
            },
        )
        assert resp.status_code == 422
        assert "account key" in resp.text.lower()
