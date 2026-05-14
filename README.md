# 🍃 Nahida Bot

> ![Avatar](assets/NahidaAvatar1.jpg)
>
> 「这是**摩诃善法大吉祥智慧主**，智慧之神**布耶尔**，须弥的**草神大人**，你敢和她对视五秒吗？」

欢迎连接到属于你的私有「虚空终端」！**Nahida Bot** 不仅仅是一个冷冰冰的机器，而是一个 **Agent 为灵魂**、以 **Workspace 为家**，还能通过 **插件随意换装** 的 Python 智能框架哦~ 🌿

QQ 群：529674493

## ✨ 核心天赋

### 💡 设计理念

- **Agent-first（意识主导）**：以 Agent Loop 为中枢，大语言模型（LLM）在这里不是外挂的工具人，而是真正的主导大脑~
- **Workspace-native（专属花盆）**：文件就是上下文，工作空间（Workspace）作为一等公民被温柔对待。
- **Plugin-driven（百变衣橱）**：不需要在核心代码里硬编码，想要什么新能力？装个插件就好啦！
- **Multi-Provider（万叶一心）**：支持 OpenAI Compatible、DeepSeek、Anthropic Claude、GLM、Groq、Minimax 等多种 LLM 后端，运行时随心切换~
- **Multi-Channel（千风引路）**：Telegram Bot + Milky QQ（Lagrange.Milky），统一的消息标准化与 ChannelService 协议~
- **Multimodal（万象识图）**：原生 vision 图片理解 or 自动 fallback 描述 or image_understand 工具，三种模式自适应~
- **Memory & Retrieval（梦境刻录）**：SQLite 会话记忆 + FTS 关键词检索 + 向量检索 + 混合检索 + LLM 记忆巩固~
- **Agent Orchestration（子机协作）**：主 Agent 可 spawn 子 Agent 执行后台任务，支持 spawn / wait / stop 全生命周期~
- **Cron & Dreaming（时之沙）**：定时任务调度 + 记忆 dreaming（LLM 驱动的周期性记忆整理与巩固）~
- **MCP Support（外道魔术）**：Model Context Protocol 客户端集成，对接外部 MCP 工具服务器~
- **Gateway-Node-ready（世界树网络）**：天然支持远程节点和分布式执行，把智慧的枝蔓延伸到各个角落~
- **Ops-friendly（无忧除虫）**：可观测、可诊断、好发布，就算遇到了 Bug 也能轻松捉虫🐞！

## 📈 成长进度（项目状态）

目前小吉祥草王已完成 **Phase 4 全闭环**：Telegram + Milky QQ 双 Channel、Multi-Provider、内置命令/工具/插件体系、Subagent 编排、Multimodal、Scheduler 与 Memory Dreaming 均主体可用。

### 🌟 已点亮的命座 ✅

- [x] 净土的基石与质量把控（Phase 0）
- [x] 核心生命循环：应用容器、分层配置、事件脉络与观测日志（Phase 1）
- [x] 专属花盆（Workspace）：空间管理、文件沙盒、指令注入（Phase 2.1-2.2）
- [x] 智慧运转（Agent Loop）：消息拼装、模型调用、工具闭环、推理链传播（Phase 2.3-2.4）
- [x] 记忆流转：SQLite 会话记忆、FTS 检索、向量检索、混合检索（Phase 2.5）
- [x] 推理链健壮性：OpenAI/DeepSeek/Claude 多后端推理提取与上下文回传（Phase 2.8）
- [x] 万象识图：Vision 原生传图、fallback 自动描述/工具模式、MediaCache/MediaResolver（Phase 2.9）
- [x] 插件系统：Manifest 声明、Loader 发现加载、权限检查、生命周期隔离、命令与工具注册（Phase 3.1-3.6）
- [x] 接引通道 Telegram：长轮询、消息标准化、HTML/Markdown 转换、群聊 @mention、媒体降级（Phase 4.1-4.5）
- [x] 接引通道 Milky QQ：Lagrange.Milky WebSocket 事件流、消息段建模、群聊触发策略、合并转发解析（Phase 4.6）
- [x] Multi-Provider：per-request model override、Provider 类型运行时注册、pre/post-agent 分阶段加载
- [x] MCP 集成：Model Context Protocol 客户端、工具适配、连接管理
- [x] Subagent 编排：spawn 子 Agent、BackgroundTask 账本、policy hook、父子 session 管理（Phase 3.8 主体）
- [x] 定时调度：Cron 定时任务 + Memory Dreaming LLM 记忆巩固
- [x] 会话级别推理设置：`/reasoning on|off|effort <level>|reset`
- [x] 群聊上下文注入：observed-only 消息记录 + 触发时注入最近群上下文
- [x] 内置命令 11 个：`/reset`、`/new`、`/status`、`/model`、`/reasoning`、`/help`、`/memory`、`/agents`、`/agent_stop`、`/agent_wait`、`/stop`
- [x] 内置工具 14+：`workspace_read/write`、`memory_read/write`、`exec`、`web_fetch`、`plan`、`cron_*`、`agent_*`、`image_understand`

