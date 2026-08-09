"""Tests for actor-bound Desktop control capabilities."""

from __future__ import annotations

from typing import Any

import pytest

from nahida_bot.gateway.node_protocol.schemas import (
    NodeCapability,
    NodeEnvelope,
    build_response,
)
from nahida_bot.gateway.node_protocol.sessions import NodeSession
from nahida_bot.gateway.services.desktop_control import (
    DESKTOP_EXEC_CAPABILITY,
    DESKTOP_FILE_READ_CAPABILITY,
    MAX_DESKTOP_EXEC_ARGS,
    MAX_DESKTOP_EXEC_ARG_CHARS,
    MAX_DESKTOP_FILE_READ_BYTES,
    MAX_DESKTOP_PATH_CHARS,
    DesktopControlService,
)
from nahida_bot.gateway.services.node_invoker import NodeInvoker
from nahida_bot.gateway.services.node_registry import NodeRegistry


def _register(
    registry: NodeRegistry,
    *,
    node_id: str,
    actor: str,
    conversation: str,
    capabilities: list[str],
    calls: list[tuple[str, NodeEnvelope]],
    node_type: str = "desktop",
) -> None:
    session = NodeSession(
        session_id="pending",
        node_id=node_id,
        actor_account_key=actor,
        conversation_id=conversation,
    )

    async def request(envelope: NodeEnvelope, timeout: float) -> NodeEnvelope:
        calls.append((node_id, envelope))
        return build_response(
            envelope.id or "", ok=True, payload={"stdout": "ok", "exit_code": 0}
        )

    session.request = request
    registry.register_session(
        session,
        node_id=node_id,
        display_name=node_id,
        node_type=node_type,
        capabilities=[NodeCapability(name=name) for name in capabilities],
        metadata={},
    )


@pytest.mark.asyncio
async def test_exec_prefers_exact_conversation_and_injects_trusted_actor() -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    for node_id, conversation in (
        ("fallback", "desktop:private:owner"),
        ("exact", "milky:private:owner"),
    ):
        _register(
            registry,
            node_id=node_id,
            actor="milky:user:owner",
            conversation=conversation,
            capabilities=[DESKTOP_EXEC_CAPABILITY],
            calls=calls,
        )
    service = DesktopControlService(registry, NodeInvoker(registry))

    result = await service.exec(
        profile_id="git",
        args=["status", "--short"],
        cwd_relative="repo",
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )

    assert result.ok is True
    assert result.node_id == "exact"
    assert calls[0][0] == "exact"
    assert calls[0][1].payload is not None
    assert calls[0][1].payload["capability"] == DESKTOP_EXEC_CAPABILITY
    assert calls[0][1].payload["arguments"] == {
        "profileId": "git",
        "args": ["status", "--short"],
        "cwdRelative": "repo",
        "actorAccountKey": "milky:user:owner",
    }


@pytest.mark.asyncio
async def test_control_never_uses_exact_conversation_bound_to_another_actor() -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    _register(
        registry,
        node_id="other",
        actor="milky:user:other",
        conversation="milky:private:shared",
        capabilities=[DESKTOP_EXEC_CAPABILITY],
        calls=calls,
    )
    _register(
        registry,
        node_id="owner",
        actor="milky:user:owner",
        conversation="desktop:private:owner",
        capabilities=[DESKTOP_EXEC_CAPABILITY],
        calls=calls,
    )
    service = DesktopControlService(registry, NodeInvoker(registry))

    result = await service.exec(
        profile_id="safe",
        args=[],
        cwd_relative="",
        conversation_id="milky:private:shared",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )

    assert result.ok is True
    assert result.node_id == "owner"
    assert [node_id for node_id, _ in calls] == ["owner"]


