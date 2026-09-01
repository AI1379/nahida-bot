"""Plugin base class, registration decorators, and MemoryRef."""

from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

from nahida_bot_sdk.api import BotAPI
from nahida_bot_sdk.commands import CommandArgument
from nahida_bot_sdk.manifest import PluginManifest

# ── Registration decorators ─────────────────────────────────

_HandlerT = TypeVar("_HandlerT", bound=Callable[..., Any])


def register_command(
    name: str,
    *,
    description: str = "",
    aliases: list[str] | None = None,
    arguments: Sequence[CommandArgument] | None = None,
) -> Callable[[_HandlerT], _HandlerT]:
    """Decorator to mark a method as a slash-command handler.

    The decorated method must accept keyword arguments
    ``args: str``, ``inbound: InboundMessage``, ``session_id: str``
    and return a ``CommandResult``.

    Usage::

        class MyPlugin(Plugin):

            @register_command("hello", description="Say hello")
            async def _cmd_hello(
                self, *, args: str, inbound: InboundMessage, session_id: str
            ) -> CommandResult:
                return CommandResult.text(f"Hello, {args}")
    """

    def deco(func: _HandlerT) -> _HandlerT:
        setattr(
            func,
            "__nahida_cmd__",
            {
                "name": name,
                "description": description,
                "aliases": aliases or [],
                "arguments": tuple(arguments or ()),
            },
        )
        return func

    return deco


def register_tool(
    name: str,
    *,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    requires_admin: bool = False,
) -> Callable[[_HandlerT], _HandlerT]:
    """Decorator to mark a method as an LLM-callable tool handler.

    The decorated method should accept ``**kwargs`` and return ``str``.

    Usage::

        class MyPlugin(Plugin):

            @register_tool("my_tool", description="Does something useful")
            async def _handle_my_tool(self, **kwargs: object) -> str:
                return "done"
    """

    def deco(func: _HandlerT) -> _HandlerT:
        setattr(
            func,
            "__nahida_tool__",
            {
                "name": name,
                "description": description,
                "parameters": parameters
                or {"type": "object", "properties": {}, "required": []},
                "requires_admin": requires_admin,
            },
        )
        return func

    return deco


def subscribe(event_type: type) -> Callable[[_HandlerT], _HandlerT]:
    """Decorator to mark a method as an event subscriber.

    The decorated method should accept a single ``event`` argument
    and return ``None``.

    Usage::

        class MyPlugin(Plugin):

            @subscribe(MessageReceived)
            async def _on_message(self, event: MessageReceived) -> None:
                pass
    """

    def deco(func: _HandlerT) -> _HandlerT:
        setattr(func, "__nahida_sub__", {"event_type": event_type})
        return func

    return deco


@dataclass(slots=True, frozen=True)
class MemoryRef:
    """A retrieved memory record."""

    key: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class _CommandDeclaration:
    name: str
    method_name: str
    description: str
    aliases: tuple[str, ...]
    arguments: tuple[CommandArgument, ...] = ()


@dataclass(slots=True, frozen=True)
class _ToolDeclaration:
    name: str
    method_name: str
    description: str
    parameters: dict[str, Any]
    requires_admin: bool


@dataclass(slots=True, frozen=True)
class _SubscriptionDeclaration:
    event_type: type
    method_name: str


@dataclass(slots=True, frozen=True)
class _BoundCommandRegistration:
    name: str
    handler: Callable[..., Awaitable[Any]]
    description: str
    aliases: tuple[str, ...]
    arguments: tuple[CommandArgument, ...] = ()


@dataclass(slots=True, frozen=True)
class _BoundToolRegistration:
    name: str
    handler: Callable[..., Awaitable[str]]
    description: str
    parameters: dict[str, Any]
    requires_admin: bool


@dataclass(slots=True, frozen=True)
class _BoundSubscriptionRegistration:
    event_type: type
    handler: Callable[..., Awaitable[None]]


