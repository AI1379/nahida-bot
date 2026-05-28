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
- **Multi-Channel（千风引路）**：Telegram Bot + Milky QQ（Lagrange.Milky），统一的消息标准化与 ChannelService 协议；OneBot v11/v12 设计中~
- **Multimodal（万象识图）**：原生 vision 图片理解 or 自动 fallback 描述 or image_understand 工具，三种模式自适应~
- **Memory & Retrieval（梦境刻录）**：SQLite 会话记忆 + FTS 关键词检索 + 向量检索 + 混合检索 + LLM 记忆巩固~
- **Agent Orchestration（子机协作）**：主 Agent 可 spawn 子 Agent 执行后台任务，支持 spawn / wait / stop 全生命周期~
- **Cron & Dreaming（时之沙）**：定时任务调度 + 记忆 dreaming（LLM 驱动的周期性记忆整理与巩固）~
- **MCP Support（外道魔术）**：Model Context Protocol 客户端集成，对接外部 MCP 工具服务器~
- **Gateway & WebUI（世界树控制台）**：FastAPI REST API + Vue 3 SPA 运维面板 + SSE 实时事件推送；密码/OTP 登录、配置可视化管理、CRON/Session/文件管理~
- **Ops-friendly（无忧除虫）**：可观测、可诊断、好发布，就算遇到了 Bug 也能轻松捉虫🐞！

## 📈 成长进度（项目状态）

目前小吉祥草王已完成 **Phase 4 全闭环 + WebUI 主体**：Telegram + Milky QQ 双 Channel、Multi-Provider、内置命令/工具/插件体系、Subagent 编排、Multimodal、Scheduler 与 Memory Dreaming、Gateway REST API、WebUI 运维面板（Vue 3）、SSE 实时事件、登录及权限体系均已可用。OneBot v11/v12 双版本协议支持正在设计中。

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
- [x] Gateway REST API：`/api/health`、`/api/status`、`/api/send`、`/api/sessions`、`/api/cron`（全局管理）、`/api/config`（读写/校验/备份）、`/api/files`（workspace 文件管理）、`/api/auth`（登录/登出/session）、`/api/events/stream`（SSE）
- [x] WebUI 运维面板：Vue 3 + Vite + shadcn-vue + Reka UI，首页状态总览、配置页（YAML 编辑/校验/保存/备份）、CRON 管理、Session 分组浏览、文件管理、系统日志
- [x] WebUI 登录体系：管理员密码（Argon2id）+ Session Cookie + 登录限速；Bearer Token 兼容脚本/API 调用
- [x] SSE 实时事件：`status.updated`、`usage.recorded`、`cron.*`、`session.updated`、`config.saved`、`file.updated`
- [x] Usage Ledger：SQLite 持久化 token 统计（input/output/cached/reasoning），支持按 provider/model/session/source_tag 聚合
- [x] Cron Session 模式：`main`（注入主 session）、`isolated`（独立 session）、`named`（持久命名 session，跨 run 累积上下文）
- [x] 回复信号协议：`NO_REPLY` 静默抑制 + `HEARTBEAT_OK` 心跳空转保护
- [x] 设计文档 10+：WebUI、Memory、Cron/WebAPI、ChatAddress/SessionID、Cross-Session、Memory Scoping、Agent Compaction、Agent Loop、Model Routing、Runtime Settings、OneBot Channel

### 🚧 正在进行的光合作用

- [ ] OneBot v11/v12 Channel 插件开发（设计文档已完成，即将进入 Phase 0 实现）。
- [ ] WebUI 高级安全特性：Chat OTP 登录、部署模式（loopback/https/http_emergency）、CSRF 防护。
- [ ] Workspace Sandbox 安全增强：符号链接、TOCTOU、文件大小、特殊文件对象等防护。
- [ ] 插件配置、MockBotAPI 和 SDK 分离整理。

### 📜 未来的建设计划

- [ ] OneBot Channel 完整实现：正向 WS（v11 双工 + v12 事件）、WebHook 模式、多账号支持。
- [ ] Gateway 与 Node 分布式部署（Phase 5）：节点注册、心跳、远程执行协议、WebSocket RPC。
- [ ] WebUI 插件页面 surface：插件通过 manifest 声明 WebUI 面板，sandbox iframe 加载。
- [ ] 轻量记忆图谱层：实体/关系抽取、主题聚类、GraphRAG 风格全局搜索。

