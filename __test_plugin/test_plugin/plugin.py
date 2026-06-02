"""A test plugin that exercises all SDK console features."""

from nahida_bot_sdk import Plugin
from nahida_bot_sdk.commands import CommandResult
from nahida_bot_sdk.events import MessageReceived
from nahida_bot_sdk.messaging import InboundMessage, OutboundMessage


class ConsoleTestPlugin(Plugin):
    async def on_load(self) -> None:
        # ── Commands ──────────────────────────────────
        self.api.register_command(
            "hello",
            self._cmd_hello,
            description="Say hello to you",
            aliases=["hi", "hey"],
        )
        self.api.register_command(
            "ping",
            self._cmd_ping,
            description="Respond with pong",
        )
        self.api.register_command(
            "uppercase",
            self._cmd_uppercase,
            description="Convert following text to uppercase",
        )

        # ── Tools ─────────────────────────────────────
        self.api.register_tool(
            "greet",
            "Greet a person by name",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Who to greet"},
                    "style": {
                        "type": "string",
                        "enum": ["casual", "formal"],
                        "default": "casual",
                    },
                },
                "required": ["name"],
            },
            self._tool_greet,
        )

        self.api.register_tool(
            "multiply",
            "Multiply two numbers",
            {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                },
                "required": ["x", "y"],
            },
            self._tool_multiply,
        )

        # ── Events ────────────────────────────────────
        self.api.subscribe(MessageReceived, self._on_message)

    # ── Command handlers ──────────────────────────────

    async def _cmd_hello(self, session_id: str, address: str, message: InboundMessage):
        return CommandResult.text(f"Hello there, {message.user_id}!")

    async def _cmd_ping(self, session_id: str, address: str, message: InboundMessage):
        return CommandResult.text("pong!")

    async def _cmd_uppercase(
        self, session_id: str, address: str, message: InboundMessage
    ):
        text = message.text
        if text.startswith("/uppercase"):
            args = text[11:].strip()
        else:
            args = text
        if not args:
            return CommandResult.text("Usage: /uppercase <text>")
        return CommandResult.text(args.upper())

    # ── Tool handlers ────────────────────────────────

    async def _tool_greet(self, name: str, style: str = "casual") -> str:
        if style == "formal":
            return f"Good day, {name}. A pleasure to make your acquaintance."
        return f"Hey {name}! What's up?"

    async def _tool_multiply(self, x: float, y: float) -> str:
        return f"{x} × {y} = {x * y}"

    # ── Event handlers ───────────────────────────────

    async def _on_message(self, event: MessageReceived) -> None:
        text = event.payload.message
        if not isinstance(text, str) or not text.strip():
            return

        msg = text.lower()
        if msg in ("hello", "hi", "hey"):
            await self.api.send_message(
                "console:private:test",
                OutboundMessage(text="Hi! Try /hello or /ping or just chat with me."),
            )
        elif "how are you" in msg:
            await self.api.send_message(
                "console:private:test",
                OutboundMessage(text="I'm just a test plugin, but I'm doing great!"),
            )
        else:
            await self.api.send_message(
                "console:private:test",
                OutboundMessage(text=f"Echo: {text}"),
            )
