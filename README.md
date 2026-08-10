# 🍃 Nahida Bot

> ![Avatar](assets/NahidaAvatar1.jpg)
>
> 「这是**摩诃善法大吉祥智慧主**，智慧之神**布耶尔**，须弥的**草神大人**，你敢和她对视五秒吗？」

欢迎连接到属于你的私有「虚空终端」！**Nahida Bot** 不仅仅是一个冷冰冰的机器，而是一个 **Agent 为灵魂**、以 **Workspace 为家**、能长出 **Live2D 桌宠形态**，还能通过 **插件随意换装** 的 Python 智能框架哦~ 🌿

文档：[Docs](https://nahida-bot.cobaltdev.top)

[![QQ 群](https://img.shields.io/badge/QQ_Group-529674493-green?logo=qq)](https://qm.qq.com/q/rXP8DKCyRi) [![Netlify Status](https://api.netlify.com/api/v1/badges/a9eafce9-b879-47be-8220-f4eb728ead1a/deploy-status)](https://app.netlify.com/projects/nahida-bot/deploys)

## ✨ 核心天赋

### 💡 设计理念

- **Agent-first（意识主导）**：以 Agent Loop 为中枢，大语言模型（LLM）在这里不是外挂的工具人，而是真正的主导大脑~
- **Workspace-native（专属花盆）**：文件就是上下文，工作空间（Workspace）作为一等公民被温柔对待。
- **Plugin-driven（百变衣橱）**：不需要在核心代码里硬编码，想要什么新能力？装个插件就好啦！
- **Multi-Provider（万叶一心）**：支持 OpenAI Compatible（含 Responses API）、DeepSeek、Anthropic Claude、GLM、Groq、Minimax 等多种 LLM 后端，运行时随心切换~
- **Multi-Channel（千风引路）**：Telegram Bot + Milky QQ（Lagrange.Milky）+ OneBot v11（NapCat/Lagrange/LLOneBot），统一的消息标准化与 ChannelService 协议~
- **Desktop Pet（梦中之相）**：Tauri + Rust + Vue 3 + Live2D（PixiJS）打造的边缘隐藏式桌宠，鼠标靠近 / Gateway 推送 / CRON 到点时从屏幕角落唤出，还能本地 TTS 发声~
- **Multimodal（万象识图）**：原生 vision 图片理解 or 自动 fallback 描述 or image_understand 工具，三种模式自适应~
- **Memory & Retrieval（梦境刻录）**：SQLite 会话记忆 + FTS 关键词检索 + 向量检索 + 混合检索 + LLM 记忆巩固；独立的 Document Store 让知识库与记忆各自安家~
- **Knowledge Base（须弥图书馆）**：内置知识库插件，导入 PDF/Word/PPT/Excel 等文档，MarkItDown 转换 + 分块 + FTS/向量/混合检索 + 可选 embedding~
- **Agent Orchestration（子机协作）**：主 Agent 可 spawn 子 Agent 执行后台任务，支持 spawn / wait / stop 全生命周期~
- **Cron & Dreaming（时之沙）**：定时任务调度 + 记忆 dreaming（LLM 驱动的周期性记忆整理与巩固）~
- **MCP Support（外道魔术）**：Model Context Protocol 客户端集成，对接外部 MCP 工具服务器~
- **Process Supervisor（眷属守护）**：把 SSH 隧道、frpc、cloudflared 等 sidecar 统一交给核心监管——声明式配置、确定性生命周期（先于 Channel 启动 / 晚于 Channel 停止）、崩溃退避重启 + 熔断、`tcp_port` 健康检查、`/api/processes` 运维面板与 `process.*` SSE 事件~
- **Gateway & WebUI（世界树控制台）**：FastAPI REST API + Vue 3 SPA 运维面板 + SSE 实时事件推送；密码/OTP 登录、配置可视化管理、CRON/Session/文件/知识库/插件/Skills/进程/用量管理~
- **Ops-friendly（无忧除虫）**：可观测、可诊断、好发布，就算遇到了 Bug 也能轻松捉虫🐞！

## 📈 成长进度（项目状态）

目前小吉祥草王已完成 **Phase 4 全闭环 + WebUI 主体 + Desktop 桌宠 + OneBot Channel**：Telegram / Milky QQ / OneBot 三 Channel、Multi-Provider、内置命令/工具/插件体系、Subagent 编排、Multimodal、Scheduler 与 Memory Dreaming、知识库与生图插件、Gateway REST API、WebUI 运维面板（Vue 3）、Desktop Live2D 桌宠、SSE 实时事件、登录及权限体系均已可用。

### 🌟 已点亮的命座 ✅

- [x] 净土的基石与质量把控（Phase 0）
- [x] 核心生命循环：应用容器、分层配置、事件脉络与观测日志（Phase 1）
- [x] 专属花盆（Workspace）：空间管理、文件沙盒、指令注入（Phase 2.1-2.2）
- [x] 智慧运转（Agent Loop）：消息拼装、模型调用、工具闭环、推理链传播（Phase 2.3-2.4）
- [x] 记忆流转：SQLite 会话记忆、FTS 检索、向量检索、混合检索（Phase 2.5）
- [x] 推理链健壮性：OpenAI/DeepSeek/Claude 多后端推理提取与上下文回传，Anthropic reasoning effort + 1M context 支持（Phase 2.8）
- [x] 万象识图：Vision 原生传图、fallback 自动描述/工具模式、MediaCache/MediaResolver（Phase 2.9）
- [x] 插件系统：Manifest 声明、Loader 发现加载、权限检查、生命周期隔离、命令与工具注册（Phase 3.1-3.6）
- [x] 接引通道 Telegram：长轮询、消息标准化、HTML/Markdown 转换、群聊 @mention、媒体降级（Phase 4.1-4.5）
- [x] 接引通道 Milky QQ：Lagrange.Milky WebSocket 事件流、消息段建模、群聊触发策略、合并转发解析（Phase 4.6）
- [x] 接引通道 OneBot：v11 正向 WebSocket + WebHook、CQ 码与 array segment 归一化（v12 为预留模块，尚未实现）
- [x] Multi-Provider：per-request model override、Provider 类型运行时注册、pre/post-agent 分阶段加载、OpenAI Responses API
- [x] MCP 集成：Model Context Protocol 客户端、工具适配、连接管理
- [x] 知识库插件：文档导入（MarkItDown 富文档转换）、分块、FTS/向量/混合检索、可选 embedding、SQLite Document Store
- [x] 生图插件：OpenAI 兼容 Images API 后端、`/draw`·`/生图` 命令、24h 滚动配额管理、自动发送附件
- [x] 群聊主动接入插件（Conversation Joiner）：observed-only 群上下文观察 + engagement 状态机，决定何时自然加入话题（in-memory MVP）
- [x] Desktop 桌宠：Tauri + Rust + Vue 3 + Live2D，边缘隐藏窗口、接近检测、气泡/输入框、本地 TTS 播放管线
- [x] Subagent 编排：spawn 子 Agent、BackgroundTask 账本、policy hook、父子 session 管理（Phase 3.8 主体）
- [x] 记忆与文档存储：Memory Store（consolidation/markdown/scope）+ Document Store 分离，按 chat/global scope 隔离的持久记忆
- [x] 集中式 TaskManager：统一管理 asyncio 任务生命周期与优雅停止
- [x] 定时调度：Cron 定时任务 + Memory Dreaming LLM 记忆巩固
- [x] 会话级别推理设置：`/reasoning on|off|effort <level>|reset`
- [x] 群聊上下文注入：observed-only 消息记录 + 触发时注入最近群上下文
- [x] 内置命令 12 个：`/reset`、`/new`、`/status`、`/model`、`/reasoning`、`/help`、`/memory`、`/agents`、`/agent_stop`、`/agent_wait`、`/cron`、`/stop`
- [x] 内置工具 16+：`workspace_read/write`、`send_local_attachment`、`memory_read/write`、`exec`、`web_fetch`、`plan`、`cron_*`、`agent_*`、`image_understand`，外加知识库检索、生图等插件工具
- [x] Gateway REST API：`/api/health`、`/api/status`、`/api/send`、`/api/sessions`、`/api/cron`（全局管理）、`/api/config`（读写/校验/备份）、`/api/files`（workspace 文件管理）、`/api/kb`（知识库导入与检索）、`/api/plugins`、`/api/skills`、`/api/processes`（sidecar 进程监管）、`/api/tokens`（用量）、`/api/messages`、`/api/auth`（登录/登出/session）、`/api/events/stream`（SSE）
- [x] WebUI 运维面板：Vue 3 + Vite + shadcn-vue + Reka UI，首页状态总览、配置页（YAML 编辑/校验/保存/备份）、CRON 管理、Session 分组浏览、文件管理、知识库导入、插件管理、Skills、用量统计、系统日志、关于页
- [x] WebUI 登录体系：管理员密码（Argon2id）+ Session Cookie + 登录限速；Bearer Token 兼容脚本/API 调用
- [x] SSE 实时事件：`status.updated`、`usage.recorded`、`cron.*`、`session.updated`、`config.saved`、`file.updated`、`process.*`
- [x] Usage Ledger：SQLite 持久化 token 统计（input/output/cached/reasoning），支持按 provider/model/session/source_tag 聚合
- [x] Cron Session 模式：`main`（注入主 session）、`isolated`（独立 session）、`named`（持久命名 session，跨 run 累积上下文）
- [x] 回复信号协议：`NO_REPLY` 静默抑制 + `HEARTBEAT_OK` 心跳空转保护
- [x] Process Supervisor：核心进程监管服务，声明式配置 SSH 隧道/frpc/cloudflared 等 sidecar，确定性生命周期、退避重启 + 熔断、`tcp_port` 健康检查、`/api/processes` 面板与 `process.*` SSE 事件
- [x] SDK 独立成包：`nahida-bot-sdk` 作为 workspace 成员，插件作者可用稳定 API 开发与发布
- [x] 设计文档 18 份：Agent Core、ChatAddress/SessionID、Cross-Session、Memory System/Scoping、Agent Compaction、Model Routing、Runtime Settings、WebUI、Plugin Web Panels、Cron/WebAPI、OneBot Channel、Conversation Joiner、Knowledge Base、Desktop App、Person Identity、MCP Dynamic Servers、Tool-Produced Image Media、Process Supervisor

### 🚧 正在进行的光合作用

- [ ] OneBot Channel 收尾：反向 WebSocket、WebHook 生产化、多账号支持与更多 action 覆盖。
- [ ] Desktop 桌宠完善：通知/番茄钟/CRON 唤出策略打磨、跨平台打包与签名。
- [ ] 知识库与记忆检索合并重构：Document Store 与 Memory 检索模型统一，引入 LLM 摘要压缩与语义召回（Phase 2.5b）。
- [ ] 群聊主动接入插件持久化、管理界面与正式 `group_observe_mode`。
- [ ] 工具产出图片的 media artifact 注册、自动注入与跨轮持久化。
- [ ] WebUI 高级安全特性：Chat OTP 登录、部署模式（loopback/https/http_emergency）、CSRF 防护。
- [ ] Workspace Sandbox 安全增强：符号链接、TOCTOU、文件大小、特殊文件对象等防护。

### 📜 未来的建设计划

- [ ] Gateway 与 Node 分布式部署（Phase 5）：节点注册、心跳、远程执行协议、WebSocket RPC，远程节点跑重模型。
- [ ] WebUI 插件页面 surface：插件通过 manifest 声明 WebUI 面板，sandbox iframe 加载。
- [ ] 人物身份系统（Person Identity）：跨 session 的用户/实体识别与画像沉淀。
- [ ] MCP 动态服务器：运行时增删 MCP server，热加载工具。
- [ ] 轻量记忆图谱层：实体/关系抽取、主题聚类、GraphRAG 风格全局搜索。

想要了解更详细的建设蓝图？请翻阅 [ROADMAP.md](docs/ROADMAP.md) 吧~

## 🏛️ 虚空系统架构概览

```
┌──────────────────────────────────────────────────────────────┐
│                世界树树冠 (Interface Layer)                   │
│   CLI (typer+rich) + WebUI (Vue 3 SPA) + Desktop (Tauri桌宠) │
├──────────────────────────────────────────────────────────────┤
│              世界树树枝 (Gateway-Node Layer)                  │
│    FastAPI Gateway (REST + SSE) / Node 分布式网络（规划中）   │
├──────────────────────────────────────────────────────────────┤
│                   挂件与神之眼 (Plugin Layer)                 │
│  插件加载/权限/命令与工具注册/Channel接入/MCP/知识库/生图/群聊 │
├──────────────────────────────────────────────────────────────┤
│                   智慧主脑 (Agent Layer)                      │
│ Agent Loop / Subagent编排 / 记忆&文档检索 / 多模态 / Provider │
├──────────────────────────────────────────────────────────────┤
│                  专属温室 (Workspace Layer)                   │
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
- [pnpm](https://pnpm.io/)（WebUI 前端 / Desktop 桌宠构建）
- [Rust](https://www.rust-lang.org/) + [Tauri 2](https://v2.tauri.app/)（仅打包 Desktop 桌宠时需要）

### 安装

```bash
git clone https://github.com/AI1379/nahida-bot.git
cd nahida-bot

# Python 后端
uv sync

# 如需知识库导入 PDF、Word、PowerPoint、Excel 等文档
uv sync --extra document-import

# 如需 Telegram Channel，安装可选依赖
uv sync --group telegram

# WebUI 前端（可选，但推荐）
pnpm install          # 安装 workspace 依赖（含 webui / desktop / docs）
pnpm webui:build      # 输出到 webui/dist/，Gateway 启动时自动挂载

# 类型检查与单元测试，可选
uv run pyright
uv run pytest

# 交互式生成最小配置（推荐），或手动编辑 config.yaml
uv run nahida-bot bootstrap
uv run nahida-bot start
```

> 配置与 `.env` 会按以下顺序自动发现，无需手动指定路径：
> `--config-yaml` / `--env` 参数 > `NAHIDA_CONFIG` / `ENV_PATH` 环境变量 > `./config.yaml` / `./.env`。
> 部署前可跑 `nahida-bot doctor` 做一次完整体检。

#### Desktop 桌宠（可选）

```bash
cd desktop
pnpm install
pnpm setup:live2d-core   # 下载 Live2D Core 运行时
pnpm dev:web             # 仅前端预览（Vite，端口 1420）
pnpm build:web           # 构建前端资源
# 打包桌面端（需要 Rust 工具链）：
pnpm build               # = tauri build，产物在 desktop/dist
cd ..
```

桌宠默认通过 WebSocket 连接本地 Gateway，平时贴在屏幕边缘；可在桌面端配置连接地址、Live2D 模型映射与 TTS 参数。详见 [Desktop 设计文档](docs/design/desktop-app.md)。

#### 知识库文档导入

基础安装可以直接导入 UTF-8 编码的 `.txt`、`.text`、`.md` 和
`.markdown` 文件。需要完整的知识库文档导入能力时，安装可选依赖：

```bash
uv sync --extra document-import
```

安装后可通过 WebUI 导入 PDF、DOCX、PPTX、XLS/XLSX、HTML、CSV、
JSON、XML、EPUB、Outlook MSG 和 Jupyter Notebook。富文档会先由
[Microsoft MarkItDown](https://github.com/microsoft/markitdown) 转换为
Markdown，再进入现有的分块和检索流程。WebUI 支持一次选择或拖入最多
20 个文件，单个文件上限为 25 MiB；批量导入会单独报告每个文件的结果，
失败文件不会回滚已经成功导入的文件。

旧版 `.doc` 不受支持，请先另存为 `.docx`。扫描版 PDF 或以图片为主的
文档可能无法提取足够文本；当前集成不会启用 MarkItDown 第三方插件或
云端 OCR。

### CLI 命令

```bash
nahida-bot version                # 显示版本信息
nahida-bot bootstrap              # 交互式生成最小 config.yaml + .env（首次部署）
nahida-bot bootstrap --fix-missing  # 只补缺，不覆盖已有配置
nahida-bot bootstrap --non-interactive  # 脚本/Docker 静默生成
nahida-bot start [--debug]        # 启动应用（含 Gateway + WebUI）
nahida-bot doctor                 # 运行诊断检查（配置/数据库/就绪度）
nahida-bot config schema          # 显示配置 schema（含插件 schema）
nahida-bot config validate        # 校验配置文件
nahida-bot codex login            # ChatGPT Codex OAuth 登录
nahida-bot tokens                 # Token 用量统计
```

### 配置生成（bootstrap）

`nahida-bot bootstrap` 会以问答方式引导你完成 LLM Provider（DeepSeek / SiliconFlow / OpenAI / Claude / GLM / 通用 OpenAI 兼容）和消息 Channel（Telegram / Milky QQ / OneBot）的最小配置，密钥写入 `.env`、其余写入 `config.yaml`。可重入：已有配置只补缺不覆盖（`--fix-missing`），也支持 `--non-interactive` 配合环境变量用于脚本化部署。

### 最小配置示例

```yaml
# config.yaml
app_name: "Nahida Bot"
log_level: "INFO"
log_file: "./data/logs/nahida.log"
log_file_level: "DEBUG"

providers:
  deepseek-main:
    type: deepseek
    api_key: "${DEEPSEEK_LLM_API_KEY:}"
    base_url: "${DEEPSEEK_LLM_BASE_URL:https://api.deepseek.com}"
    stream_responses: true
    models:
      - name: "deepseek-v4-pro"
        tags: [primary]
      - name: "deepseek-v4-flash"
        tags: [cheap, memory]

  siliconflow:
    type: "openai-compatible"
    api_key: "${SILICONFLOW_LLM_API_KEY:}"
    base_url: "${SILICONFLOW_LLM_BASE_URL:https://api.siliconflow.cn/v1}"
    stream_responses: true
    models:
      - name: "Qwen/Qwen3.6-35B-A3B"
        tags: [vision]
        capabilities:
          image_input: true
          max_image_count: 4

default_provider: deepseek-main

telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN:}"

# Milky QQ Channel（可选）
# milky:
#   base_url: "http://127.0.0.1:3000"
#   access_token: "${MILKY_ACCESS_TOKEN}"
#   group_trigger_mode: "mention"

# OneBot Channel（可选，默认 v11 正向 WS）
# onebot:
#   protocol_version: "v11"
#   ws_url: "ws://127.0.0.1:3001"
#   ws_access_token: "${ONEBOT_ACCESS_TOKEN:}"
#   webhook_enabled: false
#   webhook_host: "127.0.0.1"
#   webhook_port: 6186
```

配置支持 `${VAR}` 和 `${VAR:default}` 环境变量插值，可选 `.env` 文件加载。`config.yaml` 中包含 Agent Loop、Context Budget、Scheduler、Memory（FTS/Vector/Embedding）、Multimodal、Router、Model Tags、各 Channel 与各插件等完整配置项及详细注释。

## 📚 文档

[文档站点](https://nahida-bot.cobaltdev.top)

| 文档 | 内容 |
| ---- | ---- |
| [ARCHITECTURE](docs/architecture/README.md) | 系统架构、分层设计、模块协作 |
| [ROADMAP](docs/ROADMAP.md) | 路线图、阶段规划、验收清单 |
| [DEVELOPMENT](docs/guide/development.md) | 代码风格、测试规范、类型检查 |
| [CONFIGURATION](docs/guide/configuration.md) | 配置指南、环境变量、完整参考 |
| [config.yaml](config.yaml) | 带注释的完整配置参考 |
| [设计文档](docs/design/) | WebUI、Plugin Web Panels、Memory、OneBot Channel、Knowledge Base、Desktop App 等 17 份专题设计 |

## 🧩 内置与示例插件

| 插件 | 说明 |
| ---- | ---- |
| `builtin-commands` | 核心命令、workspace/memory 工具、exec、web_fetch、plan、cron、agent 编排 |
| `mcp` | Model Context Protocol 客户端，接入外部 MCP 工具服务器 |
| `knowledge_base` | 知识库文档导入与 FTS/向量/混合检索 |
| `image_generation` | OpenAI 兼容 Images API 生图，含配额管理 |
| `conversation_joiner` | 群聊上下文观察 + 主动接入决策 |
| `onebot` | OneBot v11 Channel（NapCat/Lagrange/LLOneBot） |
| [plugins/echo](plugins/echo) · [plugins/github-notifier](plugins/github-notifier) · [plugins/rss-notifier](plugins/rss-notifier) | 可独立安装的示例插件（含独立 pyproject） |

插件作者可基于 [`nahida-bot-sdk`](nahida-bot-sdk) 在独立仓库开发并通过 `plugin.yaml` 声明能力、权限与配置 schema。

## 🤝 参考项目

这里的很多智慧结晶，都离不开前辈们的探索：

| 项目 | 参考内容 |
|-----|-------|
| [OpenClaw](https://github.com/openclaw/openclaw) | Agent + Workspace 模式、Gateway-Node 架构灵感、sentinel token 协议 |
| [Codex](https://github.com/openai/codex) | Agent 核心的经典实现，权限管理与沙箱 |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) / [MaiBot](https://github.com/Mai-with-u/MaiBot) | Python LLM bot 领域的老前辈 |
| [nonebot2](https://github.com/nonebot/nonebot2) | 繁荣的插件生态、跨平台消息适配的经典设计 |
| [Lagrange](https://github.com/LagrangeDev/LagrangeV2) / [NapCat](https://github.com/NapNeko/NapCatQQ) / [LuckyLilliaBot](https://github.com/LLOneBot/LuckyLilliaBot) | QQ 协议后端 |
| [pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) / [Tauri](https://v2.tauri.app/) | Desktop 桌宠的 Live2D 渲染与原生壳 |
| [MarkItDown](https://github.com/microsoft/markitdown) | 知识库富文档到 Markdown 的转换 |

## Star History

<a href="https://www.star-history.com/?repos=AI1379%2Fnahida-bot&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=AI1379/nahida-bot&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=AI1379/nahida-bot&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=AI1379/nahida-bot&type=date&legend=top-left" />
 </picture>
</a>

## License

**AGPL-v3.0 License.**
