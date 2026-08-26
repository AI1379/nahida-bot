"""Tests for executing plugin-registered tools from the agent loop."""

from __future__ import annotations

import json
from typing import Any

import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers import ChatProvider, ProviderResponse, ToolCall
from nahida_bot.agent.tokenization import CharacterEstimateTokenizer
from nahida_bot.identity.authorization import (
    TOOL_SCOPE_CHAT_DOMAIN,
    AuthorizationGate,
    ChatDomainIndex,
)
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry
from nahida_bot.plugins.tool_executor import RegistryToolExecutor


class _ToolCallingProvider(ChatProvider):
    name = "tool-calling-provider"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def tokenizer(self):
        return None

    async def _chat_impl(
        self, *, messages, tools=None, timeout_seconds=None, model=None
    ):  # noqa: ANN001
        self.calls += 1
        if self.calls == 1:
            assert tools is not None
            assert tools[0].name == "echo"
            return ProviderResponse(
                content=None,
                tool_calls=[
                    ToolCall(call_id="tc_1", name="echo", arguments={"text": "hi"})
                ],
            )

        tool_payload = json.loads(messages[-1].content)
        return ProviderResponse(
            content=f"tool said {tool_payload['output']}",
            tool_calls=[],
        )


@pytest.mark.asyncio
async def test_registry_tool_executor_completes_agent_tool_roundtrip() -> None:
    async def echo(text: str) -> str:
        return text.upper()

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="echo",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo,
            plugin_id="echo-plugin",
        )
    )
    executor = RegistryToolExecutor(registry)
    provider = _ToolCallingProvider()
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=executor,
    )

    result = await loop.run(
        user_message="call echo",
        system_prompt="sys",
        tools=executor.definitions(),
    )

    assert result.final_response == "tool said HI"
    assert result.steps == 2


@pytest.mark.asyncio
async def test_registry_tool_executor_reports_missing_tool() -> None:
    executor = RegistryToolExecutor(ToolRegistry())

    result = await executor.execute(
        ToolCall(call_id="tc_missing", name="missing", arguments={})
    )

    assert result.is_error is True
    assert result.error_code == "tool_not_registered"


def test_registry_tool_executor_exposes_admin_requirement() -> None:
    async def admin_action() -> str:
        return "ok"

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="admin_action",
            description="Admin action",
            parameters={"type": "object"},
            handler=admin_action,
            plugin_id="admin-plugin",
            requires_admin=True,
        )
    )
    executor = RegistryToolExecutor(registry)

    assert executor.tool_requires_admin("admin_action") is True
    assert executor.tool_requires_admin("missing") is False


def test_registry_tool_executor_exposes_scope_mode() -> None:
    async def scoped_lookup() -> str:
        return "ok"

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="scoped_lookup",
            description="Scoped lookup",
            parameters={"type": "object"},
            handler=scoped_lookup,
            plugin_id="scoped-plugin",
            scope=TOOL_SCOPE_CHAT_DOMAIN,
        )
    )
    executor = RegistryToolExecutor(registry)

    assert executor.tool_scope("scoped_lookup") == TOOL_SCOPE_CHAT_DOMAIN
    assert executor.tool_scope("missing") == ""


@pytest.mark.asyncio
async def test_agent_loop_enforces_registry_admin_requirement() -> None:
    executed = False

    async def admin_action() -> str:
        nonlocal executed
        executed = True
        return "ok"

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="plugin_admin_action",
            description="Admin action",
            parameters={"type": "object"},
            handler=admin_action,
            plugin_id="admin-plugin",
            requires_admin=True,
        )
    )
    executor = RegistryToolExecutor(registry)
    loop = AgentLoop(
        provider=_ToolCallingProvider(),
        context_builder=ContextBuilder(
            budget=ContextBudget(max_tokens=300, reserved_tokens=0),
            fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
        ),
        tool_executor=executor,
        authorization=AuthorizationGate(
            frozenset({"milky:admin"}),
            enabled=True,
        ),
    )

    result, _, phase = await loop._execute_tool_with_lifecycle(
        ToolCall(
            call_id="tc_admin",
            name="plugin_admin_action",
            arguments={},
        ),
        sender_account_key="milky:user",
    )

    assert result.error_code == "not_authorized"
    assert phase == "not_authorized"
    assert executed is False


