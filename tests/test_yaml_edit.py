"""Tests for the comment-preserving YAML editor."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nahida_bot.core.yaml_edit import (
    YamlEditError,
    document_to_text,
    load_yaml_document,
    save_document,
    upsert_entry,
    upsert_path,
)

COMMENTED_CONFIG = """\
# top-of-file comment
app_name: Nahida Bot   # inline comment

providers:
  # section comment above deepseek
  deepseek-main:
    type: deepseek
    api_key: "${DEEPSEEK_LLM_API_KEY:}"

memory:
  enabled: true
"""


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config-run.yaml"
    path.write_text(COMMENTED_CONFIG, encoding="utf-8")
    return path


class TestUpsertEntry:
    def test_inserts_entry_and_preserves_comments(
        self, config_file: Path, tmp_path: Path
    ) -> None:
        backup = upsert_entry(
            config_file,
            "providers",
            "codex",
            {
                "type": "codex",
                "stream_responses": True,
                "models": [{"name": "gpt-5.5"}],
            },
        )
        assert backup is not None
        assert Path(backup).is_file()
        assert Path(backup).read_text(encoding="utf-8") == COMMENTED_CONFIG

        text = config_file.read_text(encoding="utf-8")
        assert "# top-of-file comment" in text
        assert "# inline comment" in text
        assert "# section comment above deepseek" in text

        data = yaml.safe_load(text)
        assert data["providers"]["codex"]["type"] == "codex"
        assert data["providers"]["deepseek-main"]["type"] == "deepseek"
        assert data["memory"]["enabled"] is True

    def test_replaces_existing_entry(self, config_file: Path) -> None:
        upsert_entry(
            config_file,
            "providers",
            "deepseek-main",
            {"type": "deepseek", "api_key": "sk-new", "models": []},
        )
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert data["providers"]["deepseek-main"]["api_key"] == "sk-new"

    def test_creates_missing_section(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("app_name: Nahida Bot\n", encoding="utf-8")
        upsert_entry(path, "providers", "glm", {"type": "glm"})
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["providers"]["glm"] == {"type": "glm"}

    def test_creates_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "config.yaml"
        assert upsert_entry(path, "providers", "glm", {"type": "glm"}) is None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["providers"]["glm"] == {"type": "glm"}

    def test_no_backup_when_disabled(self, config_file: Path) -> None:
        assert (
            upsert_entry(config_file, "providers", "glm", {"type": "glm"}, backup=False)
            is None
        )
        backups = list(config_file.parent.glob("*.bak.*"))
        assert not backups

    def test_rejects_non_mapping_section(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("providers: a-string\n", encoding="utf-8")
        with pytest.raises(YamlEditError):
            upsert_entry(path, "providers", "glm", {"type": "glm"})

    def test_rejects_unparsable_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("providers: [unclosed\n", encoding="utf-8")
        with pytest.raises(YamlEditError):
            upsert_entry(path, "providers", "glm", {"type": "glm"})
        # The broken file must be left untouched.
        assert path.read_text(encoding="utf-8") == "providers: [unclosed\n"


class TestUpsertPath:
    def test_deep_path_creates_intermediate_mappings(self) -> None:
        doc = load_yaml_document(Path("/nonexistent"))
        upsert_path(doc, ["a", "b", "c"], 1)
        assert doc["a"]["b"]["c"] == 1

    def test_refuses_to_cross_scalar(self) -> None:
        doc = load_yaml_document(Path("/nonexistent"))
        upsert_path(doc, ["a"], "scalar")
        with pytest.raises(YamlEditError):
            upsert_path(doc, ["a", "b"], 1)


class TestSaveDocument:
    def test_atomic_write_preserves_comments(self, config_file: Path) -> None:
        doc = load_yaml_document(config_file)
        doc["memory"]["enabled"] = False
        save_document(doc, config_file)
        text = config_file.read_text(encoding="utf-8")
        assert "# section comment above deepseek" in text
        assert yaml.safe_load(text)["memory"]["enabled"] is False

    def test_document_to_text_does_not_write(self, config_file: Path) -> None:
        doc = load_yaml_document(config_file)
        before = config_file.read_text(encoding="utf-8")
        text = document_to_text(doc)
        assert yaml.safe_load(text)["app_name"] == "Nahida Bot"
        assert config_file.read_text(encoding="utf-8") == before
