"""Tests for command argument metadata and completion (framework layer)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from nahida_bot.plugins.commands import (
    CommandEntry,
    CommandRegistry,
    run_argument_completion,
)
from nahida_bot_sdk.commands import (
    CommandArgument,
    CompletionChoice,
    CompletionQuery,
)
from nahida_bot_sdk.plugin import Plugin, bind_decorated_registrations, register_command

from .helpers import RecordingMockBotAPI


def _query(**overrides: Any) -> CompletionQuery:
    params: dict[str, Any] = {"command": "model", "argument": "name"}
    params.update(overrides)
    return CompletionQuery(**params)


class TestRunArgumentCompletion:
    async def test_static_choices_filtered_by_prefix(self) -> None:
        argument = CommandArgument(name="action", choices=("list", "cancel", "delete"))
        result = await run_argument_completion(argument, _query(partial="de"))

        assert [choice.value for choice in result] == ["delete"]

    async def test_static_choices_all_when_no_partial(self) -> None:
        argument = CommandArgument(name="action", choices=("cancel", "list"))
        result = await run_argument_completion(argument, _query())

        assert [choice.value for choice in result] == ["cancel", "list"]

    async def test_lazy_choices_callable(self) -> None:
        argument = CommandArgument(name="action", choices=lambda: ["x", "y"])
        result = await run_argument_completion(argument, _query(partial="x"))

        assert [choice.value for choice in result] == ["x"]

    async def test_dynamic_completer_receives_raw_query(self) -> None:
        seen: list[CompletionQuery] = []

        async def completer(query: CompletionQuery) -> list[str]:
            seen.append(query)
            return ["deepseek-main", "gpt-4o"]

        argument = CommandArgument(name="name", completer=completer)
        result = await run_argument_completion(
            argument, _query(partial="deep", filled={"other": "1"})
        )

        # Completers own their filtering; nothing is filtered here.
        assert [choice.value for choice in result] == ["deepseek-main", "gpt-4o"]
        assert seen[0].partial == "deep"
        assert seen[0].filled == {"other": "1"}

    async def test_completer_may_return_choice_objects(self) -> None:
        async def completer(query: CompletionQuery) -> list[CompletionChoice]:
            return [CompletionChoice(value="m1", display="Model 1", description="d")]

        argument = CommandArgument(name="name", completer=completer)
        result = await run_argument_completion(argument, _query())

        assert result[0].display == "Model 1"
        assert result[0].description == "d"

    async def test_failing_completer_returns_empty(self) -> None:
        async def completer(query: CompletionQuery) -> list[str]:
            raise RuntimeError("boom")

        argument = CommandArgument(name="name", completer=completer)
        assert await run_argument_completion(argument, _query()) == []

    async def test_no_choices_no_completer_returns_empty(self) -> None:
        argument = CommandArgument(name="name")
        assert await run_argument_completion(argument, _query()) == []


class TestRegistryArguments:
    def test_entry_arguments_flow_to_info(self) -> None:
        async def handler() -> None:  # pragma: no cover - never called
            return None

        entry = CommandEntry(
            name="model",
            handler=handler,  # type: ignore[arg-type]
            description="d",
            aliases=(),
            plugin_id="p",
            arguments=(CommandArgument(name="name"),),
        )
        registry = CommandRegistry()
        registry.register(entry)

        info = registry.get("model").to_info()  # type: ignore[union-attr]
        assert info.arguments == (CommandArgument(name="name"),)

    def test_entry_without_arguments_defaults_empty(self) -> None:
        async def handler() -> None:  # pragma: no cover - never called
            return None

        entry = CommandEntry(
            name="x",
            handler=handler,  # type: ignore[arg-type]
            description="",
            aliases=(),
            plugin_id="p",
        )
        assert entry.to_info().arguments == ()


class TestDecoratorArguments:
    def test_decorated_command_carries_arguments(self) -> None:
        api = RecordingMockBotAPI()

        class _DemoPlugin(Plugin):
            @register_command(
                "demo",
                description="demo command",
                arguments=[CommandArgument(name="kind", choices=("a", "b"))],
            )
            async def _cmd_demo(
                self, *, args: str, inbound: Any, session_id: str
            ) -> None:  # pragma: no cover - never called
                return None

        plugin = _DemoPlugin.__new__(_DemoPlugin)
        bind_decorated_registrations(plugin, cast(Any, api))

        registration = api.registered_commands["demo"]
        assert registration["arguments"][0].name == "kind"
        assert registration["arguments"][0].choices == ("a", "b")


@pytest.mark.parametrize(
    "argument_type,expected_discord_type",
    [
        ("string", 3),
        ("int", 4),
        ("bool", 5),
        ("user", 6),
        ("channel", 7),
        ("float", 10),
    ],
)
def test_option_type_mapping(argument_type: str, expected_discord_type: int) -> None:
    from nahida_bot.channels.discord.plugin import _OPTION_TYPE_MAP

    assert _OPTION_TYPE_MAP.get(argument_type, 3) == expected_discord_type