### 🚧 正在进行的光合作用

- [ ] Workspace Sandbox 安全增强：符号链接、TOCTOU、文件大小、特殊文件对象等防护。
- [ ] 插件配置、MockBotAPI 和 SDK 分离整理。
- [ ] Gateway 与 Node 分布式执行闭环。

### 📜 未来的建设计划

- [ ] Gateway 与 Node 分布式部署（Phase 5）
- [ ] 可视化面板与运维工具（WebUI）（Phase 6）

想要了解更详细的建设蓝图？请翻阅 [ROADMAP.md](docs/ROADMAP.md) 吧~

## 🏛️ 虚空系统架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                 世界树树冠 (Interface Layer)                 │
│                   CLI (typer+rich) / WebUI (计划中)          │
├─────────────────────────────────────────────────────────────┤
│               世界树树枝 (Gateway-Node Layer)                │
│            远程节点通讯 / 智慧分布式网络（规划中）            │
├─────────────────────────────────────────────────────────────┤
│                挂件与神之眼 (Plugin Layer)                   │
│   插件加载/权限管理/命令注册/工具注册/Channel接入/MCP集成    │
├─────────────────────────────────────────────────────────────┤
│                   智慧主脑 (Agent Layer)                     │
│ Agent Loop / Subagent编排 / 记忆检索 / 多模态 / Provider抽象 │
├─────────────────────────────────────────────────────────────┤
│                 专属温室 (Workspace Layer)                   │
│          工作空间管理 / 安全文件沙盒 / 指令文件注入           │
├─────────────────────────────────────────────────────────────┤
│                   净土核心 (Core Layer)                      │
│    应用生命周期 / 分层配置 / 事件总线 / 会话管理 / 结构化日志 │
└─────────────────────────────────────────────────────────────┘
```

详细的系统图纸存放在教令院的 [ARCHITECTURE](docs/architecture/README.md) 里哦。

## 🚀 启动终端（快速开始）

### 环境要求

- Python 3.12+
- [astral-uv](https://docs.astral.sh/uv/)

### 安装

```bash
git clone https://github.com/your-org/nahida-bot.git
cd nahida-bot

uv sync

# 如需 Telegram Channel，安装可选依赖
uv sync --group telegram

# 类型检查与单元测试，可选
uv run pyright
uv run pytest

# 编辑 config.yaml 配置 LLM Provider 和 Channel 后启动
uv run nahida-bot start
```

### CLI 命令

```bash
nahida-bot version                # 显示版本信息
nahida-bot start [--debug]        # 启动应用（可指定 --config-yaml 路径）
nahida-bot config                 # 显示当前配置
nahida-bot doctor                 # 运行诊断检查
```

### 最小配置示例

```yaml
# config.yaml
app_name: "Nahida Bot"

providers:
  default:
    type: "openai-compatible"
    api_key: "${LLM_API_KEY}"
    base_url: "${LLM_BASE_URL}"
    stream_responses: true
    models:
      - "${LLM_MODEL}"

default_provider: default

telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"

# Milky QQ Channel（可选）
# milky:
#   base_url: "http://127.0.0.1:3000"
#   access_token: "${MILKY_ACCESS_TOKEN}"
#   group_trigger_mode: "mention"
```

配置支持 `${VAR}` 和 `${VAR:default}` 环境变量插值，可选 `.env` 文件加载。`config.yaml` 中包含 Agent Loop、Context Budget、Scheduler、Memory（FTS/Vector/Embedding）、Multimodal、Router、Model Tags 等完整配置项及详细注释。

## 📚 文档

| 文档 | 内容 |
| ---- | ---- |
| [ARCHITECTURE](docs/architecture/README.md) | 系统架构、分层设计、模块协作 |
| [ROADMAP](docs/ROADMAP.md) | 路线图、阶段规划、验收清单 |
| [DEVELOPMENT](docs/DEVELOPMENT.md) | 代码风格、测试规范、类型检查 |
| [config.yaml](config.yaml) | 带注释的完整配置参考 |

## 🤝 参考项目

这里的很多智慧结晶，都离不开前辈们的探索：

| 项目 | 参考内容 |
|-----|-------|
| OpenClaw | Agent + Workspace 模式、Gateway-Node 架构灵感、sentinel token 协议 |
| AstrBot | Python LLM bot 领域的重量级项目 |
| nonebot2 | 繁荣的插件生态、跨平台消息适配的绝佳设计 |
| LiteLLM | 多 Provider 模型的优雅抽象和错误兼容 |
| aiogram | Telegram Bot API 领域建模和异步处理 |
| Milky / Lagrange | QQ 平台接入协议与实现 |

## License

**AGPL-v3.0 License.**
