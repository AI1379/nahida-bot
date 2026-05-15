"""Tests for configuration CLI helpers."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nahida_bot.cli import app
from nahida_bot.cli.config_commands import _build_schema, _validate
from nahida_bot.core.config import (
    MemoryConfig,
    MemoryEmbeddingConfig,
    MemoryRetrievalConfig,
    MultimodalConfig,
    ProviderEntryConfig,
    ProviderModelConfig,
    Settings,
)


runner = CliRunner()


def _settings_with_provider(
    *,
    provider_id: str = "p1",
    model: str = "model-a",
    tags: list[str] | None = None,
    multimodal: MultimodalConfig | None = None,
    memory: MemoryConfig | None = None,
) -> Settings:
    return Settings(
        providers={
            provider_id: ProviderEntryConfig(
                api_key="key",
                base_url="https://example.invalid",
                models=[
                    ProviderModelConfig(
                        name=model,
                        tags=tags or [],
                    )
                ],
            )
        },
        default_provider=provider_id,
        multimodal=multimodal or MultimodalConfig(image_fallback_mode="off"),
        memory=memory or MemoryConfig(),
    )


def test_schema_json_preserves_generic_types() -> None:
    result = runner.invoke(
        app,
        [
            "config",
            "schema",
            "--providers",
            "--section",
            "providers",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    entries = {entry["path"]: entry for entry in json.loads(result.stdout)}
    assert entries["providers"]["type"] == "dict[str, ProviderEntryConfig]"
    assert entries["providers.<id>.models"]["type"] == "list[str | ProviderModelConfig]"
    assert entries["providers.<id>.models"]["default"] == "[]"


def test_schema_reads_pydantic_v2_constraints() -> None:
    entry = _build_schema("agent.max_steps", show_providers=False)[0]

    assert entry.constraints == ">=1"


def test_schema_includes_builtin_plugin_config_defaults() -> None:
    entries = {
        entry.path: entry
        for entry in _build_schema("builtin-commands", show_providers=False)
    }

    assert entries["builtin-commands.allow_external_attachment_paths"].type_ == "bool"
    assert (
        entries["builtin-commands.allow_external_attachment_paths"].default_ == "False"
    )
    assert entries["builtin-commands.external_attachment_roots"].type_ == "list"


def test_schema_reads_external_plugin_config_schema(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
id: demo
name: Demo Plugin
version: "1.0.0"
entrypoint: demo:DemoPlugin
config:
  enabled: true
config_schema:
  type: object
  required: [api_key]
  properties:
    enabled:
      type: boolean
      default: false
    api_key:
      type: string
    retries:
      type: integer
      minimum: 0
      default: 2
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"plugin_paths:\n  - {json.dumps(str(tmp_path / 'plugins'))}\n",
        encoding="utf-8",
    )

    entries = {
        entry.path: entry
        for entry in _build_schema(
            "demo",
            show_providers=False,
            config_yaml=str(config_path),
        )
    }

    assert entries["demo.enabled"].default_ == "True"
    assert entries["demo.api_key"].default_ == "required"
    assert entries["demo.retries"].constraints == ">=0"


def test_validate_accepts_vision_tag_fallback() -> None:
    settings = _settings_with_provider(
        model="vision-model",
        tags=["vision"],
        multimodal=MultimodalConfig(image_fallback_mode="auto"),
    )

    report = _validate(settings)

    assert not any(i.path == "multimodal.image_fallback_model" for i in report.issues)


def test_validate_reports_unresolved_embedding_spec() -> None:
    settings = _settings_with_provider(
        memory=MemoryConfig(
            enabled=True,
            embedding=MemoryEmbeddingConfig(
                enabled=True,
                model="missing-provider/embed-model",
            ),
        )
    )

    report = _validate(settings)

    assert report.errors == 1
    assert report.issues[0].path == "memory.embedding.model"


def test_validate_warns_for_sqlite_vec_without_dimensions() -> None:
    settings = _settings_with_provider(
        tags=["embedding"],
        memory=MemoryConfig(
            enabled=True,
            retrieval=MemoryRetrievalConfig(
                vector_enabled=True,
                vector_backend="sqlite-vec",
            ),
            embedding=MemoryEmbeddingConfig(
                enabled=True,
                dimensions=0,
            ),
        ),
    )

    report = _validate(settings)

    assert any(i.path == "memory.embedding.dimensions" for i in report.issues)
