"""Tests for command registry and matcher."""

import pytest

from nahida_bot.plugins.commands import (
    CommandEntry,
    CommandMatcher,
    CommandRegistry,
)


def _entry(
    name: str = "test",
    plugin_id: str = "p1",
    aliases: tuple[str, ...] = (),
) -> CommandEntry:
    return CommandEntry(
        name=name,
        handler=_async_stub,
        description=f"Test command {name}",
        aliases=aliases,
        plugin_id=plugin_id,
    )


async def _async_stub(**kwargs: object) -> str:
    return "ok"


class TestCommandRegistry:
    def test_register_and_lookup(self) -> None:
        reg = CommandRegistry()
        e = _entry("help")
        reg.register(e)
        assert reg.get("help") is e

    def test_register_with_aliases(self) -> None:
        reg = CommandRegistry()
        e = _entry("help", aliases=("h", "?"))
        reg.register(e)
        assert reg.get("help") is e
        assert reg.get("h") is e
        assert reg.get("?") is e

    def test_register_duplicate_name_raises(self) -> None:
        reg = CommandRegistry()
        reg.register(_entry("help"))
        with pytest.raises(KeyError, match="already registered"):
            reg.register(_entry("help", plugin_id="p2"))

    def test_register_duplicate_alias_raises(self) -> None:
        reg = CommandRegistry()
        reg.register(_entry("help", aliases=("h",)))
        with pytest.raises(KeyError, match="already registered"):
            reg.register(_entry("hint", aliases=("h",)))

    def test_unregister_removes_name_and_aliases(self) -> None:
        reg = CommandRegistry()
        reg.register(_entry("help", aliases=("h",)))
        reg.unregister("help")
        assert reg.get("help") is None
        assert reg.get("h") is None

    def test_unregister_nonexistent_is_noop(self) -> None:
        reg = CommandRegistry()
        reg.unregister("nope")  # should not raise

    def test_unregister_by_plugin(self) -> None:
        reg = CommandRegistry()
        reg.register(_entry("cmd_a", plugin_id="p1"))
        reg.register(_entry("cmd_b", plugin_id="p1", aliases=("b",)))
        reg.register(_entry("cmd_c", plugin_id="p2"))

        count = reg.unregister_by_plugin("p1")
        assert count == 2
        assert reg.get("cmd_a") is None
        assert reg.get("cmd_b") is None
        assert reg.get("b") is None
        assert reg.get("cmd_c") is not None

    def test_all_commands_deduplicates(self) -> None:
        reg = CommandRegistry()
        reg.register(_entry("help", aliases=("h",)))
        reg.register(_entry("ping"))
        cmds = reg.all_commands()
        names = {c.name for c in cmds}
        assert names == {"help", "ping"}


class TestCommandMatcher:
    def test_simple_command(self) -> None:
        m = CommandMatcher()
        result = m.match("/help")
        assert result.matched is True
        assert result.name == "help"
        assert result.args == ""

    def test_command_with_args(self) -> None:
        m = CommandMatcher()
        result = m.match("/search foo bar")
        assert result.matched is True
        assert result.name == "search"
        assert result.args == "foo bar"

    def test_command_with_mention_prefix(self) -> None:
        m = CommandMatcher()
        result = m.match("@mybot /help")
        assert result.matched is True
        assert result.name == "help"
        assert result.args == ""

    def test_mention_with_command_no_space(self) -> None:
        m = CommandMatcher()
        # "@bot/help" style — not matched by our pattern, should fallback
        result = m.match("@bot/help")
        # Our pattern requires space after mention, so this won't match
        # unless the pattern is adjusted. For now this is acceptable.
        assert result.matched is False

    def test_custom_prefix(self) -> None:
        m = CommandMatcher(prefix="!")
        result = m.match("!help")
        assert result.matched is True
        assert result.name == "help"

    def test_override_prefix_per_message(self) -> None:
        m = CommandMatcher(prefix="/")
        result = m.match("!help", prefix="!")
        assert result.matched is True
        assert result.name == "help"

    def test_wrong_prefix_no_match(self) -> None:
        m = CommandMatcher(prefix="/")
        result = m.match("!help")
        assert result.matched is False

    def test_plain_text_no_match(self) -> None:
        m = CommandMatcher()
        result = m.match("hello world")
        assert result.matched is False

    def test_empty_string_no_match(self) -> None:
        m = CommandMatcher()
        result = m.match("")
        assert result.matched is False

    def test_mention_with_command_and_args(self) -> None:
        m = CommandMatcher()
        result = m.match("@bot /search foo bar")
        assert result.matched is True
        assert result.name == "search"
        assert result.args == "foo bar"

    def test_slash_alone_no_command(self) -> None:
        m = CommandMatcher()
        result = m.match("/")
        assert result.matched is False
