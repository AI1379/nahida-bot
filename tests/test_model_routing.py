"""Tests for ModelRouter — tag-aware model routing."""

from __future__ import annotations


from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
from nahida_bot.agent.providers.router import ModelRouter
from nahida_bot.core.config import ModelRoutingConfig, ModelRoutingEntry


def _make_slot(
    slot_id: str,
    models: list[str],
    *,
    default: str | None = None,
    tags: dict[str, list[str]] | None = None,
) -> ProviderSlot:
    """Create a minimal ProviderSlot for testing."""
    return ProviderSlot(
        id=slot_id,
        provider=None,  # type: ignore[arg-type]
        context_builder=None,  # type: ignore[arg-type]
        default_model=default or (models[0] if models else ""),
        available_models=models,
        tags_by_model=tags or {},
    )


def _make_router(
    slots: list[ProviderSlot],
    *,
    default_id: str = "",
    routing: ModelRoutingConfig | None = None,
) -> ModelRouter:
    pm = ProviderManager(slots, default_id=default_id or (slots[0].id if slots else ""))
    return ModelRouter(pm, routing or ModelRoutingConfig())


# ── resolve() — concrete model ──────────────────────────


class TestResolveConcrete:
    def test_bare_model_name(self) -> None:
        slot = _make_slot("ds", ["deepseek-chat", "deepseek-lite"])
        router = _make_router([slot])
        result = router.resolve("deepseek-chat")
        assert result is not None
        assert result.slot.id == "ds"
        assert result.model == "deepseek-chat"
        assert result.reason == "explicit"

    def test_provider_model_format(self) -> None:
        slot_a = _make_slot("ds", ["deepseek-chat"])
        slot_b = _make_slot("sf", ["glm-5"])
        router = _make_router([slot_a, slot_b])
        result = router.resolve("sf/glm-5")
        assert result is not None
        assert result.slot.id == "sf"
        assert result.model == "glm-5"

    def test_empty_string_returns_none(self) -> None:
        slot = _make_slot("ds", ["model-a"])
        router = _make_router([slot])
        assert router.resolve("") is None
        assert router.resolve("  ") is None

    def test_unknown_model_returns_none(self) -> None:
        slot = _make_slot("ds", ["deepseek-chat"])
        router = _make_router([slot])
        assert router.resolve("nonexistent-model") is None


# ── resolve() — tag-based ───────────────────────────────


class TestResolveByTag:
    def test_tag_match(self) -> None:
        slot = _make_slot(
            "ds",
            ["deepseek-chat", "deepseek-lite"],
            tags={"deepseek-lite": ["cheap", "memory"]},
        )
        router = _make_router([slot])
        result = router.resolve("cheap")
        assert result is not None
        assert result.slot.id == "ds"
        assert result.model == "deepseek-lite"
        assert "tag:cheap" in result.reason

    def test_tag_across_multiple_providers(self) -> None:
        slot_a = _make_slot("ds", ["deepseek-chat"])
        slot_b = _make_slot(
            "sf",
            ["glm-5", "qwen-lite"],
            tags={"qwen-lite": ["cheap"]},
        )
        router = _make_router([slot_a, slot_b])
        result = router.resolve("cheap")
        assert result is not None
        assert result.slot.id == "sf"
        assert result.model == "qwen-lite"

    def test_primary_tag_matches_default_model(self) -> None:
        slot = _make_slot("ds", ["deepseek-chat", "deepseek-lite"])
        router = _make_router([slot])
        result = router.resolve("primary")
        assert result is not None
        assert result.model == "deepseek-chat"
        assert "tag:primary" in result.reason

    def test_unknown_tag_returns_none(self) -> None:
        slot = _make_slot("ds", ["deepseek-chat"])
        router = _make_router([slot])
        assert router.resolve("embedding") is None

    def test_concrete_takes_priority_over_tag(self) -> None:
        """If a model is literally named 'embedding', concrete match wins."""
        slot = _make_slot(
            "ds",
            ["chat-model", "embedding"],
            tags={"embedding": ["embedding"]},
        )
        router = _make_router([slot])
        # "embedding" matches as a bare model name first
        result = router.resolve("embedding")
        assert result is not None
        assert result.reason == "explicit"


# ── resolve_for_task() ──────────────────────────────────


