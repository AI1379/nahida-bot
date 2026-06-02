"""A simple test plugin for the SDK console."""

from nahida_bot_sdk import Plugin
from nahida_bot_sdk.commands import CommandResult
from nahida_bot_sdk.events import MessageReceived
from nahida_bot_sdk.messaging import InboundMessage, OutboundMessage


class TestConsolePlugin(Plugin):
    async def on_load(self) -> None:
        self.api.register_command("hello", self._hello, description="Say hello")
        self.api.register_command("echo", self._echo, description="Echo back the args")

        self.api.register_tool(
            "add",
            "Add two numbers",
            {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
            self._add,
        )

        self.api.subscribe(MessageReceived, self._on_message)

    async def _hello(self, session_id: str, address: str, message: InboundMessage):
        return CommandResult.text(f"Hello, {message.user_id}!")

    async def _echo(self, session_id: str, address: str, message: InboundMessage):
        args = message.text[5:].strip()  # strip "/echo "
        return CommandResult.text(args if args else "(nothing to echo)")

    async def _add(self, a: float, b: float) -> str:
        return str(a + b)

    async def _on_message(self, event: MessageReceived) -> None:
        text = event.payload.message
        if isinstance(text, str) and text.strip():
            await self.api.send_message(
                "console:private:test",
                OutboundMessage(text=f"You said: {text}"),
            )
