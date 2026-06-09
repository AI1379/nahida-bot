"""Tests for plugin-owned webhook endpoint hosting."""

from __future__ import annotations

import pytest

from nahida_bot.gateway.services.webhost import WebHostService
from nahida_bot_sdk import WebhookResponse


@pytest.mark.asyncio
async def test_webhost_dispatches_registered_endpoint() -> None:
    service = WebHostService()
    seen: dict[str, object] = {}

    async def _handler(request):
        seen["method"] = request.method
        seen["path"] = request.path
        seen["headers"] = request.headers
        seen["query"] = request.query
        seen["body"] = request.body
        seen["client_host"] = request.client_host
        return WebhookResponse(status_code=202, body="ok")

    service.register(plugin_id="p1", path="/github/", handler=_handler)

    response = await service.dispatch(
        path="github",
        method="POST",
        headers={"content-type": "application/json"},
        query={"x": "1"},
        body=b"{}",
        client_host="127.0.0.1",
    )

    assert response.status_code == 202
    assert response.body == "ok"
    assert seen == {
        "method": "POST",
        "path": "github",
        "headers": {"content-type": "application/json"},
        "query": {"x": "1"},
        "body": b"{}",
        "client_host": "127.0.0.1",
    }


@pytest.mark.asyncio
async def test_webhost_unknown_and_method_not_allowed() -> None:
    service = WebHostService()

    async def _handler(request):
        return WebhookResponse()

    service.register(plugin_id="p1", path="github", handler=_handler)

    missing = await service.dispatch(
        path="missing",
        method="POST",
        headers={},
        query={},
        body=b"",
    )
    wrong_method = await service.dispatch(
        path="github",
        method="GET",
        headers={},
        query={},
        body=b"",
    )

    assert missing.status_code == 404
    assert wrong_method.status_code == 405
    assert wrong_method.headers["Allow"] == "POST"


@pytest.mark.asyncio
async def test_webhost_empty_path_returns_404() -> None:
    service = WebHostService()

    response = await service.dispatch(
        path="",
        method="POST",
        headers={},
        query={},
        body=b"",
    )

    assert response.status_code == 404


def test_webhost_rejects_duplicate_path_and_unregisters() -> None:
    service = WebHostService()

    async def _handler(request):
        return WebhookResponse()

    handle = service.register(plugin_id="p1", path="github", handler=_handler)
    with pytest.raises(KeyError, match="already registered"):
        service.register(plugin_id="p2", path="github", handler=_handler)

    handle.unsubscribe()
    service.register(plugin_id="p2", path="github", handler=_handler)


def test_webhost_unregister_by_plugin() -> None:
    service = WebHostService()

    async def _handler(request):
        return WebhookResponse()

    service.register(plugin_id="p1", path="a", handler=_handler)
    service.register(plugin_id="p1", path="b", handler=_handler)
    service.register(plugin_id="p2", path="c", handler=_handler)

    assert service.unregister_by_plugin("p1") == 2
    assert service.unregister_by_plugin("p1") == 0
