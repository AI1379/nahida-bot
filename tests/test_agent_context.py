"""Tests for agent context building and budget control."""

from __future__ import annotations

from pathlib import Path

import pytest

from nahida_bot.agent.context import (
    ContextBudget,
    ContextBuilder,
    ContextMessage,
    ContextPart,
)
from nahida_bot.agent.providers import ChatProvider, ProviderResponse
from nahida_bot.agent.tokenization import CharacterEstimateTokenizer


class _AlwaysOneTokenizer:
    def count_tokens(self, text: str) -> int:
        return 1


class _FailingTokenizer:
    def count_tokens(self, text: str) -> int:
        raise RuntimeError("boom")


class _ProviderWithTokenizer(ChatProvider):
    name = "mock-provider"

    @property
    def tokenizer(self):
        return _AlwaysOneTokenizer()

    async def chat(self, *, messages, tools=None, timeout_seconds=None, model=None):  # noqa: ANN001
        return ProviderResponse(content="ok")


class _ProviderWithoutTokenizer(ChatProvider):
    name = "mock-provider-no-tokenizer"

    @property
    def tokenizer(self):
        return None

    async def chat(self, *, messages, tools=None, timeout_seconds=None, model=None):  # noqa: ANN001
        return ProviderResponse(content="ok")


