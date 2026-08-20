# 目录结构与模块边界

## 目录结构

仓库按功能分层，顶层 Python 包 `nahida_bot/` 下分为以下子包：

```text
nahida_bot/
  core/            # 应用容器、分层配置、事件总线、结构化日志
    app.py         #   Application 主类与生命周期
    config.py      #   Settings 及配置加载（YAML + .env 插值）
    events.py      #   EventBus / AppLifecycle 等事件
    config_validation.py / config_schema.py / preflight.py
    router.py / session_runner.py / tasks.py / channel_registry.py
    process_supervisor.py   # 附属进程（SSH/frpc/cloudflared）监管
    ...
  agent/           # Agent 大脑
    loop.py        #   AgentLoop 主循环
    context.py     #   上下文预算与组装
    providers/     #   LLM 后端：deepseek / anthropic / glm / groq /
                   #   minimax / openai_compatible / openai_responses / codex
                   #   + manager.py（ProviderManager）+ router.py（ModelRouter）
                   #   + registry.py（provider 注册表）+ reasoning.py（mixin）
    memory/        #   SQLite 记忆：service / consolidation / scope / models
    orchestration/ #   子 Agent 编排：spawn/wait/stop
    media/         #   媒体缓存与解析
    runtime/       #   canonical run ledger（agent-loop 修复写入侧）
    storage/       #   Document Store + embedding/vector
    usage.py / metrics.py / motion_planner.py
  channels/        # 内置消息通道（以插件形式注册）
    telegram/      #   Telegram Bot
    milky/         #   Milky QQ（Lagrange.Milky）
    onebot/        #   OneBot v11（NapCat/Lagrange/LLOneBot）
  plugins/         # 插件体系
    manager.py / loader.py / registry.py / api_bridge.py / commands.py
    builtin/       #   核心命令、工具（workspace/memory/exec/web_fetch/plan/agent…）
    mcp/           #   Model Context Protocol 客户端
    knowledge_base/  # 知识库导入与检索
    image_generation/  # OpenAI 兼容生图
    conversation_joiner/  # 群聊主动接入
    tts/           #   语音合成（/speak）
  gateway/         # FastAPI REST + SSE
    app.py         #   WebAPIApp 装配、StaticFiles 挂载 webui/dist
    routes/        #   ~21 个路由模块（auth/config/cron/sessions/files/kb/
                   #   plugins/skills/processes/tokens/messages/nodes …）
    services/      #   ~17 个服务（webhost/webui_auth/node_*/...）
    node_protocol/ #   Phase 5 分布式节点协议（规划中）
  node/            # 分布式节点客户端（Phase 5，规划中）
    client.py / capabilities.py
  db/              # SQLite 引擎与仓储
    engine.py
    repositories/  #   各仓储实现（按表拆分）
  identity/        # 人物身份与授权（Person / Admin / AuthorizationGate）
  scheduler/       # Cron 定时任务 + Memory Dreaming
  speech/          # TTS 服务与适配（GPT-SoVITS 等）
  workspace/       # 工作空间管理与文件沙盒
    manager.py / sandbox.py
  cli/             # Typer CLI（start / doctor / bootstrap / config / auth / webui / tokens）
    __init__.py
    bootstrap_commands.py / auth_commands.py / config_commands.py / token_commands.py / webui_commands.py
  core/config.py 是配置入口；SDK 定义见 nahida-bot-sdk/nahida_bot_sdk/。
```

::: tip
插件作者面向的是 [`nahida-bot-sdk`](https://github.com/AI1379/nahida-bot) 包，
其模块是扁平结构：`nahida_bot_sdk/{plugin,api,messaging,events,manifest,commands,chat_address,scaffold}.py` +
`nahida_bot_sdk/testing/`。
:::

## 重点说明

1. **ChannelService 协议** 定义在 `nahida-bot-sdk/nahida_bot_sdk/api.py`（经
   `nahida_bot/plugins/base.py` 再导出）。标准接口：`handle_inbound_event`、
   `send_message`、`get_user_info` 等，并声明支持的通信方式（WebSocket / HTTP / SSE）。

2. **内置 Channel** 在 `nahida_bot/channels/{telegram,milky,onebot}/` 下，以普通插件
   形式被 `PluginManager` 发现与加载（`discover_builtin_channels` 开关），享有权限隔离
   与生命周期管理。OneBot 目前仅 v11 落地，v12 为预留空模块。

3. **第三方 Channel / 能力插件** 结构相同，可外部贡献——遵循同一 Plugin 接口契约，
   通过 `plugin.yaml` 声明，无须修改核心代码。详见 [插件系统](./plugin-system)。
