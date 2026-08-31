"""Tests for authoritative plugin runtime snapshots sent to Nodes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nahida_bot.core.config import Settings
from nahida_bot.core.events import (
    EventBus,
    EventContext,
    PluginEnabled,
    PluginPayload,
)
from nahida_bot.gateway.services.plugin_runtimes import PluginRuntimeService
from nahida_bot.plugins.manager import PluginRecord, PluginState
from nahida_bot.plugins.manifest import PluginManifest


class _PluginRecords:
    def __init__(self, records: list[PluginRecord]) -> None:
        self.records = records

    def list_plugins(self) -> list[PluginRecord]:
        return self.records


class _Nodes:
    def __init__(self, session: Any) -> None:
        self.session = session

    def get_online_session(self, node_id: str) -> Any | None:
        return self.session if node_id == self.session.node_id else None

    def list_online_nodes(self) -> list[dict[str, object]]:
        return [{"node_id": self.session.node_id, "session_id": "session-1"}]

    def get_session(self, session_id: str) -> Any | None:
        return self.session if session_id == "session-1" else None


def _event_bus() -> EventBus:
    return EventBus(
        EventContext(
            app=SimpleNamespace(),  # type: ignore[arg-type]
            settings=Settings(app_name="test"),
            logger=SimpleNamespace(exception=lambda *a, **k: None),
        )
    )


def _desktop_record() -> PluginRecord:
    manifest = PluginManifest.model_validate(
        {
            "id": "example.desktop",
            "name": "Desktop Example",
            "version": "1.2.0",
            "runtimes": {
                "desktop": {
                    "entrypoint": "builtin:example.desktop",
                    "mode": "builtin",
                }
            },
            "contributes": {
                "pages": [
                    {
                        "id": "settings",
                        "target": "desktop.main",
                        "entry": "dist/settings.html",
                    }
                ]
            },
        }
    )
    return PluginRecord(
        manifest=manifest,
        plugin_dir=Path("plugins/example.desktop"),
        state=PluginState.ENABLED,
    )


async def test_sync_node_sends_runtime_facets_without_filesystem_paths() -> None:
    sent = []

    async def send(envelope):
        sent.append(envelope)
        return True

    session = SimpleNamespace(node_id="desktop-1", send=send)
    bus = _event_bus()
    service = PluginRuntimeService(
        _PluginRecords([_desktop_record()]),  # type: ignore[arg-type]
        _Nodes(session),  # type: ignore[arg-type]
        bus,
    )

    assert await service.sync_node("desktop-1") is True

    envelope = sent[0]
    assert envelope.event == "plugin.runtime.sync"
    generation = envelope.payload.pop("generation")
    assert isinstance(generation, str)
    assert len(generation) == 32
    assert envelope.payload == {
        "revision": 1,
        "plugins": [
            {
                "id": "example.desktop",
                "name": "Desktop Example",
                "version": "1.2.0",
                "state": "enabled",
                "configured_enabled": True,
                "runtimes": {
                    "desktop": {
                        "entrypoint": "builtin:example.desktop",
                        "mode": "builtin",
                    }
                },
                "contributes": {
                    "desktop_surfaces": [],
                    "pages": [
                        {
                            "id": "settings",
                            "target": "desktop.main",
                            "entry": "dist/settings.html",
                            "title": "",
                        }
                    ],
                },
            }
        ],
    }
    assert "path" not in envelope.payload["plugins"][0]


async def test_lifecycle_event_pushes_a_fresh_snapshot() -> None:
    sent = []

    async def send(envelope):
        sent.append(envelope)
        return True

    record = _desktop_record()
    session = SimpleNamespace(node_id="desktop-1", send=send)
    bus = _event_bus()
    service = PluginRuntimeService(
        _PluginRecords([record]),  # type: ignore[arg-type]
        _Nodes(session),  # type: ignore[arg-type]
        bus,
    )
    service.start()

    await bus.publish(
        PluginEnabled(
            payload=PluginPayload(
                plugin_id=record.manifest.id,
                plugin_name=record.manifest.name,
                plugin_version=record.manifest.version,
            )
        )
    )

    assert len(sent) == 1
    assert sent[0].payload["revision"] == 1
    service.stop()