class TestContextBuilder:
    """Context assembly and truncation tests."""

    def test_build_context_uses_required_order(self, temp_dir: Path) -> None:
        """Build context should follow baseline, instruction, history, tool order."""
        # Arrange
        workspace_dir = temp_dir / "ws"
        workspace_dir.mkdir(parents=True)
        (workspace_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
        (workspace_dir / "SOUL.md").write_text("soul", encoding="utf-8")
        (workspace_dir / "USER.md").write_text("user", encoding="utf-8")

        builder = ContextBuilder(
            budget=ContextBudget(max_tokens=4000, reserved_tokens=0)
        )
        history = [
            ContextMessage(role="user", source="history", content="hello"),
            ContextMessage(role="assistant", source="history", content="world"),
        ]
        tools = [ContextMessage(role="tool", source="tool_call", content="done")]

        # Act
        result = builder.build_context(
            system_prompt="baseline",
            workspace_root=workspace_dir,
            history_messages=history,
            tool_messages=tools,
        )

        # Assert
        assert [item.source for item in result] == [
            "combined_system",
            "history",
            "history",
            "tool_call",
        ]
        assert "**system_baseline**" in result[0].content
        assert "**workspace_instruction:AGENTS.md**" in result[0].content
        assert "**workspace_instruction:SOUL.md**" in result[0].content
        assert "**workspace_instruction:USER.md**" in result[0].content

    def test_budget_sliding_window_keeps_latest_messages(self, temp_dir: Path) -> None:
        """Sliding window should keep newest dynamic messages first."""
        # Arrange
        builder = ContextBuilder(
            budget=ContextBudget(max_tokens=4, reserved_tokens=0),
            tokenizer=_AlwaysOneTokenizer(),
        )
        history = [
            ContextMessage(role="user", source="history", content="old-1 " * 8),
            ContextMessage(role="assistant", source="history", content="old-2 " * 8),
            ContextMessage(role="user", source="history", content="new-1 " * 8),
            ContextMessage(role="assistant", source="history", content="new-2 " * 8),
        ]

        # Act
        result = builder.build_context(
            system_prompt="baseline", history_messages=history
        )

        # Assert
        retained_history_contents = [
            item.content for item in result if item.source == "history"
        ]
        contents = [item.content for item in result]
        assert any("new-2" in item for item in contents)
        assert not any("old-1" in item for item in retained_history_contents)

    def test_budget_adds_summary_when_messages_are_dropped(self) -> None:
        """Dropped context should be represented by a summary message when possible."""
        # Arrange
        builder = ContextBuilder(
            budget=ContextBudget(
                max_tokens=5, reserved_tokens=0, summary_max_chars=120
            ),
            tokenizer=_AlwaysOneTokenizer(),
        )
        history = [
            ContextMessage(role="user", source="history", content="first " * 10),
            ContextMessage(role="assistant", source="history", content="second " * 10),
            ContextMessage(role="user", source="history", content="third " * 10),
            ContextMessage(role="assistant", source="history", content="forth " * 10),
            ContextMessage(role="user", source="history", content="fifth " * 10),
        ]

        # Act
        result = builder.build_context(
            system_prompt="baseline", history_messages=history
        )

        # Assert
        assert any(item.source == "history_summary" for item in result)

    def test_load_workspace_instructions_skips_missing_and_empty_files(
        self, temp_dir: Path
    ) -> None:
        """Instruction loader should skip absent and empty instruction files."""
        # Arrange
        workspace_dir = temp_dir / "ws"
        workspace_dir.mkdir(parents=True)
        (workspace_dir / "AGENTS.md").write_text("", encoding="utf-8")
        (workspace_dir / "USER.md").write_text("user", encoding="utf-8")
        builder = ContextBuilder()

        # Act
        instructions = builder.load_workspace_instructions(workspace_dir)

        # Assert
        assert [item.source for item in instructions] == [
            "workspace_instruction:USER.md"
        ]

    def test_load_workspace_skills_reads_skill_files(self, temp_dir: Path) -> None:
        """Skill loader should read AgentSkills-compatible SKILL.md files."""
        # Arrange
        workspace_dir = temp_dir / "ws"
        skill_dir = workspace_dir / "skills" / "files"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: files
description: Work with workspace files.
---
# Files

Use workspace_read before workspace_write.
""",
            encoding="utf-8",
        )
        builder = ContextBuilder()

        # Act
        skills = builder.load_workspace_skills(workspace_dir)

        # Assert
        assert len(skills) == 1
        assert skills[0].source == "workspace_skill:files"
        assert "Description: Work with workspace files." in skills[0].content
        assert "workspace_read" in skills[0].content
        assert skills[0].metadata == {
            "skill_name": "files",
            "description": "Work with workspace files.",
            "path": "skills/files/SKILL.md",
        }

    def test_workspace_skill_overrides_project_agent_skill(
        self, temp_dir: Path
    ) -> None:
        """Workspace skills should win when names collide."""
        # Arrange
        workspace_dir = temp_dir / "ws"
        project_skill = workspace_dir / ".agents" / "skills" / "files"
        workspace_skill = workspace_dir / "skills" / "files"
        project_skill.mkdir(parents=True)
        workspace_skill.mkdir(parents=True)
        (project_skill / "SKILL.md").write_text(
            "---\nname: files\n---\nproject version",
            encoding="utf-8",
        )
        (workspace_skill / "SKILL.md").write_text(
            "---\nname: files\n---\nworkspace version",
            encoding="utf-8",
        )
        builder = ContextBuilder()

        # Act
        skills = builder.load_workspace_skills(workspace_dir)

        # Assert
        assert len(skills) == 1
        assert "workspace version" in skills[0].content
        assert "project version" not in skills[0].content

    def test_build_context_injects_skills_after_instructions(
        self, temp_dir: Path
    ) -> None:
        """Context order should include skills after workspace instructions."""
        # Arrange
        workspace_dir = temp_dir / "ws"
        skill_dir = workspace_dir / "skills" / "files"
        skill_dir.mkdir(parents=True)
        (workspace_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
        (skill_dir / "SKILL.md").write_text(
            "---\nname: files\n---\nskill body",
            encoding="utf-8",
        )
        builder = ContextBuilder()

        # Act
        result = builder.build_context(
            system_prompt="baseline",
            workspace_root=workspace_dir,
        )

        # Assert
        assert [item.source for item in result] == ["combined_system"]
        assert "**system_baseline**" in result[0].content
        assert "**workspace_instruction:AGENTS.md**" in result[0].content
        assert "**workspace_skill:files**" in result[0].content

    def test_provider_tokenizer_is_used_when_available(self) -> None:
        """Context builder should use provider tokenizer when provider exposes one."""
        # Arrange
        provider = _ProviderWithTokenizer()
        builder = ContextBuilder(
            budget=ContextBudget(max_tokens=5, reserved_tokens=0),
            provider=provider,
        )
        history = [
            ContextMessage(role="user", source="history", content="a"),
            ContextMessage(role="assistant", source="history", content="b"),
            ContextMessage(role="user", source="history", content="c"),
        ]

        # Act
        result = builder.build_context(system_prompt="base", history_messages=history)

        # Assert
        # One token per message allows more messages than char-estimation would keep.
        assert len(result) == 4

    def test_fallback_tokenizer_is_used_when_provider_has_no_tokenizer(self) -> None:
        """Builder should use configured fallback tokenizer if provider tokenizer is missing."""
        # Arrange
        provider = _ProviderWithoutTokenizer()
        builder = ContextBuilder(
            budget=ContextBudget(max_tokens=4, reserved_tokens=0),
            provider=provider,
            fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=100),
        )
        history = [
            ContextMessage(role="user", source="history", content="hello"),
            ContextMessage(role="assistant", source="history", content="world"),
            ContextMessage(role="tool", source="tool", content="done"),
        ]

        # Act
        result = builder.build_context(system_prompt="base", history_messages=history)

        # Assert
        assert len(result) == 4

    def test_runtime_tokenizer_failure_falls_back(self) -> None:
        """Tokenizer runtime failure should fallback to character estimator path."""
        # Arrange
        builder = ContextBuilder(
            budget=ContextBudget(max_tokens=30, reserved_tokens=0),
            tokenizer=_FailingTokenizer(),
            fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=10),
        )

        # Act
        result = builder.build_context(
            system_prompt="baseline",
            history_messages=[
                ContextMessage(role="user", source="history", content="x" * 40)
            ],
        )

        # Assert
        assert len(result) >= 1


class TestContextPart:
    def test_text_part(self) -> None:
        part = ContextPart(type="text", text="hello")
        assert part.type == "text"
        assert part.text == "hello"
        assert part.url == ""
        assert part.data == ""

    def test_image_url_part(self) -> None:
        part = ContextPart(type="image_url", url="http://example.com/img.jpg")
        assert part.type == "image_url"
        assert part.url == "http://example.com/img.jpg"

    def test_image_base64_part(self) -> None:
        part = ContextPart(
            type="image_base64",
            data="iVBORw0KGgo=",
            mime_type="image/png",
            media_id="img_123",
        )
        assert part.type == "image_base64"
        assert part.data == "iVBORw0KGgo="
        assert part.mime_type == "image/png"

    def test_frozen(self) -> None:
        import dataclasses

        part = ContextPart(type="text", text="hello")
        with pytest.raises(dataclasses.FrozenInstanceError):
            part.text = "changed"  # type: ignore[misc]


class TestContextMessageParts:
    def test_default_empty_parts(self) -> None:
        msg = ContextMessage(role="user", source="test", content="hello")
        assert msg.parts == []

    def test_with_parts(self) -> None:
        parts = [
            ContextPart(type="text", text="describe this"),
            ContextPart(
                type="image_url",
                url="http://example.com/img.jpg",
                media_id="img_123",
            ),
        ]
        msg = ContextMessage(
            role="user",
            source="user_input",
            content="[Image attached]",
            parts=parts,
        )
        assert len(msg.parts) == 2
        assert msg.parts[0].type == "text"
        assert msg.parts[1].type == "image_url"

    def test_backward_compat_no_parts(self) -> None:
        msg = ContextMessage(role="user", source="test", content="hello")
        assert msg.content == "hello"
        assert msg.parts == []
        assert msg.reasoning is None

    def test_token_budget_counts_parts(self) -> None:
        builder = ContextBuilder(
            tokenizer=CharacterEstimateTokenizer(chars_per_token=1)
        )
        without_parts = ContextMessage(role="user", source="test", content="hello")
        with_parts = ContextMessage(
            role="user",
            source="test",
            content="hello",
            parts=[ContextPart(type="image_base64", data="x" * 100)],
        )

        assert builder._estimate_tokens([with_parts]) > builder._estimate_tokens(
            [without_parts]
        )