@pytest.mark.asyncio
async def test_file_read_maps_only_fixed_arguments_to_declared_capability() -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    _register(
        registry,
        node_id="owner",
        actor="milky:user:owner",
        conversation="milky:private:owner",
        capabilities=[DESKTOP_FILE_READ_CAPABILITY],
        calls=calls,
    )
    service = DesktopControlService(registry, NodeInvoker(registry))

    result = await service.file_read(
        root_id="documents",
        relative_path="notes/today.txt",
        offset=20,
        max_bytes=1024,
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )

    assert result.ok is True
    assert calls[0][1].payload is not None
    assert calls[0][1].payload["capability"] == DESKTOP_FILE_READ_CAPABILITY
    assert calls[0][1].payload["arguments"] == {
        "rootId": "documents",
        "relativePath": "notes/today.txt",
        "offset": 20,
        "maxBytes": 1024,
        "actorAccountKey": "milky:user:owner",
    }


@pytest.mark.asyncio
async def test_file_read_fails_closed_for_ambiguous_actor() -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    for node_id in ("desktop-1", "desktop-2"):
        _register(
            registry,
            node_id=node_id,
            actor="milky:user:owner",
            conversation=f"desktop:private:{node_id}",
            capabilities=[DESKTOP_FILE_READ_CAPABILITY],
            calls=calls,
        )
    service = DesktopControlService(registry, NodeInvoker(registry))

    result = await service.file_read(
        root_id="documents",
        relative_path="notes/today.txt",
        offset=0,
        max_bytes=1024,
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:cron:test",
    )

    assert result.ok is False
    assert result.error_code == "ambiguous_desktop"
    assert calls == []


@pytest.mark.asyncio
async def test_control_requires_actor_and_desktop_node_type() -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    _register(
        registry,
        node_id="worker",
        actor="milky:user:owner",
        conversation="milky:private:owner",
        capabilities=[DESKTOP_EXEC_CAPABILITY],
        calls=calls,
        node_type="worker",
    )
    service = DesktopControlService(registry, NodeInvoker(registry))

    no_actor = await service.exec(
        profile_id="safe",
        args=[],
        cwd_relative="",
        conversation_id="milky:private:owner",
        actor_account_key="",
        caller="agent:chat:test",
    )
    wrong_type = await service.exec(
        profile_id="safe",
        args=[],
        cwd_relative="",
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )

    assert no_actor.error_code == "actor_unavailable"
    assert wrong_type.error_code == "desktop_unavailable"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"profile_id": "", "args": [], "cwd_relative": ""}, "profile_id"),
        (
            {
                "profile_id": "safe",
                "args": ["x"] * (MAX_DESKTOP_EXEC_ARGS + 1),
                "cwd_relative": "",
            },
            "args exceeds",
        ),
        (
            {
                "profile_id": "safe",
                "args": ["x" * (MAX_DESKTOP_EXEC_ARG_CHARS + 1)],
                "cwd_relative": "",
            },
            "an arg exceeds",
        ),
        (
            {"profile_id": "safe", "args": [], "cwd_relative": "../outside"},
            "configured root",
        ),
        (
            {"profile_id": "safe", "args": [], "cwd_relative": "C:\\outside"},
            "must be relative",
        ),
    ],
)
async def test_exec_rejects_invalid_arguments(
    kwargs: dict[str, Any], message: str
) -> None:
    service = DesktopControlService(NodeRegistry(), NodeInvoker(NodeRegistry()))
    result = await service.exec(
        **kwargs,
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )
    assert result.error_code == "invalid_arguments"
    assert message in result.error_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("relative_path", "offset", "max_bytes"),
    [
        ("", 0, 1),
        ("x" * (MAX_DESKTOP_PATH_CHARS + 1), 0, 1),
        ("../secret", 0, 1),
        ("/etc/passwd", 0, 1),
        ("file.txt", -1, 1),
        ("file.txt", 0, 0),
        ("file.txt", 0, MAX_DESKTOP_FILE_READ_BYTES + 1),
    ],
)
async def test_file_read_rejects_invalid_arguments(
    relative_path: str, offset: int, max_bytes: int
) -> None:
    registry = NodeRegistry()
    service = DesktopControlService(registry, NodeInvoker(registry))
    result = await service.file_read(
        root_id="documents",
        relative_path=relative_path,
        offset=offset,
        max_bytes=max_bytes,
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )
    assert result.error_code == "invalid_arguments"
