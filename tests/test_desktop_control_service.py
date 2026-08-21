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
    DESKTOP_INPUT_CAPABILITY,
    DESKTOP_SCREENSHOT_CAPABILITY,
    MAX_DESKTOP_EXEC_ARGS,
    MAX_DESKTOP_EXEC_ARG_CHARS,
    MAX_DESKTOP_FILE_READ_BYTES,
    MAX_DESKTOP_PATH_CHARS,
    MAX_DESKTOP_PROGRAM_CHARS,
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
async def test_exec_sends_unified_payload_and_injects_trusted_actor() -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    _register(
        registry,
        node_id="owner",
        actor="milky:user:owner",
        conversation="milky:private:owner",
        capabilities=[DESKTOP_EXEC_CAPABILITY],
        calls=calls,
    )
    service = DesktopControlService(registry, NodeInvoker(registry))

    result = await service.exec(
        program="git",
        args=["status", "--short"],
        cwd="repo",
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )

    assert result.ok is True
    assert result.node_id == "owner"
    assert calls[0][0] == "owner"
    assert calls[0][1].payload is not None
    assert calls[0][1].payload["capability"] == DESKTOP_EXEC_CAPABILITY
    assert calls[0][1].payload["arguments"] == {
        "program": "git",
        "args": ["status", "--short"],
        "cwd": "repo",
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
        program="safe",
        args=[],
        cwd="",
        conversation_id="milky:private:shared",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )

    assert result.ok is True
    assert result.node_id == "owner"
    assert [node_id for node_id, _ in calls] == ["owner"]


@pytest.mark.asyncio
async def test_file_read_sends_unified_payload_to_declared_capability() -> None:
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
        path="notes/today.txt",
        root_id="documents",
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
        "path": "notes/today.txt",
        "rootId": "documents",
        "offset": 20,
        "maxBytes": 1024,
        "actorAccountKey": "milky:user:owner",
    }


@pytest.mark.asyncio
async def test_computer_use_sends_actor_bound_screenshot_and_input() -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    _register(
        registry,
        node_id="owner",
        actor="milky:user:owner",
        conversation="milky:private:owner",
        capabilities=[DESKTOP_SCREENSHOT_CAPABILITY, DESKTOP_INPUT_CAPABILITY],
        calls=calls,
    )
    service = DesktopControlService(registry, NodeInvoker(registry))

    screenshot = await service.screenshot(
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )
    input_result = await service.input(
        action="click",
        x=500,
        y=250,
        button="left",
        clicks=1,
        scroll_steps=0,
        text="",
        keys=[],
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )

    assert screenshot.ok is True
    assert input_result.ok is True
    assert calls[0][1].payload is not None
    assert calls[0][1].payload["capability"] == DESKTOP_SCREENSHOT_CAPABILITY
    assert calls[0][1].payload["arguments"] == {"actorAccountKey": "milky:user:owner"}
    assert calls[1][1].payload is not None
    assert calls[1][1].payload["capability"] == DESKTOP_INPUT_CAPABILITY
    assert calls[1][1].payload["arguments"] == {
        "action": "click",
        "x": 500,
        "y": 250,
        "button": "left",
        "clicks": 1,
        "scrollSteps": 0,
        "text": "",
        "keys": [],
        "actorAccountKey": "milky:user:owner",
    }


@pytest.mark.asyncio
async def test_computer_input_rejects_invalid_normalized_coordinates() -> None:
    registry = NodeRegistry()
    service = DesktopControlService(registry, NodeInvoker(registry))
    result = await service.input(
        action="click",
        x=1001,
        y=0,
        button="left",
        clicks=1,
        scroll_steps=0,
        text="",
        keys=[],
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )
    assert result.error_code == "invalid_arguments"
    assert "x must be between" in result.error_message


@pytest.mark.asyncio
async def test_file_read_prefers_exact_conversation_for_same_actor() -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    for node_id, conversation in (
        ("desktop-1", "milky:private:owner"),
        ("desktop-2", "desktop:private:desktop-2"),
    ):
        _register(
            registry,
            node_id=node_id,
            actor="milky:user:owner",
            conversation=conversation,
            capabilities=[DESKTOP_FILE_READ_CAPABILITY],
            calls=calls,
        )
    service = DesktopControlService(registry, NodeInvoker(registry))

    result = await service.file_read(
        path="notes/today.txt",
        root_id="documents",
        offset=0,
        max_bytes=1024,
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:cron:test",
    )

    assert result.ok is True
    assert result.node_id == "desktop-1"
    assert [node_id for node_id, _ in calls] == ["desktop-1"]


