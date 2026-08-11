"""Tests for provider capability parsing used during application startup."""

from pathlib import Path
from typing import Any, cast

import pytest

from nahida_bot.core.app import (
    Application,
    _model_capabilities_from_config,
    _provider_model_entries,
    _with_context_1m_capability,
)
from nahida_bot.core.config import (
    ProviderEntryConfig,
    ProviderModelConfig,
    RouterConfigModel,
    Settings,
)
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_provider_credential_repo import (
    ProviderCredential,
    SQLiteProviderCredentialRepository,
)


def test_model_capabilities_from_config_parses_known_fields() -> None:
    cap = _model_capabilities_from_config(
        {
            "image_input": True,
            "prompt_cache": True,
            "supported_image_mime_types": ["image/png"],
            "context_window": 128000,
            "max_context_window": 1_000_000,
            "auto_compact_token_limit": 100000,
            "effective_context_window_percent": 90,
            "image_generation": True,
            "web_search": True,
            "unknown": "ignored",
        }
    )

    assert cap.image_input is True
    assert cap.prompt_cache is True
    assert cap.image_generation is True
    assert cap.web_search is True
    assert cap.supported_image_mime_types == ("image/png",)
    assert cap.context_window == 128000
    assert cap.max_context_window == 1_000_000
    assert cap.auto_compact_token_limit == 100000
    assert cap.effective_context_window_percent == 90


def test_model_capabilities_from_empty_config_uses_defaults() -> None:
    cap = _model_capabilities_from_config({})

    assert cap.image_input is False
    assert cap.tool_calling is True


def test_context_1m_capability_fills_missing_window() -> None:
    cap = _with_context_1m_capability(_model_capabilities_from_config({}))

    assert cap.context_window == 1_000_000
    assert cap.resolved_context_window() == 1_000_000


def test_context_1m_capability_preserves_explicit_window() -> None:
    cap = _with_context_1m_capability(
        _model_capabilities_from_config({"context_window": 200_000})
    )

    assert cap.context_window == 200_000


def test_provider_model_entries_normalizes_strings_and_objects() -> None:
    entries = _provider_model_entries(
        [
            "text-model",
            ProviderModelConfig(
                name="vision-model",
                capabilities={"image_input": True},
            ),
        ]
    )

    assert entries == [
        ("text-model", {}, []),
        ("vision-model", {"image_input": True}, []),
    ]


@pytest.mark.asyncio
async def test_application_uses_stored_provider_api_key(tmp_path: Path) -> None:
    db_path = tmp_path / "nahida.db"
    engine = DatabaseEngine(db_path)
    await engine.initialize()
    try:
        await SQLiteProviderCredentialRepository(engine).upsert(
            ProviderCredential("deepseek", "api_key", "stored-secret")
        )
    finally:
        await engine.close()

    settings = Settings(
        db_path=str(db_path),
        workspace_base_dir=str(tmp_path / "workspace"),
        plugin_paths=[],
        discover_builtin_channels=False,
        providers={
            "deepseek": ProviderEntryConfig(
                type="deepseek",
                base_url="https://example.invalid",
                models=["deepseek-chat"],
            )
        },
        default_provider="deepseek",
    )

    app = Application(settings=settings)
    await app.initialize()
    try:
        provider_manager = cast(Any, app._provider_manager)
        slot = provider_manager.get("deepseek")
        assert slot is not None
        assert slot.provider.api_key == "stored-secret"
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_application_passes_anthropic_max_tokens_and_history_turns(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=":memory:",
        workspace_base_dir=str(tmp_path / "workspace"),
        plugin_paths=[],
        discover_builtin_channels=False,
        router=RouterConfigModel(max_history_turns=321),
        providers={
            "anthropic": ProviderEntryConfig.model_validate(
                {
                    "type": "anthropic",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid",
                    "max_tokens": 32000,
                    "models": ["claude-sonnet-4-20250514"],
                }
            )
        },
        default_provider="anthropic",
    )

    app = Application(settings=settings)
    await app.initialize()
    try:
        provider_manager = cast(Any, app._provider_manager)
        default_slot = provider_manager.default
        assert default_slot is not None
        assert default_slot.provider.max_tokens == 32000
        assert app.session_runner is not None
        assert app.session_runner._max_history_turns == 321
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_application_context_1m_sets_anthropic_context_budget(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=":memory:",
        workspace_base_dir=str(tmp_path / "workspace"),
        plugin_paths=[],
        discover_builtin_channels=False,
        providers={
            "anthropic": ProviderEntryConfig.model_validate(
                {
                    "type": "anthropic",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid",
                    "context_1m": True,
                    "models": ["claude-sonnet-4-6"],
                }
            )
        },
        default_provider="anthropic",
    )

    app = Application(settings=settings)
    await app.initialize()
    try:
        provider_manager = cast(Any, app._provider_manager)
        default_slot = provider_manager.default
        assert default_slot is not None
        assert default_slot.provider.context_1m is True
        capabilities = default_slot.resolve_capabilities("claude-sonnet-4-6")
        assert capabilities.context_window == 1_000_000
        assert app.session_runner is not None
        builder = app.session_runner._context_builder_for_model(
            default_slot.provider,
            capabilities,
        )
        assert builder.budget.max_tokens == 1_000_000
        assert builder.budget.usable_tokens == 950_000
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_application_passes_anthropic_beta_headers(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=":memory:",
        workspace_base_dir=str(tmp_path / "workspace"),
        plugin_paths=[],
        discover_builtin_channels=False,
        providers={
            "anthropic": ProviderEntryConfig.model_validate(
                {
                    "type": "anthropic",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid",
                    "anthropic_beta_headers": ["context-1m-2025-08-07"],
                    "models": ["claude-opus-4-8"],
                }
            )
        },
        default_provider="anthropic",
    )

    app = Application(settings=settings)
    await app.initialize()
    try:
        provider_manager = cast(Any, app._provider_manager)
        default_slot = provider_manager.default
        assert default_slot is not None
        assert default_slot.provider.anthropic_beta_headers == ["context-1m-2025-08-07"]
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_application_passes_deepseek_thinking_enabled(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=":memory:",
        workspace_base_dir=str(tmp_path / "workspace"),
        plugin_paths=[],
        discover_builtin_channels=False,
        providers={
            "deepseek": ProviderEntryConfig.model_validate(
                {
                    "type": "deepseek",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid",
                    "thinking_enabled": False,
                    "reasoning_effort": "high",
                    "models": ["deepseek-chat"],
                }
            )
        },
        default_provider="deepseek",
    )

    app = Application(settings=settings)
    await app.initialize()
    try:
        provider_manager = cast(Any, app._provider_manager)
        default_slot = provider_manager.default
        assert default_slot is not None
        assert default_slot.provider.thinking_enabled is False
        assert default_slot.provider.reasoning_effort == "high"
    finally:
        await app.stop()
