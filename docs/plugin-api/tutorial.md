# 插件开发教程

本教程从零开始，带你编写一个完整的 nahida-bot 插件。

## 前置条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 包管理器
- nahida-bot 已安装（`uv sync`）

## 第一步：创建项目结构

```bash
mkdir hello-plugin
cd hello-plugin
mkdir hello_plugin
touch hello_plugin/__init__.py
```

最终目录：

```
hello-plugin/
├── pyproject.toml
├── plugin.yaml
└── hello_plugin/
    ├── __init__.py
    └── plugin.py
```

## 第二步：编写 pyproject.toml

```toml
[project]
name = "nahida-plugin-hello"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["nahida-bot-sdk>=0.1.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["hello_plugin"]
```

关键是 `nahida-bot-sdk` 依赖——它会提供 `Plugin`、`BotAPI`、消息类型等全部 SDK 类型。

## 第三步：编写 plugin.yaml

```yaml
id: com.example.hello
name: "Hello Plugin"
version: "0.1.0"
description: "一个简单的问候插件"
entrypoint: "hello_plugin.plugin:HelloPlugin"

permissions:
  network:
    outbound: ["*"]
```

## 第四步：编写插件代码

在 `hello_plugin/plugin.py` 中：

```python
from nahida_bot_sdk import Plugin
from nahida_bot_sdk.commands import CommandResult
from nahida_bot_sdk.events import MessageReceived
from nahida_bot_sdk.messaging import InboundMessage, OutboundMessage


class HelloPlugin(Plugin):

    async def on_load(self) -> None:
        # 注册一个 /hello 命令
        self.api.register_command(
            "hello", self._hello,
            description="Say hello to you",
        )

        # 订阅消息事件——监听所有入站消息
        self.api.subscribe(MessageReceived, self._on_message)

    async def _hello(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return CommandResult.text(f"Hello, {inbound.user_id}!")

    async def _on_message(self, event: MessageReceived) -> None:
        inbound = event.payload.message
        if not isinstance(inbound, InboundMessage) or not inbound.text.strip():
            return
        # 如果有人发送 "天气"，回复一段话
        if "天气" in inbound.text:
            await self.api.send_message(
                "console:private:test",
                OutboundMessage(text="今天天气不错！适合写代码。"),
            )
```

## 第五步：测试

安装插件：

```bash
uv pip install .
```

启动测试控制台：

```bash
uv run python -m nahida_bot_sdk.testing.console .
```

控制台会加载你的插件并显示注册的命令和事件。试试：

```
> /hello
Bot > Hello, console_user!

> 今天天气怎么样
Bot > 今天天气不错！适合写代码。

> #commands
Registered commands (1):
  /hello — Say hello to you

> #exit
```

## 第六步：添加工具

让我们添加一个 LLM 可调用的工具。在 `on_load` 中添加：

```python
self.api.register_tool(
    "get_weather",
    "Get the current weather for a city",
    {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
        },
        "required": ["city"],
    },
    self._get_weather,
)
```

实现 handler：

```python
async def _get_weather(self, city: str) -> str:
    # 实际插件中可以调用天气 API
    return f"{city} 当前晴朗，25°C，湿度 45%"
```

重启控制台测试：

```
> #tools
Registered tools (1):
  get_weather(city) — Get the current weather for a city

> #call get_weather {"city": "Beijing"}
[get_weather] Beijing 当前晴朗，25°C，湿度 45%
```

## 第七步：加入外部依赖

假设你要用 `httpx` 发 HTTP 请求。在 `pyproject.toml` 中添加：

```toml
dependencies = [
    "nahida-bot-sdk>=0.1.0",
    "httpx>=0.27",
]
```

重新安装：

```bash
uv pip install .
```

然后就可以在插件中 `import httpx` 了。

## 第八步：部署到 nahida-bot

1. 将插件目录放到 nahida-bot 的插件路径下（默认 `plugins/`）
2. 安装：`uv pip install ./plugins/hello-plugin`
3. 在 `config.yaml` 中添加插件配置（可选）：

```yaml
plugins:
  "com.example.hello":
    greeting: "Hello from config!"
```

4. 启动 bot：`nahida run`

插件配置在运行时通过 `self.manifest.config` 访问。

## 下一步

- 阅读 [API 参考](/plugin-api/reference) 了解完整的方法签名、事件类型和消息格式
- 阅读 [权限模型](/plugin-api/reference#11-权限声明) 为插件配置正确的权限
- 了解 [测试工具](/plugin-api/reference#7-测试) 的更多用法