@pytest.mark.asyncio
async def test_file_read_fails_closed_for_ambiguous_actor_fallback() -> None:
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
        path="notes/today.txt",
        root_id="documents",
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
async def test_control_requires_actor_desktop_node_type_and_capability() -> None:
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
    _register(
        registry,
        node_id="desktop-without-capability",
        actor="milky:user:owner",
        conversation="milky:private:owner",
        capabilities=[DESKTOP_FILE_READ_CAPABILITY],
        calls=calls,
    )
    service = DesktopControlService(registry, NodeInvoker(registry))

    no_actor = await service.exec(
        program="safe",
        args=[],
        cwd="",
        conversation_id="milky:private:owner",
        actor_account_key="",
        caller="agent:chat:test",
    )
    wrong_type = await service.exec(
        program="safe",
        args=[],
        cwd="",
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
        ({"program": "", "args": [], "cwd": ""}, "program"),
        (
            {
                "program": "safe",
                "args": ["x"] * (MAX_DESKTOP_EXEC_ARGS + 1),
                "cwd": "",
            },
            "args exceeds",
        ),
        (
            {
                "program": "safe",
                "args": ["x" * (MAX_DESKTOP_EXEC_ARG_CHARS + 1)],
                "cwd": "",
            },
            "an arg exceeds",
        ),
        (
            {"program": "x" * (MAX_DESKTOP_PROGRAM_CHARS + 1), "args": [], "cwd": ""},
            "program exceeds",
        ),
        (
            {"program": "safe", "args": ["bad\x00arg"], "cwd": ""},
            "NUL",
        ),
        (
            {"program": "safe", "args": [], "cwd": "bad\x00cwd"},
            "NUL",
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
    ("path", "root_id", "offset", "max_bytes"),
    [
        ("", "", 0, 1),
        ("x" * (MAX_DESKTOP_PATH_CHARS + 1), "", 0, 1),
        ("bad\x00path", "", 0, 1),
        ("file.txt", "bad\x00root", 0, 1),
        ("file.txt", "", -1, 1),
        ("file.txt", "", 0, 0),
        ("file.txt", "", 0, MAX_DESKTOP_FILE_READ_BYTES + 1),
    ],
)
async def test_file_read_rejects_invalid_arguments(
    path: str, root_id: str, offset: int, max_bytes: int
) -> None:
    registry = NodeRegistry()
    service = DesktopControlService(registry, NodeInvoker(registry))
    result = await service.file_read(
        path=path,
        root_id=root_id,
        offset=offset,
        max_bytes=max_bytes,
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )
    assert result.error_code == "invalid_arguments"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("program", "cwd", "path"),
    [
        (r"C:\Windows\System32\cmd.exe", r"C:\work", r"C:\secret.txt"),
        ("../bin/tool", "../work", "../secret.txt"),
    ],
)
async def test_absolute_and_parent_paths_are_forwarded_for_desktop_mode_policy(
    program: str, cwd: str, path: str
) -> None:
    registry = NodeRegistry()
    calls: list[tuple[str, NodeEnvelope]] = []
    _register(
        registry,
        node_id="owner",
        actor="milky:user:owner",
        conversation="desktop:private:owner",
        capabilities=[DESKTOP_EXEC_CAPABILITY, DESKTOP_FILE_READ_CAPABILITY],
        calls=calls,
    )
    service = DesktopControlService(registry, NodeInvoker(registry))

    exec_result = await service.exec(
        program=program,
        args=[],
        cwd=cwd,
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )
    read_result = await service.file_read(
        path=path,
        root_id="",
        offset=0,
        max_bytes=65536,
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:chat:test",
    )

    assert exec_result.ok is True
    assert read_result.ok is True
    assert calls[0][1].payload is not None
    assert calls[0][1].payload["arguments"]["program"] == program
    assert calls[0][1].payload["arguments"]["cwd"] == cwd
    assert calls[1][1].payload is not None
    assert calls[1][1].payload["arguments"]["path"] == path
