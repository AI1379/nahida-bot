"""Tests for Gateway desired-state delivery of Desktop plugin surfaces."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nahida_bot.gateway.services.desktop_surfaces import DesktopSurfaceService
from nahida_bot.gateway.services.node_invoker import InvokeResult
from nahida_bot.plugins.desktop_surfaces import (
    DesktopSurfaceProviderEntry,
    DesktopSurfaceRegistry,
)
from nahida_bot_sdk.desktop import DesktopSurfaceContext, DesktopSurfaceView


class _Nodes:
    def __init__(self, session: Any | None) -> None:
        self.session = session

    def get_online_session(self, node_id: str) -> Any | None:
        return (
            self.session if self.session and self.session.node_id == node_id else None
        )

    def list_online_nodes(self) -> list[dict[str, object]]:
        if self.session is None:
            return []
        return [{"node_id": self.session.node_id, "node_type": self.session.node_type}]


class _Invoker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, **kwargs: Any) -> InvokeResult:
        self.calls.append(kwargs)
        return InvokeResult(ok=True, payload={"applied": True})


def _desktop_session(*, supports_surfaces: bool = True) -> Any:
    return SimpleNamespace(
        node_id="desktop-1",
        display_name="Study PC",
        node_type="desktop",
        metadata={"platform": "windows"},
        get_capability=lambda name: (
            object() if supports_surfaces and name == "desktop.surface.sync" else None
        ),
    )


@pytest.mark.asyncio
async def test_sync_node_sends_owner_stamped_snapshot() -> None:
    registry = DesktopSurfaceRegistry()

    async def today(context: DesktopSurfaceContext) -> DesktopSurfaceView:
        assert context.metadata == {"platform": "windows"}
        return DesktopSurfaceView(
            title="今日安排",
            items=[{"text": "整理插件协议", "detail": "上午"}],
        )

    registry.register(
        DesktopSurfaceProviderEntry(
            plugin_id="example.schedule",
            surface_id="today",
            target="desktop.home",
            kind="list",
            priority=25,
            handler=today,
        )
    )
    invoker = _Invoker()
    service = DesktopSurfaceService(
        registry,
        _Nodes(_desktop_session()),  # type: ignore[arg-type]
        invoker,  # type: ignore[arg-type]
    )

    assert await service.sync_node("desktop-1") is True

    assert invoker.calls[0]["capability"] == "desktop.surface.sync"
    assert invoker.calls[0]["node_id"] == "desktop-1"
    assert invoker.calls[0]["arguments"] == {
        "revision": 1,
        "surfaces": [
            {
                "owner_plugin_id": "example.schedule",
                "id": "today",
                "target": "desktop.home",
                "kind": "list",
                "priority": 25,
                "view": {
                    "title": "今日安排",
                    "text": "",
                    "status": "",
                    "detail": "",
                    "expires_at": "",
                    "progress": None,
                    "items": [
                        {
                            "text": "整理插件协议",
                            "detail": "上午",
                            "completed": False,
                        }
                    ],
                    "tone": "neutral",
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_sync_skips_nodes_without_surface_capability() -> None:
    invoker = _Invoker()
    service = DesktopSurfaceService(
        DesktopSurfaceRegistry(),
        _Nodes(_desktop_session(supports_surfaces=False)),  # type: ignore[arg-type]
        invoker,  # type: ignore[arg-type]
    )

    assert await service.sync_node("desktop-1") is False
    assert invoker.calls == []
