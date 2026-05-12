"""Tests for per-request model override across the provider/loop/session stack."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from nahida_bot.agent.loop import LoopEvent

from nahida_bot.agent.context import ContextBuilder, ContextMessage
from nahida_bot.agent.memory.models import MemoryItem
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers.base import (
    ChatProvider,
    ModelCapabilities,
    ProviderResponse,
    ToolDefinition,
)
from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
from nahida_bot.agent.tokenization import Tokenizer
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.plugins.base import InboundAttachment


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
        self.captured_user_parts: Any = None

    async def run(self, **kwargs: Any) -> Any:
        async for event in self.run_stream(**kwargs):
            if event.type == "done":
                result = MagicMock()
                result.final_response = event.final_response or "ok"
                result.assistant_messages = event.assistant_messages or []
                result.tool_messages = event.tool_messages or []
                result.steps = event.steps
                result.trace_id = event.trace_id
                result.error = event.error
                return result
        return MagicMock(final_response="")

    async def run_stream(self, **kwargs: Any) -> AsyncIterator[LoopEvent]:
        self.captured_model = kwargs.get("model")
        self.captured_provider = kwargs.get("provider")
        self.captured_user_parts = kwargs.get("user_parts")
        yield LoopEvent(type="text", text="ok")
        yield LoopEvent(type="done", final_response="ok")


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

    @pytest.mark.asyncio
    async def test_run_passes_image_attachments_as_parts_for_vision_model(
        self,
    ) -> None:
        slot = ProviderSlot(
            id="vision",
            provider=_RecordingProvider(),
            context_builder=ContextBuilder(),
            default_model="vision-model",
            available_models=["vision-model"],
            capabilities_by_model={
                "vision-model": ModelCapabilities(
                    image_input=True,
                    max_image_count=2,
                )
            },
        )
        pm = ProviderManager([slot], default_id="vision")
        memory = _FakeMemoryStore(meta={})
        spy_loop = _SpyAgentLoop()

        runner = SessionRunner(
            agent_loop=cast(Any, spy_loop),
            memory_store=cast(Any, memory),
            provider_manager=pm,
        )
        await runner.run(
            user_message="describe this",
            session_id="s1",
            system_prompt="sys",
            attachments=[
                InboundAttachment(
                    kind="image",
                    platform_id="img_1",
                    url="https://example.com/img.jpg",
                )
            ],
        )

        assert [part.type for part in spy_loop.captured_user_parts] == [
            "text",
            "image_url",
        ]
        assert spy_loop.captured_user_parts[1].media_id == "img_1"

    @pytest.mark.asyncio
    async def test_run_does_not_pass_image_parts_for_nonvision_model(self) -> None:
        slot = _slot("text", models=["text-model"], default_model="text-model")
        pm = ProviderManager([slot], default_id="text")
        memory = _FakeMemoryStore(meta={})
        spy_loop = _SpyAgentLoop()

        runner = SessionRunner(
            agent_loop=cast(Any, spy_loop),
            memory_store=cast(Any, memory),
            provider_manager=pm,
        )
        await runner.run(
            user_message="[Media: type=image]",
            session_id="s1",
            system_prompt="sys",
            attachments=[
                InboundAttachment(
                    kind="image",
                    platform_id="img_1",
                    url="https://example.com/img.jpg",
                )
            ],
        )

        assert spy_loop.captured_user_parts is None


class TestBuildUserPartsEdgeCases:
    """Unit tests for SessionRunner._build_user_parts edge cases."""

    def _make_runner(self, cap: ModelCapabilities) -> SessionRunner:
        slot = ProviderSlot(
            id="test",
            provider=_RecordingProvider(),
            context_builder=ContextBuilder(),
            default_model="model-a",
            capabilities_by_model={"model-a": cap},
        )
        pm = ProviderManager([slot], default_id="test")
        return SessionRunner(
            agent_loop=cast(Any, _SpyAgentLoop()),
            provider_manager=pm,
        )

    @pytest.mark.asyncio
    async def test_mime_filtering_rejects_unsupported(self) -> None:
        runner = self._make_runner(
            ModelCapabilities(
                image_input=True,
                max_image_count=5,
                supported_image_mime_types=("image/jpeg", "image/png"),
            )
        )
        parts = await runner._build_user_parts(
            "hello",
            [
                InboundAttachment(
                    kind="image",
                    platform_id="img_gif",
                    url="https://x.com/img.gif",
                    mime_type="image/gif",
                ),
                InboundAttachment(
                    kind="image",
                    platform_id="img_jpg",
                    url="https://x.com/img.jpg",
                    mime_type="image/jpeg",
                ),
            ],
            capabilities=ModelCapabilities(
                image_input=True,
                max_image_count=5,
                supported_image_mime_types=("image/jpeg", "image/png"),
            ),
        )
        types = [p.type for p in parts]
        assert "text" in types
        assert types.count("image_url") == 1
        assert all(p.media_id != "img_gif" for p in parts)

    @pytest.mark.asyncio
    async def test_max_image_count_truncates(self) -> None:
        runner = self._make_runner(
            ModelCapabilities(image_input=True, max_image_count=2)
        )
        parts = await runner._build_user_parts(
            "describe",
            [
                InboundAttachment(
                    kind="image", platform_id=f"img_{i}", url=f"https://x.com/{i}"
                )
                for i in range(5)
            ],
            capabilities=ModelCapabilities(
                image_input=True,
                max_image_count=2,
                supported_image_mime_types=(),
            ),
        )
        image_parts = [p for p in parts if p.type == "image_url"]
        assert len(image_parts) == 2

    @pytest.mark.asyncio
    async def test_path_fallback_when_no_url(self) -> None:
        runner = self._make_runner(
            ModelCapabilities(image_input=True, max_image_count=5)
        )
        parts = await runner._build_user_parts(
            "look",
            [
                InboundAttachment(
                    kind="image",
                    platform_id="img_local",
                    alt_text="local image",
                )
            ],
            capabilities=ModelCapabilities(
                image_input=True,
                max_image_count=5,
                supported_image_mime_types=(),
            ),
        )
        # Without a real file on disk, path is not used; alt_text fallback
        desc_parts = [p for p in parts if p.type == "image_description"]
        assert len(desc_parts) == 1

    @pytest.mark.asyncio
    async def test_alt_text_fallback_when_no_url_or_path(self) -> None:
        runner = self._make_runner(
            ModelCapabilities(image_input=True, max_image_count=5)
        )
        parts = await runner._build_user_parts(
            "what is this",
            [
                InboundAttachment(
                    kind="image",
                    platform_id="img_desc",
                    alt_text="A sunset over mountains",
                )
            ],
            capabilities=ModelCapabilities(
                image_input=True,
                max_image_count=5,
                supported_image_mime_types=(),
            ),
        )
        desc_parts = [p for p in parts if p.type == "image_description"]
        assert len(desc_parts) == 1
        assert desc_parts[0].text == "A sunset over mountains"

    @pytest.mark.asyncio
    async def test_non_image_attachments_ignored(self) -> None:
        runner = self._make_runner(
            ModelCapabilities(image_input=True, max_image_count=5)
        )
        parts = await runner._build_user_parts(
            "listen",
            [
                InboundAttachment(kind="audio", platform_id="audio_1"),
                InboundAttachment(kind="video", platform_id="vid_1"),
            ],
            capabilities=ModelCapabilities(
                image_input=True,
                max_image_count=5,
                supported_image_mime_types=(),
            ),
        )
        assert [p.type for p in parts] == ["text"]

    @pytest.mark.asyncio
    async def test_empty_mime_allows_all(self) -> None:
        runner = self._make_runner(
            ModelCapabilities(
                image_input=True,
                max_image_count=5,
                supported_image_mime_types=(),
            )
        )
        parts = await runner._build_user_parts(
            "hello",
            [
                InboundAttachment(
                    kind="image",
                    platform_id="img_1",
                    url="https://x.com/img.webp",
                    mime_type="image/webp",
                ),
            ],
            capabilities=ModelCapabilities(
                image_input=True,
                max_image_count=5,
                supported_image_mime_types=(),
            ),
        )
        image_parts = [p for p in parts if p.type == "image_url"]
        assert len(image_parts) == 1

    @pytest.mark.asyncio
    async def test_no_image_input_returns_empty(self) -> None:
        runner = self._make_runner(ModelCapabilities(image_input=False))
        parts = await runner._build_user_parts(
            "hello",
            [
                InboundAttachment(
                    kind="image",
                    platform_id="img_1",
                    url="https://x.com/img.jpg",
                ),
            ],
            capabilities=ModelCapabilities(image_input=False),
        )
        assert parts == []


class TestPersistTurnsWithAttachments:
    """Tests for SessionRunner._persist_turns with attachment metadata."""

    @pytest.mark.asyncio
    async def test_persists_attachment_metadata_in_user_turn(self) -> None:
        memory = _RecordingMemoryStore()
        runner = SessionRunner(
            agent_loop=cast(Any, _SpyAgentLoop()),
            memory_store=cast(Any, memory),
        )
        attachments = [
            InboundAttachment(
                kind="image",
                platform_id="img_123",
                url="https://cdn.example.com/img.jpg",
                width=800,
                height=600,
                mime_type="image/jpeg",
            )
        ]
        result = MagicMock()
        result.final_response = "It's a cat."

        await runner._persist_turns(
            "session_1",
            "describe this",
            result,
            attachments=attachments,
            source_tag="user_input",
        )

        assert len(memory.turns) == 2
        user_turn = memory.turns[0]
        assert user_turn.role == "user"
        assert user_turn.content == "describe this"
        assert user_turn.metadata is not None
        assert "attachments" in user_turn.metadata
        att_data = user_turn.metadata["attachments"]
        assert len(att_data) == 1
        assert att_data[0]["kind"] == "image"
        assert att_data[0]["platform_id"] == "img_123"
        assert att_data[0]["width"] == 800
        assert att_data[0]["height"] == 600

    @pytest.mark.asyncio
    async def test_no_metadata_when_no_attachments(self) -> None:
        memory = _RecordingMemoryStore()
        runner = SessionRunner(
            agent_loop=cast(Any, _SpyAgentLoop()),
            memory_store=cast(Any, memory),
        )
        result = MagicMock()
        result.final_response = "hello back"

        await runner._persist_turns(
            "session_1",
            "hello",
            result,
            attachments=[],
            source_tag="user_input",
        )

        user_turn = memory.turns[0]
        assert user_turn.metadata is None

    @pytest.mark.asyncio
    async def test_assistant_turn_persisted(self) -> None:
        memory = _RecordingMemoryStore()
        runner = SessionRunner(
            agent_loop=cast(Any, _SpyAgentLoop()),
            memory_store=cast(Any, memory),
        )
        result = MagicMock()
        result.final_response = "It's a cat."

        await runner._persist_turns(
            "session_1",
            "describe this",
            result,
            attachments=[],
            source_tag="user_input",
        )

        assert len(memory.turns) == 2
        assert memory.turns[1].role == "assistant"
        assert memory.turns[1].content == "It's a cat."


class _RecordingMemoryStore:
    """Memory store that records all appended turns."""

    def __init__(self) -> None:
        self.turns: list[Any] = []

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        pass

    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        return {}

    async def get_recent(self, *a: Any, **kw: Any) -> list[Any]:
        return []

    async def append_turn(self, session_id: str, turn: Any, **kw: Any) -> int:
        self.turns.append(turn)
        return len(self.turns)


class _SearchableMemoryStore(_RecordingMemoryStore):
    async def search_items(
        self, query: str = "", *, limit: int = 10
    ) -> list[MemoryItem]:
        return [
            MemoryItem(
                item_id="mem_1",
                scope_type="global",
                scope_id="__global__",
                kind="preference",
                title="Language",
                content="User prefers Chinese technical discussion.",
            )
        ][:limit]


@pytest.mark.asyncio
async def test_session_runner_loads_relevant_durable_memory() -> None:
    runner = SessionRunner(memory_store=cast(Any, _SearchableMemoryStore()))

    message = await runner._load_relevant_memory("Chinese technical discussion")

    assert message is not None
    assert message.source == "long_term_memory"
    assert "User prefers Chinese" in message.content


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