class TestResolveForTask:
    def test_explicit_override_concrete(self) -> None:
        slot_a = _make_slot("ds", ["deepseek-chat"])
        slot_b = _make_slot("sf", ["glm-5"])
        router = _make_router([slot_a, slot_b])
        result = router.resolve_for_task("memory_dreaming", explicit="sf/glm-5")
        assert result is not None
        assert result.slot.id == "sf"
        assert result.model == "glm-5"

    def test_explicit_override_as_tag(self) -> None:
        slot = _make_slot(
            "ds",
            ["deepseek-chat", "deepseek-lite"],
            tags={"deepseek-lite": ["cheap"]},
        )
        router = _make_router([slot])
        result = router.resolve_for_task("memory_dreaming", explicit="cheap")
        assert result is not None
        assert result.model == "deepseek-lite"

    def test_prefer_tags_from_config(self) -> None:
        slot = _make_slot(
            "ds",
            ["deepseek-chat", "deepseek-lite"],
            tags={"deepseek-lite": ["cheap", "memory"]},
        )
        routing = ModelRoutingConfig(
            memory_dreaming=ModelRoutingEntry(
                prefer_tags=["memory", "cheap"],
                fallback="session",
            ),
        )
        router = _make_router([slot], routing=routing)
        result = router.resolve_for_task("memory_dreaming")
        assert result is not None
        assert result.model == "deepseek-lite"
        assert "tag:memory" in result.reason

    def test_fallback_session_returns_none(self) -> None:
        slot = _make_slot("ds", ["deepseek-chat"])
        routing = ModelRoutingConfig(
            memory_dreaming=ModelRoutingEntry(
                prefer_tags=["memory"],
                fallback="session",
            ),
        )
        router = _make_router([slot], routing=routing)
        # No model has "memory" tag → fallback="session" → return None
        assert router.resolve_for_task("memory_dreaming") is None

    def test_fallback_default(self) -> None:
        slot_a = _make_slot("ds", ["deepseek-chat"])
        slot_b = _make_slot("sf", ["glm-5"])
        routing = ModelRoutingConfig(
            memory_dreaming=ModelRoutingEntry(
                prefer_tags=["memory"],
                fallback="default",
            ),
        )
        router = _make_router([slot_a, slot_b], default_id="ds", routing=routing)
        result = router.resolve_for_task("memory_dreaming")
        assert result is not None
        assert result.slot.id == "ds"
        assert result.model is None  # use default
        assert "fallback:default" in result.reason

    def test_fallback_disabled_returns_none(self) -> None:
        slot = _make_slot("ds", ["deepseek-chat"])
        routing = ModelRoutingConfig(
            reranker=ModelRoutingEntry(
                prefer_tags=["reranker"],
                fallback="disabled",
            ),
        )
        router = _make_router([slot], routing=routing)
        assert router.resolve_for_task("reranker") is None

    def test_unknown_task_returns_none(self) -> None:
        slot = _make_slot("ds", ["deepseek-chat"])
        router = _make_router([slot])
        assert router.resolve_for_task("nonexistent_task") is None

    def test_custom_task_via_extra_allow(self) -> None:
        slot = _make_slot(
            "ds",
            ["deepseek-chat", "summarizer"],
            tags={"summarizer": ["summarization"]},
        )
        routing = ModelRoutingConfig(
            **{  # type: ignore[arg-type]
                "summarization": ModelRoutingEntry(
                    prefer_tags=["summarization"],
                    fallback="default",
                ),
            },
        )
        router = _make_router([slot], routing=routing)
        result = router.resolve_for_task("summarization")
        assert result is not None
        assert result.model == "summarizer"

    def test_prefer_tags_order_matters(self) -> None:
        slot = _make_slot(
            "ds",
            ["model-a", "model-b"],
            tags={
                "model-a": ["cheap"],
                "model-b": ["memory"],
            },
        )
        routing = ModelRoutingConfig(
            memory_dreaming=ModelRoutingEntry(
                prefer_tags=["memory", "cheap"],
                fallback="session",
            ),
        )
        router = _make_router([slot], routing=routing)
        result = router.resolve_for_task("memory_dreaming")
        assert result is not None
        assert result.model == "model-b"  # "memory" tag matched first


# ── Config model tests ──────────────────────────────────


class TestConfigModels:
    def test_provider_model_config_tags_default(self) -> None:
        from nahida_bot.core.config import ProviderModelConfig

        cfg = ProviderModelConfig(name="test-model")
        assert cfg.tags == []

    def test_provider_model_config_tags_set(self) -> None:
        from nahida_bot.core.config import ProviderModelConfig

        cfg = ProviderModelConfig(name="test-model", tags=["primary", "cheap"])
        assert cfg.tags == ["primary", "cheap"]

    def test_model_routing_config_defaults(self) -> None:
        cfg = ModelRoutingConfig()
        assert cfg.memory_dreaming.prefer_tags == ["memory", "cheap"]
        assert cfg.memory_dreaming.fallback == "session"
        assert cfg.embedding.prefer_tags == ["embedding"]
        assert cfg.reranker.prefer_tags == ["reranker", "cheap"]

    def test_model_routing_config_extra_tasks(self) -> None:
        cfg = ModelRoutingConfig(
            **{"custom_task": ModelRoutingEntry(prefer_tags=["custom"])}  # type: ignore[arg-type]
        )
        extra = cfg.model_extra or {}
        assert "custom_task" in extra
