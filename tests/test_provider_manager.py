"""Tests for ProviderManager."""

from unittest.mock import MagicMock

from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot


def _slot(
    id: str = "test",
    models: list[str] | None = None,
    default_model: str = "model-a",
) -> ProviderSlot:
    return ProviderSlot(
        id=id,
        provider=MagicMock(),
        context_builder=MagicMock(),
        default_model=default_model,
        available_models=models or [default_model],
    )


class TestProviderManagerDefault:
    def test_default_returns_first_when_no_id(self) -> None:
        s1, s2 = _slot("a"), _slot("b")
        pm = ProviderManager([s1, s2])
        assert pm.default is s1

    def test_default_returns_named_slot(self) -> None:
        s1, s2 = _slot("a"), _slot("b")
        pm = ProviderManager([s1, s2], default_id="b")
        assert pm.default is s2

    def test_default_none_when_empty(self) -> None:
        pm = ProviderManager([])
        assert pm.default is None


class TestProviderManagerGet:
    def test_get_found(self) -> None:
        s = _slot("deepseek")
        pm = ProviderManager([s])
        assert pm.get("deepseek") is s

    def test_get_not_found(self) -> None:
        pm = ProviderManager([_slot("a")])
        assert pm.get("missing") is None


class TestProviderManagerResolveModel:
    def test_resolve_by_exact_model(self) -> None:
        s1 = _slot("deepseek", models=["deepseek-chat", "deepseek-reasoner"])
        s2 = _slot("glm", models=["glm-4-flash"])
        pm = ProviderManager([s1, s2])
        assert pm.resolve_model("deepseek-reasoner") is s1
        assert pm.resolve_model("glm-4-flash") is s2

    def test_resolve_empty_models_matches_any(self) -> None:
        # A slot with no explicit model list accepts any model name
        s1 = ProviderSlot(
            id="openai",
            provider=MagicMock(),
            context_builder=MagicMock(),
            default_model="gpt-4o",
            available_models=[],
        )
        pm = ProviderManager([s1])
        assert pm.resolve_model("gpt-5-turbo") is s1

    def test_resolve_not_found(self) -> None:
        s1 = _slot("glm", models=["glm-4-flash"])
        pm = ProviderManager([s1])
        assert pm.resolve_model("nonexistent") is None


class TestProviderManagerListAvailable:
    def test_list_all(self) -> None:
        s1 = _slot("ds", models=["a", "b"])
        s2 = _slot("glm", models=["c"])
        pm = ProviderManager([s1, s2])
        result = pm.list_available()
        assert result == [
            {"provider_id": "ds", "model": "a"},
            {"provider_id": "ds", "model": "b"},
            {"provider_id": "glm", "model": "c"},
        ]

    def test_list_uses_default_when_empty_models(self) -> None:
        s = _slot("x", models=[], default_model="fallback")
        pm = ProviderManager([s])
        result = pm.list_available()
        assert result == [{"provider_id": "x", "model": "fallback"}]


class TestProviderManagerSlotIds:
    def test_slot_ids(self) -> None:
        pm = ProviderManager([_slot("a"), _slot("b"), _slot("c")])
        assert pm.slot_ids == ["a", "b", "c"]
