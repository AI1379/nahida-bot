# nahida-bot-sdk

Public SDK types for nahida-bot plugin development.

## Installation

```bash
uv pip install nahida-bot-sdk
```

## Usage

```python
from nahida_bot_sdk import Plugin, BotAPI, PluginManifest, OutboundMessage

class MyPlugin(Plugin):
    async def on_load(self) -> None:
        self.api.register_command("hello", self._hello, description="Say hello")

    async def _hello(self, session_id: str, address: str, message: InboundMessage):
        return CommandResult.text("Hello!")
```
