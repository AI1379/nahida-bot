"""Echo plugin — provides /echo and /ping commands."""

from __future__ import annotations

from nahida_bot_sdk import (
    CommandResult,
    InboundMessage,
    OutboundMessage,
    Plugin,
    register_command,
)


class EchoPlugin(Plugin):
    """Simple plugin that echoes text back and answers /ping."""

    @register_command(
        "echo", description="Echo the text you provide. Usage: /echo <text>"
    )
    async def _cmd_echo(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        """Return whatever the user typed after /echo."""
        text = args.strip() if args.strip() else "…"
        return CommandResult(OutboundMessage(text=text))

    @register_command("ping", description="Replies with pong.")
    async def _cmd_ping(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        """Always returns pong."""
        return CommandResult(OutboundMessage(text="pong"))