# --- chat-domain scoped tool execution ----------------------------------------


def _scoped_registry(capture: dict[str, Any]) -> ToolRegistry:
    async def scoped_lookup(**kwargs: Any) -> str:
        capture.update(kwargs)
        return "ok"

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="scoped_lookup",
            description="Scoped lookup",
            parameters={"type": "object"},
            handler=scoped_lookup,
            plugin_id="scoped-plugin",
            scope=TOOL_SCOPE_CHAT_DOMAIN,
        )
    )
    return registry


def _scoped_gate() -> AuthorizationGate:
    return AuthorizationGate(
        frozenset({"milky:admin"}),
        enabled=True,
        domains=ChatDomainIndex({"main": ["milky:group:100", "milky:group:200"]}),
    )


def _loop_for(registry: ToolRegistry, gate: AuthorizationGate) -> AgentLoop:
    return AgentLoop(
        provider=_ToolCallingProvider(),
        context_builder=ContextBuilder(
            budget=ContextBudget(max_tokens=300, reserved_tokens=0),
            fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
        ),
        tool_executor=RegistryToolExecutor(registry),
        authorization=gate,
    )


@pytest.mark.asyncio
async def test_scoped_tool_gets_allowed_chats_injected_for_non_admin() -> None:
    capture: dict[str, Any] = {}
    loop = _loop_for(_scoped_registry(capture), _scoped_gate())

    result, _, phase = await loop._execute_tool_with_lifecycle(
        ToolCall(call_id="tc1", name="scoped_lookup", arguments={}),
        sender_account_key="milky:user",
        chat_address="milky:group:100",
    )

    assert phase == "completed"
    assert result.is_error is False
    assert capture["allowed_chats"] == ["milky:group:100", "milky:group:200"]


@pytest.mark.asyncio
async def test_scoped_tool_model_provided_allowed_chats_is_stripped() -> None:
    capture: dict[str, Any] = {}
    loop = _loop_for(_scoped_registry(capture), _scoped_gate())

    result, _, phase = await loop._execute_tool_with_lifecycle(
        ToolCall(
            call_id="tc1",
            name="scoped_lookup",
            # The model must not be able to widen its own scope.
            arguments={"allowed_chats": ["milky:group:300"]},
        ),
        sender_account_key="milky:user",
        chat_address="milky:group:100",
    )

    assert phase == "completed"
    assert result.is_error is False
    assert capture["allowed_chats"] == ["milky:group:100", "milky:group:200"]


@pytest.mark.asyncio
async def test_scoped_tool_admin_gets_no_allowed_chats_injection() -> None:
    capture: dict[str, Any] = {}
    loop = _loop_for(_scoped_registry(capture), _scoped_gate())

    result, _, phase = await loop._execute_tool_with_lifecycle(
        ToolCall(call_id="tc1", name="scoped_lookup", arguments={}),
        sender_account_key="milky:admin",
        chat_address="milky:group:100",
    )

    assert phase == "completed"
    assert "allowed_chats" not in capture


@pytest.mark.asyncio
async def test_scoped_tool_denies_cross_domain_target() -> None:
    capture: dict[str, Any] = {}
    loop = _loop_for(_scoped_registry(capture), _scoped_gate())

    result, _, phase = await loop._execute_tool_with_lifecycle(
        ToolCall(
            call_id="tc1",
            name="scoped_lookup",
            arguments={"chat_address": "milky:group:300"},
        ),
        sender_account_key="milky:user",
        chat_address="milky:group:100",
    )

    assert result.error_code == "not_authorized"
    assert phase == "not_authorized"
    assert capture == {}
    assert "Do not retry" in (result.error_message or "")
