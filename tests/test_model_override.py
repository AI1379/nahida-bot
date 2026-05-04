"""Tests for per-request model override across the provider/loop/session stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from nahida_bot.agent.context import ContextBuilder, ContextMessage
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers.base import (
    ChatProvider,
    ProviderResponse,
    ToolDefinition,
)
from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
from nahida_bot.agent.tokenization import Tokenizer
from nahida_bot.core.session_runner import SessionRunner


# -- Test providers that record the model they received --


@dataclass(slots=True)
class _RecordingProvider(ChatProvider):
    """Provider that records the model parameter it received."""

    name: str = "recorder"
    api_family: str = "openai-completions"
    default_model: str = "default-model"
    received_model: str | None = field(default=None, init=False)

    @property
    def tokenizer(self) -> Tokenizer | None:
        return None

    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        self.received_model = model
        return ProviderResponse(content="ok")


def _slot(
    id: str = "test",
    models: list[str] | None = None,
    default_model: str = "default-model",
) -> ProviderSlot:
    provider = _RecordingProvider(default_model=default_model)
    return ProviderSlot(
        id=id,
        provider=provider,
        context_builder=ContextBuilder(),
        default_model=default_model,
        available_models=models or [default_model],
    )


# -- AgentLoop model override --


class TestAgentLoopModelOverride:
    @pytest.mark.asyncio
    async def test_run_without_model_uses_provider_default(self) -> None:
        provider = _RecordingProvider(default_model="gpt-4o")
        loop = AgentLoop(provider=provider, context_builder=ContextBuilder())

        await loop.run(
            user_message="hi",
            system_prompt="sys",
        )
        assert provider.received_model is None

    @pytest.mark.asyncio
    async def test_run_with_model_override(self) -> None:
        provider = _RecordingProvider(default_model="gpt-4o")
        loop = AgentLoop(provider=provider, context_builder=ContextBuilder())

        await loop.run(
            user_message="hi",
            system_prompt="sys",
            model="gpt-4o-mini",
        )
        assert provider.received_model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_run_with_explicit_none_model(self) -> None:
        provider = _RecordingProvider(default_model="gpt-4o")
        loop = AgentLoop(provider=provider, context_builder=ContextBuilder())

        await loop.run(
            user_message="hi",
            system_prompt="sys",
            model=None,
        )
        assert provider.received_model is None


# -- SessionRunner model resolution --


class _FakeMemoryStore:
    """Minimal memory store mock with controllable session metadata."""

    def __init__(self, meta: dict[str, Any] | None = None) -> None:
        self._meta = meta or {}

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        pass

    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        return dict(self._meta)

    async def get_recent(self, *a: Any, **kw: Any) -> list[Any]:
        return []

    async def append_turn(self, *a: Any, **kw: Any) -> int:
        return 0


class TestSessionRunnerModelResolution:
    @pytest.mark.asyncio
    async def test_no_meta_returns_default_no_override(self) -> None:
        slot = _slot("ds", models=["deepseek-chat", "deepseek-reasoner"])
        pm = ProviderManager([slot], default_id="ds")
        memory = _FakeMemoryStore(meta={})

        runner = SessionRunner(
            provider_manager=pm,
            memory_store=cast(Any, memory),
        )
        resolved_slot, model = await runner._resolve_provider("s1")

        assert resolved_slot is slot
        assert model is None

    @pytest.mark.asyncio
    async def test_same_model_as_default_no_override(self) -> None:
        slot = _slot(
            "ds",
            models=["deepseek-chat", "deepseek-reasoner"],
            default_model="deepseek-chat",
        )
        pm = ProviderManager([slot], default_id="ds")
        memory = _FakeMemoryStore(meta={"model": "deepseek-chat"})

        runner = SessionRunner(
            provider_manager=pm,
            memory_store=cast(Any, memory),
        )
        resolved_slot, model = await runner._resolve_provider("s1")

        assert resolved_slot is slot
        assert model is None

    @pytest.mark.asyncio
    async def test_different_model_returns_override(self) -> None:
        slot = _slot("ds", models=["deepseek-chat", "deepseek-reasoner"])
        pm = ProviderManager([slot], default_id="ds")
        memory = _FakeMemoryStore(meta={"model": "deepseek-reasoner"})

        runner = SessionRunner(
            provider_manager=pm,
            memory_store=cast(Any, memory),
        )
        resolved_slot, model = await runner._resolve_provider("s1")

        assert resolved_slot is slot
        assert model == "deepseek-reasoner"

    @pytest.mark.asyncio
    async def test_provider_id_lookup_no_model_override(self) -> None:
        slot1 = _slot("ds", models=["deepseek-chat"])
        slot2 = _slot("glm", models=["glm-4-flash"])
        pm = ProviderManager([slot1, slot2], default_id="ds")
        memory = _FakeMemoryStore(meta={"provider_id": "glm"})

        runner = SessionRunner(
            provider_manager=pm,
            memory_store=cast(Any, memory),
        )
        resolved_slot, model = await runner._resolve_provider("s1")

        assert resolved_slot is slot2
        assert model is None

    @pytest.mark.asyncio
    async def test_model_takes_priority_over_provider_id(self) -> None:
        slot1 = _slot("ds", models=["deepseek-chat", "deepseek-reasoner"])
        slot2 = _slot("glm", models=["glm-4-flash"])
        pm = ProviderManager([slot1, slot2], default_id="ds")
        memory = _FakeMemoryStore(
            meta={"model": "deepseek-reasoner", "provider_id": "glm"}
        )

        runner = SessionRunner(
            provider_manager=pm,
            memory_store=cast(Any, memory),
        )
        resolved_slot, model = await runner._resolve_provider("s1")

        assert resolved_slot is slot1
        assert model == "deepseek-reasoner"

    @pytest.mark.asyncio
    async def test_no_provider_manager(self) -> None:
        runner = SessionRunner()
        resolved_slot, model = await runner._resolve_provider("s1")

        assert resolved_slot is None
        assert model is None

    @pytest.mark.asyncio
    async def test_no_memory_store(self) -> None:
        slot = _slot("ds")
        pm = ProviderManager([slot], default_id="ds")
        runner = SessionRunner(provider_manager=pm, memory_store=None)

        resolved_slot, model = await runner._resolve_provider("s1")

        assert resolved_slot is slot
        assert model is None


# -- End-to-end: SessionRunner.run() wires model to AgentLoop --


class _SpyAgentLoop:
    """Minimal AgentLoop stand-in that records the model kwarg."""

    def __init__(self) -> None:
        self.captured_model: str | None = "NOT_CALLED"
        self.captured_provider: Any = None

    async def run(self, **kwargs: Any) -> Any:
        self.captured_model = kwargs.get("model")
        self.captured_provider = kwargs.get("provider")
        result = MagicMock()
        result.final_response = "ok"
        return result


class TestSessionRunnerEndToEnd:
    @pytest.mark.asyncio
    async def test_run_passes_model_to_agent_loop(self) -> None:
        slot = _slot("ds", models=["deepseek-chat", "deepseek-reasoner"])
        pm = ProviderManager([slot], default_id="ds")
        memory = _FakeMemoryStore(meta={"model": "deepseek-reasoner"})
        spy_loop = _SpyAgentLoop()

        runner = SessionRunner(
            agent_loop=cast(Any, spy_loop),
            memory_store=cast(Any, memory),
            provider_manager=pm,
        )
        await runner.run(
            user_message="hello",
            session_id="s1",
            system_prompt="sys",
        )

        assert spy_loop.captured_model == "deepseek-reasoner"
        assert spy_loop.captured_provider is slot.provider

    @pytest.mark.asyncio
    async def test_run_no_model_no_override(self) -> None:
        slot = _slot("ds", models=["deepseek-chat"])
        pm = ProviderManager([slot], default_id="ds")
        memory = _FakeMemoryStore(meta={})
        spy_loop = _SpyAgentLoop()

        runner = SessionRunner(
            agent_loop=cast(Any, spy_loop),
            memory_store=cast(Any, memory),
            provider_manager=pm,
        )
        await runner.run(
            user_message="hello",
            session_id="s1",
            system_prompt="sys",
        )

        assert spy_loop.captured_model is None
        assert spy_loop.captured_provider is slot.provider


# -- ProviderManager resolve_model still works --


class TestProviderManagerResolveModelEdgeCases:
    def test_resolve_model_returns_correct_slot(self) -> None:
        s1 = _slot("ds", models=["a", "b"])
        s2 = _slot("glm", models=["c", "d"])
        pm = ProviderManager([s1, s2])

        assert pm.resolve_model("b") is s1
        assert pm.resolve_model("d") is s2

    def test_resolve_model_empty_list_matches_any(self) -> None:
        s = ProviderSlot(
            id="openai",
            provider=_RecordingProvider(),
            context_builder=ContextBuilder(),
            default_model="gpt-4o",
            available_models=[],
        )
        pm = ProviderManager([s])

        assert pm.resolve_model("anything") is s

    def test_resolve_model_compound_format(self) -> None:
        s1 = _slot("minimax", models=["MiniMax-M2.5", "MiniMax-M2.7"])
        s2 = _slot("siliconflow", models=["Pro/zai-org/GLM-5"])
        pm = ProviderManager([s1, s2])

        # compound "provider/model" format
        assert pm.resolve_model("minimax/MiniMax-M2.5") is s1
        assert pm.resolve_model("siliconflow/Pro/zai-org/GLM-5") is s2

    def test_resolve_model_compound_unknown_provider_returns_none(self) -> None:
        s1 = _slot("ds", models=["deepseek-chat"])
        pm = ProviderManager([s1])

        # "unknown/model" — prefix not a slot id, full string not a bare model
        assert pm.resolve_model("unknown/deepseek-chat") is None

    def test_resolve_model_bare_name_with_slash(self) -> None:
        s1 = _slot("siliconflow", models=["Pro/zai-org/GLM-5"])
        pm = ProviderManager([s1])

        # "Pro" is not a slot id, so fall back to bare search with full string
        assert pm.resolve_model("Pro/zai-org/GLM-5") is s1

    def test_resolve_model_compound_wrong_model_returns_none(self) -> None:
        s1 = _slot("minimax", models=["MiniMax-M2.5"])
        pm = ProviderManager([s1])

        # correct provider prefix but wrong model
        assert pm.resolve_model("minimax/nonexistent") is None
