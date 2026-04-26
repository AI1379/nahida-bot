"""Builtin commands plugin — /reset, /new, /status, /model, /help."""

from __future__ import annotations

from typing import Any

import structlog

from nahida_bot.core.router import MessageRouter
from nahida_bot.plugins.api_bridge import RealBotAPI
from nahida_bot.plugins.base import InboundMessage, Plugin

_logger = structlog.get_logger(__name__)


class BuiltinCommandsPlugin(Plugin):
    """Registers core commands available in every nahida-bot instance."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._real_api: RealBotAPI | None = None

    def _get_real_api(self) -> RealBotAPI:
        """Lazily resolve the RealBotAPI for internal access."""
        if self._real_api is None:
            assert isinstance(self.api, RealBotAPI)
            self._real_api = self.api
        return self._real_api

    async def on_load(self) -> None:
        self.api.register_command(
            "reset",
            self._cmd_reset,
            description="Clear current session history",
            aliases=["r"],
        )
        self.api.register_command(
            "new", self._cmd_new, description="Start a new conversation session"
        )
        self.api.register_command(
            "status",
            self._cmd_status,
            description="Show session and model info",
            aliases=["info"],
        )
        self.api.register_command(
            "model", self._cmd_model, description="List or switch model (/model [name])"
        )
        self.api.register_command(
            "help", self._cmd_help, description="List available commands"
        )
        self.api.register_tool(
            "workspace_read",
            "Read a UTF-8 text file from the active workspace.",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the active workspace.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            self._tool_workspace_read,
        )
        self.api.register_tool(
            "workspace_write",
            "Write UTF-8 text content to a file in the active workspace.",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the active workspace.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            self._tool_workspace_write,
        )

    # ── Command Handlers ──────────────────────────────────

    async def _cmd_reset(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        _logger.debug(
            "cmd.reset",
            session_id=session_id,
            platform=inbound.platform,
            chat_id=inbound.chat_id,
        )
        deleted = await self._get_real_api().clear_session(session_id)
        _logger.debug("cmd.reset.done", session_id=session_id, deleted=deleted)
        return f"Session cleared. {deleted} message(s) removed."

    async def _cmd_new(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        new_id = MessageRouter.make_new_session_id(inbound.platform, inbound.chat_id)
        real_api = self._get_real_api()

        _logger.debug(
            "cmd.new.attempt",
            old_session_id=session_id,
            new_session_id=new_id,
            platform=inbound.platform,
            chat_id=inbound.chat_id,
            has_event_bus=real_api._event_bus is not None,
        )

        # Find the MessageRouter to update the active session mapping.
        # The router is accessible through the event bus context.
        if real_api._event_bus is not None:
            ctx = real_api._event_bus._context
            _logger.debug(
                "cmd.new.resolve_context",
                has_context=ctx is not None,
                has_app=hasattr(ctx, "app") if ctx is not None else False,
            )
            if ctx is not None and hasattr(ctx, "app"):
                router = ctx.app.message_router
                _logger.debug(
                    "cmd.new.resolve_router",
                    has_router=router is not None,
                    has_memory=router.memory is not None if router else False,
                )
                if router is not None:
                    router.set_active_session(inbound.platform, inbound.chat_id, new_id)
                    if router.memory is not None:
                        await router.memory.ensure_session(new_id)
                        _logger.debug("cmd.new.success", new_session_id=new_id)
                    else:
                        _logger.warning("cmd.new.no_memory", new_session_id=new_id)
                    return f"New session started: {new_id}"
                else:
                    _logger.warning("cmd.new.no_router")
            else:
                _logger.warning("cmd.new.no_app_context")
        else:
            _logger.warning("cmd.new.no_event_bus")
        return "Failed to create new session — router not available."

    async def _cmd_status(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        real_api = self._get_real_api()
        info = await real_api.get_session_info(session_id)
        provider_id = info.get("provider_id", "(default)")
        model = info.get("model", "(default)")

        lines = [
            f"Session: {session_id}",
            f"Provider: {provider_id}",
            f"Model: {model}",
        ]
        return "\n".join(lines)

    async def _cmd_model(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        real_api = self._get_real_api()

        if not args.strip():
            # List available models
            models = real_api.list_models()
            if not models:
                return "No providers configured."
            info = await real_api.get_session_info(session_id)
            current_model = info.get("model", "")
            lines = ["Available models:"]
            for entry in models:
                marker = " (current)" if entry["model"] == current_model else ""
                lines.append(f"  {entry['provider_id']}/{entry['model']}{marker}")
            return "\n".join(lines)

        # Switch model
        model_name = args.strip()
        provider_id = await real_api.set_session_model(session_id, model_name)
        if provider_id is not None:
            return f"Switched to {model_name} (via {provider_id})"
        return f"Model '{model_name}' not found in any provider."

    async def _cmd_help(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        # Access the command registry through the api bridge
        real_api = self._get_real_api()
        commands = real_api._command_registry.all_commands()
        if not commands:
            return "No commands available."
        lines = ["Available commands:"]
        for cmd in sorted(commands, key=lambda c: c.name):
            aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
            desc = f" — {cmd.description}" if cmd.description else ""
            lines.append(f"  /{cmd.name}{aliases}{desc}")
        return "\n".join(lines)

    async def _tool_workspace_read(self, path: str) -> str:
        """Read a text file from the active workspace."""
        return await self.api.workspace_read(path)

    async def _tool_workspace_write(self, path: str, content: str) -> str:
        """Write a text file to the active workspace."""
        await self.api.workspace_write(path, content)
        return f"Written workspace file: {path}"
