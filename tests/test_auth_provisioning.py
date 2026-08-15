"""Tests for `auth login` provider provisioning (unconfigured provider ids)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from typer.testing import CliRunner

from nahida_bot.cli import app
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_provider_credential_repo import (
    SQLiteProviderCredentialRepository,
)

runner = CliRunner()


def _write_config(tmp_path: Path) -> Path:
    db_path = tmp_path / "nahida.db"
    config_path = tmp_path / "config-run.yaml"
    config_path.write_text(
        f"""# hand-written config
db_path: "{db_path.as_posix()}"
providers:
  # my main chat provider
  deepseek-main:
    type: deepseek
    api_key: "${{DEEPSEEK_LLM_API_KEY:}}"
    models: [deepseek-chat]
""",
        encoding="utf-8",
    )
    return config_path


async def _stored_secret(db_file: Path, provider_id: str) -> str | None:
    engine = DatabaseEngine(str(db_file))
    await engine.initialize()
    try:
        item = await SQLiteProviderCredentialRepository(engine).get(provider_id)
        return item.secret if item else None
    finally:
        await engine.close()


class TestProvisioning:
    def test_generic_relay_full_interactive_flow(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        result = runner.invoke(
            app,
            ["auth", "login", "myrelay", "--config", str(config_path)],
            input=(
                "y\n"  # create new provider 'myrelay'?
                "9\n"  # preset menu -> generic openai-compatible
                "https://relay.example/v1\n"  # base URL
                "\n"  # model names: accept default
                "sk-relay-key\n"  # API key (hidden)
            ),
        )
        assert result.exit_code == 0, result.output

        text = config_path.read_text(encoding="utf-8")
        assert "# hand-written config" in text, "comments must survive provisioning"
        assert "# my main chat provider" in text
        data = yaml.safe_load(text)
        entry = data["providers"]["myrelay"]
        assert entry["type"] == "openai-compatible"
        assert entry["base_url"] == "https://relay.example/v1"
        assert entry["models"][0]["name"] == "gpt-3.5-turbo"

        secret = asyncio.run(_stored_secret(tmp_path / "nahida.db", "myrelay"))
        assert secret == "sk-relay-key"
        assert "sk-relay-key" not in result.output

    def test_codex_fast_path_skips_menu_and_starts_oauth(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config_path = _write_config(tmp_path)
        calls: list[tuple[str, str]] = []

        async def fake_login(provider_id: str, settings: object) -> int:
            calls.append((provider_id, str(getattr(settings, "db_path", ""))))
            return 0

        monkeypatch.setattr("nahida_bot.cli.auth_commands._run_codex_login", fake_login)
        result = runner.invoke(
            app,
            ["auth", "login", "codex", "--config", str(config_path)],
            input=(
                "y\n"  # create new provider 'codex'?
                "\n"  # model names: accept default
            ),
        )
        assert result.exit_code == 0, result.output
        assert calls and calls[0][0] == "codex"

        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        entry = data["providers"]["codex"]
        assert entry["type"] == "codex"
        assert "api_key" not in entry
        # The OAuth credential never lives in YAML.
        assert "SQLite" not in config_path.read_text(encoding="utf-8")

    def test_typo_suggestion_routes_to_existing_provider(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        before = config_path.read_text(encoding="utf-8")
        result = runner.invoke(
            app,
            ["auth", "login", "deepseek-mian", "--config", str(config_path)],
            input=(
                "y\n"  # did you mean 'deepseek-main'?
                "sk-typo-key\n"
            ),
        )
        assert result.exit_code == 0, result.output
        assert "deepseek-main" in result.output
        assert config_path.read_text(encoding="utf-8") == before
        secret = asyncio.run(_stored_secret(tmp_path / "nahida.db", "deepseek-main"))
        assert secret == "sk-typo-key"

    def test_eof_degrades_to_codex_snippet(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        before = config_path.read_text(encoding="utf-8")
        result = runner.invoke(
            app,
            ["auth", "login", "codex", "--config", str(config_path)],
            input="",
        )
        assert result.exit_code == 1
        assert "providers:" in result.output
        assert "type: codex" in result.output
        # Nothing written on EOF.
        assert config_path.read_text(encoding="utf-8") == before

    def test_decline_create_exits_cleanly(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        result = runner.invoke(
            app,
            ["auth", "login", "myrelay", "--config", str(config_path)],
            input="n\n",
        )
        assert result.exit_code == 1
        assert "myrelay" not in config_path.read_text(encoding="utf-8")