想要了解更详细的建设蓝图？请翻阅 [ROADMAP.md](docs/ROADMAP.md) 吧~

## 🏛️ 虚空系统架构概览

```
┌──────────────────────────────────────────────────────────────┐
│                世界树树冠 (Interface Layer)                   │
│              CLI (typer+rich) + WebUI (Vue 3 SPA)            │
├──────────────────────────────────────────────────────────────┤
│              世界树树枝 (Gateway-Node Layer)                  │
│    FastAPI Gateway (REST + SSE) / Node 分布式网络（规划中）   │
├──────────────────────────────────────────────────────────────┤
│                挂件与神之眼 (Plugin Layer)                    │
│   插件加载/权限管理/命令注册/工具注册/Channel接入/MCP集成     │
├──────────────────────────────────────────────────────────────┤
│                   智慧主脑 (Agent Layer)                      │
│ Agent Loop / Subagent编排 / 记忆检索 / 多模态 / Provider抽象  │
├──────────────────────────────────────────────────────────────┤
│                 专属温室 (Workspace Layer)                    │
│          工作空间管理 / 安全文件沙盒 / 指令文件注入            │
├──────────────────────────────────────────────────────────────┤
│                   净土核心 (Core Layer)                       │
│    应用生命周期 / 分层配置 / 事件总线 / 会话管理 / 结构化日志  │
└──────────────────────────────────────────────────────────────┘
```

详细的系统图纸存放在教令院的 [ARCHITECTURE](docs/architecture/README.md) 里哦。

## 🚀 启动终端（快速开始）

### 环境要求

- Python 3.12+
- [astral-uv](https://docs.astral.sh/uv/)
- [pnpm](https://pnpm.io/)（WebUI 前端构建）

### 安装

```bash
git clone https://github.com/your-org/nahida-bot.git
cd nahida-bot

# Python 后端
uv sync

# 如需 Telegram Channel，安装可选依赖
uv sync --group telegram

# WebUI 前端（可选，但推荐）
cd webui
pnpm install
pnpm build    # 输出到 webui/dist/，Gateway 启动时自动挂载
cd ..

# 类型检查与单元测试，可选
uv run pyright
uv run pytest

# 编辑 config.yaml 配置 LLM Provider 和 Channel 后启动
uv run nahida-bot start
```

### CLI 命令

```bash
nahida-bot version                # 显示版本信息
nahida-bot start [--debug]        # 启动应用（含 Gateway + WebUI）
nahida-bot config                 # 显示当前配置
nahida-bot config schema          # 显示配置 schema（含插件 schema）
nahida-bot config validate        # 校验配置文件
nahida-bot doctor                 # 运行诊断检查
nahida-bot gateway                # 仅启动 Gateway（WebAPI + WebUI）
```

### 最小配置示例

```yaml
# config.yaml
app_name: "Nahida Bot"
log_level: "INFO"
log_file: "./data/logs/nahida.log"
log_file_level: "DEBUG"

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
| [CONFIGURATION](docs/CONFIGURATION.md) | 配置指南、环境变量、完整参考 |
| [config.yaml](config.yaml) | 带注释的完整配置参考 |
| [设计文档](docs/todos/) | WebUI、Memory、OneBot Channel 等专题设计 |

## 🤝 参考项目

这里的很多智慧结晶，都离不开前辈们的探索：

| 项目 | 参考内容 |
|-----|-------|
| OpenClaw | Agent + Workspace 模式、Gateway-Node 架构灵感、sentinel token 协议 |
| AstrBot | Python LLM bot 领域的重量级项目 |
| nonebot2 | 繁荣的插件生态、跨平台消息适配的绝佳设计 |
| OneBot v11/v12 | QQ 平台统一协议标准，NapCat/Lagrange/LLOneBot 实现 |
| LiteLLM | 多 Provider 模型的优雅抽象和错误兼容 |
| aiogram | Telegram Bot API 领域建模和异步处理 |
| Milky / Lagrange | QQ 平台接入协议与实现 |

## License

**AGPL-v3.0 License.**
