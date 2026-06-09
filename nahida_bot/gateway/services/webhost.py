"""Plugin-owned webhook endpoint registry for the WebAPI host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

import structlog

from nahida_bot_sdk import WebhookRequest, WebhookResponse

logger = structlog.get_logger(__name__)

WebhookHandler = Callable[[WebhookRequest], Awaitable[WebhookResponse | None]]


@dataclass(slots=True)
class _WebhookEndpoint:
    plugin_id: str
    path: str
    methods: frozenset[str]
    handler: WebhookHandler


class WebhookEndpointHandle:
    """Unsubscribe handle for one plugin-owned webhook endpoint."""

    def __init__(self, service: "WebHostService", plugin_id: str, path: str) -> None:
        self._service = service
        self._plugin_id = plugin_id
        self._path = path
        self._unsubscribed = False

    def unsubscribe(self) -> None:
        if self._unsubscribed:
            return
        self._service.unregister(self._plugin_id, self._path)
        self._unsubscribed = True


class WebHostService:
    """Runtime registry for plugin webhook endpoints.

    FastAPI mounts a single catch-all ``/webhooks/{path:path}`` route and
    delegates lookup to this service.  This keeps plugin disable/reload cheap:
    endpoint lifecycles only mutate this registry, not FastAPI's route table.
    """

    def __init__(self) -> None:
        self._endpoints: dict[str, _WebhookEndpoint] = {}

    def register(
        self,
        *,
        plugin_id: str,
        path: str,
        handler: WebhookHandler,
        methods: tuple[str, ...] = ("POST",),
    ) -> WebhookEndpointHandle:
        normalized_path = _normalize_path(path)
        normalized_methods = frozenset(_normalize_method(method) for method in methods)
        if not normalized_methods:
            raise ValueError("Webhook endpoint methods must not be empty")
        existing = self._endpoints.get(normalized_path)
        if existing is not None:
            raise KeyError(
                f"Webhook endpoint '{normalized_path}' is already registered by "
                f"plugin '{existing.plugin_id}'"
            )
        self._endpoints[normalized_path] = _WebhookEndpoint(
            plugin_id=plugin_id,
            path=normalized_path,
            methods=normalized_methods,
            handler=handler,
        )
        logger.debug(
            "webhost.endpoint_registered",
            plugin_id=plugin_id,
            path=normalized_path,
            methods=sorted(normalized_methods),
        )
        return WebhookEndpointHandle(self, plugin_id, normalized_path)

    def unregister(self, plugin_id: str, path: str) -> bool:
        normalized_path = _normalize_path(path)
        endpoint = self._endpoints.get(normalized_path)
        if endpoint is None or endpoint.plugin_id != plugin_id:
            return False
        self._endpoints.pop(normalized_path, None)
        logger.debug(
            "webhost.endpoint_unregistered",
            plugin_id=plugin_id,
            path=normalized_path,
        )
        return True

    def unregister_by_plugin(self, plugin_id: str) -> int:
        removed = 0
        for path, endpoint in list(self._endpoints.items()):
            if endpoint.plugin_id != plugin_id:
                continue
            self._endpoints.pop(path, None)
            removed += 1
        if removed:
            logger.debug(
                "webhost.endpoints_unregistered_by_plugin",
                plugin_id=plugin_id,
                count=removed,
            )
        return removed

    async def dispatch(
        self,
        *,
        path: str,
        method: str,
        headers: dict[str, str],
        query: dict[str, str],
        body: bytes,
        client_host: str = "",
    ) -> WebhookResponse:
        try:
            normalized_path = _normalize_path(path)
        except ValueError:
            return WebhookResponse(status_code=404, body="Webhook endpoint not found")
        endpoint = self._endpoints.get(normalized_path)
        if endpoint is None:
            return WebhookResponse(status_code=404, body="Webhook endpoint not found")

        normalized_method = _normalize_method(method)
        if normalized_method not in endpoint.methods:
            return WebhookResponse(
                status_code=405,
                body="Method not allowed",
                headers={"Allow": ", ".join(sorted(endpoint.methods))},
            )

        request = WebhookRequest(
            method=normalized_method,
            path=normalized_path,
            headers=headers,
            query=query,
            body=body,
            client_host=client_host,
        )
        try:
            response = await endpoint.handler(request)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "webhost.endpoint_failed",
                plugin_id=endpoint.plugin_id,
                path=normalized_path,
                method=normalized_method,
                error=str(exc),
            )
            return WebhookResponse(status_code=500, body="Webhook handler failed")
        return response or WebhookResponse()


def _normalize_path(path: str) -> str:
    normalized = str(path or "").strip().strip("/")
    if not normalized:
        raise ValueError("Webhook endpoint path must not be empty")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Invalid webhook endpoint path: {path!r}")
    return "/".join(parts)


def _normalize_method(method: str) -> str:
    normalized = str(method or "").strip().upper()
    if not normalized:
        raise ValueError("Webhook endpoint method must not be empty")
    return normalized