def _command_registration_names(name: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    return (name, *aliases)


def bind_decorated_registrations(plugin: Any, api: BotAPI | None = None) -> None:
    """Bind decorator declarations from *plugin* into *api*.

    This is used by the runtime and SDK testing helpers. Plugin authors usually
    do not need to call it directly.
    """
    target_api = plugin.api if api is None else api
    for command in plugin._iter_decorated_commands():
        target_api.register_command(
            command.name,
            command.handler,
            description=command.description,
            aliases=list(command.aliases),
            arguments=list(command.arguments),
        )
    for tool in plugin._iter_decorated_tools():
        target_api.register_tool(
            tool.name,
            tool.description,
            tool.parameters,
            tool.handler,
            requires_admin=tool.requires_admin,
        )
    for subscription in plugin._iter_decorated_subscriptions():
        target_api.subscribe(subscription.event_type, subscription.handler)


class Plugin(ABC):
    """Base class for all nahida-bot plugins.

    Two styles are supported for registering handlers:

    1. **Decorator style** (recommended)::

           class MyPlugin(Plugin):
               @register_command("hello", description="Say hello")
               async def _cmd_hello(self, *, args, inbound, session_id) -> CommandResult:
                   return CommandResult(OutboundMessage(text=f"Hello, {args}"))

       Decorated handlers are activated by the runtime when the plugin is enabled.

    2. **Imperative style** (backward compatible)::

           class MyPlugin(Plugin):
               async def on_load(self) -> None:
                   self.api.register_command("hello", self._cmd_hello, ...)

    Both styles coexist — a plugin can use decorators for its own handlers
    while also calling ``self.api.register_*`` in ``on_load`` for dynamic
    registration.
    """

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        commands: dict[str, _CommandDeclaration] = {}
        command_names_by_method: dict[str, tuple[str, ...]] = {}
        command_owner_by_name: dict[str, str] = {}
        tools: dict[str, _ToolDeclaration] = {}
        tool_name_by_method: dict[str, str] = {}
        subscriptions_by_method: dict[str, _SubscriptionDeclaration] = {}

        for base in reversed(cls.__mro__):
            if base in (Plugin, object):
                continue
            for attr_name, attr in base.__dict__.items():
                old_command_names = command_names_by_method.pop(attr_name, None)
                if old_command_names is not None:
                    commands.pop(old_command_names[0], None)
                    for command_name in old_command_names:
                        command_owner_by_name.pop(command_name, None)
                old_tool = tool_name_by_method.pop(attr_name, None)
                if old_tool is not None:
                    tools.pop(old_tool, None)
                subscriptions_by_method.pop(attr_name, None)

                if not callable(attr):
                    continue

                cmd_meta = getattr(attr, "__nahida_cmd__", None)
                if cmd_meta is not None:
                    cmd_name = str(cmd_meta["name"])
                    aliases = tuple(
                        str(alias) for alias in cmd_meta.get("aliases") or ()
                    )
                    command_names = _command_registration_names(cmd_name, aliases)
                    seen_command_names: set[str] = set()
                    for command_name in command_names:
                        if command_name in seen_command_names:
                            raise ValueError(
                                f"Duplicate @register_command name or alias "
                                f"{command_name!r} in plugin class {cls.__name__}"
                            )
                        seen_command_names.add(command_name)
                        if command_name in command_owner_by_name:
                            raise ValueError(
                                f"Duplicate @register_command name or alias "
                                f"{command_name!r} in plugin class {cls.__name__}"
                            )
                    commands[cmd_name] = _CommandDeclaration(
                        name=cmd_name,
                        method_name=attr_name,
                        description=str(cmd_meta.get("description", "")),
                        aliases=aliases,
                        arguments=tuple(cmd_meta.get("arguments", ())),
                    )
                    command_names_by_method[attr_name] = command_names
                    for command_name in command_names:
                        command_owner_by_name[command_name] = attr_name

                tool_meta = getattr(attr, "__nahida_tool__", None)
                if tool_meta is not None:
                    tool_name = str(tool_meta["name"])
                    if tool_name in tools:
                        raise ValueError(
                            f"Duplicate @register_tool name {tool_name!r} "
                            f"in plugin class {cls.__name__}"
                        )
                    tools[tool_name] = _ToolDeclaration(
                        name=tool_name,
                        method_name=attr_name,
                        description=str(tool_meta.get("description", "")),
                        parameters=dict(
                            tool_meta.get(
                                "parameters",
                                {"type": "object", "properties": {}, "required": []},
                            )
                        ),
                        requires_admin=bool(tool_meta.get("requires_admin", False)),
                    )
                    tool_name_by_method[attr_name] = tool_name

                sub_meta = getattr(attr, "__nahida_sub__", None)
                if sub_meta is not None:
                    subscriptions_by_method[attr_name] = _SubscriptionDeclaration(
                        event_type=sub_meta["event_type"],
                        method_name=attr_name,
                    )

        cls.__nahida_command_declarations__ = tuple(commands.values())
        cls.__nahida_tool_declarations__ = tuple(tools.values())
        cls.__nahida_subscription_declarations__ = tuple(
            subscriptions_by_method.values()
        )

    def __init__(self, api: BotAPI, manifest: PluginManifest) -> None:
        self._api = api
        self._manifest = manifest

    @property
    def api(self) -> BotAPI:
        """Bot capabilities available to this plugin."""
        return self._api

    @property
    def manifest(self) -> PluginManifest:
        """This plugin's manifest metadata."""
        return self._manifest

    def _iter_decorated_commands(self) -> tuple[_BoundCommandRegistration, ...]:
        registrations: list[_BoundCommandRegistration] = []
        for declaration in self.__class__.__nahida_command_declarations__:  # type: ignore[attr-defined]
            handler = getattr(self, declaration.method_name)
            registrations.append(
                _BoundCommandRegistration(
                    name=declaration.name,
                    handler=handler,
                    description=declaration.description,
                    aliases=declaration.aliases,
                    arguments=declaration.arguments,
                )
            )
        return tuple(registrations)

    def _iter_decorated_tools(self) -> tuple[_BoundToolRegistration, ...]:
        registrations: list[_BoundToolRegistration] = []
        for declaration in self.__class__.__nahida_tool_declarations__:  # type: ignore[attr-defined]
            handler = getattr(self, declaration.method_name)
            registrations.append(
                _BoundToolRegistration(
                    name=declaration.name,
                    handler=handler,
                    description=declaration.description,
                    parameters=dict(declaration.parameters),
                    requires_admin=declaration.requires_admin,
                )
            )
        return tuple(registrations)

    def _iter_decorated_subscriptions(
        self,
    ) -> tuple[_BoundSubscriptionRegistration, ...]:
        registrations: list[_BoundSubscriptionRegistration] = []
        for declaration in self.__class__.__nahida_subscription_declarations__:  # type: ignore[attr-defined]
            handler = getattr(self, declaration.method_name)
            registrations.append(
                _BoundSubscriptionRegistration(
                    event_type=declaration.event_type,
                    handler=handler,
                )
            )
        return tuple(registrations)

    async def on_load(self) -> None:
        """Called once before the plugin is enabled for the first time.

        Override for one-time setup or imperative ``self.api.register_*`` calls.
        Decorator registrations are handled by the runtime, not by this hook.
        """

    async def on_unload(self) -> None:
        """Called when the plugin is being unloaded. Clean up resources."""
        pass

    async def on_enable(self) -> None:
        """Called when the plugin is enabled after loading."""
        pass

    async def on_disable(self) -> None:
        """Called when the plugin is being disabled."""
        pass
