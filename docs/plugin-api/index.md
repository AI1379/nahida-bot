# 插件 API

nahida-bot 的插件系统提供了完整的 SDK，让第三方开发者可以在不引入 bot 全部依赖的情况下编写插件。

## 为什么用插件

- **零侵入**：插件运行在隔离的沙箱中，单个插件崩溃不影响核心和其他插件
- **声明式权限**：所有能力通过 `plugin.yaml` 声明，运行时强制检查
- **轻量 SDK**：`nahida-bot-sdk` 仅依赖 `pydantic` + `pyyaml`，不引入数据库、Web 框架等重型依赖
- **可测试**：MockBotAPI 和测试控制台让你无需启动完整 bot 就能验证插件逻辑

## 快速导航

| 页面 | 适合 |
|------|------|
| [教程](/plugin-api/tutorial) | 从未写过 nahida-bot 插件？从这里开始 |
| [API 参考](/plugin-api/reference) | 查找具体的方法签名、事件类型、消息格式 |

## 五分钟上手

```bash
# 1. 创建插件目录
mkdir my-plugin && cd my-plugin

# 2. 编写 pyproject.toml（声明依赖 nahida-bot-sdk）
# 3. 编写 plugin.yaml（声明权限和入口点）
# 4. 编写 plugin.py（继承 Plugin，实现 on_load）
# 5. 测试
uv pip install .
uv run python -m nahida_bot_sdk.testing.console .
```

详细步骤见 [教程](/plugin-api/tutorial) 和 [API 参考](/plugin-api/reference)。
