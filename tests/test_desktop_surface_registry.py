"""Tests for plugin-owned Desktop surface provider isolation."""

from __future__ import annotations

import asyncio

import pytest

from nahida_bot.plugins.desktop_surfaces import (
    DesktopSurfaceProviderEntry,
    DesktopSurfaceRegistry,
)
from nahida_bot_sdk.desktop import DesktopSurfaceContext, DesktopSurfaceView


def _entry(
    plugin_id: str,
    surface_id: str,
    *,
    priority: int,
    handler: object,
) -> DesktopSurfaceProviderEntry:
    return DesktopSurfaceProviderEntry(
        plugin_id=plugin_id,
        surface_id=surface_id,
        target="desktop.home",
        kind="card",
        priority=priority,
        handler=handler,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_collects_valid_surfaces_in_stable_priority_order() -> None:
    registry = DesktopSurfaceRegistry()

    async def lower(context: DesktopSurfaceContext) -> DesktopSurfaceView:
        return DesktopSurfaceView(title=f"Lower for {context.display_name}")

    async def higher(_: DesktopSurfaceContext) -> dict[str, str]:
        return {"title": "Higher", "tone": "info"}

    registry.register(_entry("example.lower", "card", priority=1, handler=lower))
    registry.register(_entry("example.higher", "card", priority=20, handler=higher))

    surfaces = await registry.collect(
        DesktopSurfaceContext(node_id="desktop-1", display_name="Study PC")
    )

    assert [surface.owner_plugin_id for surface in surfaces] == [
        "example.higher",
        "example.lower",
    ]
    assert surfaces[1].view.title == "Lower for Study PC"


@pytest.mark.asyncio
async def test_provider_failure_timeout_and_invalid_result_are_isolated() -> None:
    registry = DesktopSurfaceRegistry(provider_timeout_seconds=0.01)

    async def broken(_: DesktopSurfaceContext) -> DesktopSurfaceView:
        raise RuntimeError("broken")

    async def slow(_: DesktopSurfaceContext) -> DesktopSurfaceView:
        await asyncio.sleep(1)
        return DesktopSurfaceView(title="Too late")

    async def invalid(_: DesktopSurfaceContext) -> dict[str, float]:
        return {"progress": 2.0}

    async def valid(_: DesktopSurfaceContext) -> DesktopSurfaceView:
        return DesktopSurfaceView(title="Valid view, invalid owner")

    registry.register(_entry("example.broken", "card", priority=1, handler=broken))
    registry.register(_entry("example.slow", "card", priority=1, handler=slow))
    registry.register(_entry("example.invalid", "card", priority=1, handler=invalid))
    registry.register(_entry("invalid/plugin", "card", priority=1, handler=valid))

    assert await registry.collect(DesktopSurfaceContext(node_id="desktop-1")) == []


def test_registry_owns_identity_and_notifies_only_on_changes() -> None:
    registry = DesktopSurfaceRegistry()
    changes = 0

    def changed() -> None:
        nonlocal changes
        changes += 1

    async def provider(_: DesktopSurfaceContext) -> DesktopSurfaceView:
        return DesktopSurfaceView(title="Today")

    registry.set_change_callback(changed)
    registry.register(_entry("example.schedule", "today", priority=0, handler=provider))
    assert registry.refresh("example.schedule", "today") is True
    assert registry.refresh("example.schedule", "missing") is False

    with pytest.raises(KeyError, match="already registered"):
        registry.register(
            _entry("example.schedule", "today", priority=0, handler=provider)
        )

    assert registry.unregister("example.schedule", "missing") is False
    assert registry.unregister("example.schedule", "today") is True
    assert changes == 3
