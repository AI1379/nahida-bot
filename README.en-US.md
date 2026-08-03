

# 🍃 Nahida Bot

> ![Avatar](assets/NahidaAvatar1.jpg)
>
> "This is the **Great Wise One of Maha Karuna Dharma**, the God of Wisdom **Buer**, the **Dendro Archon** of Sumeru. Dare you look her in the eyes for five seconds?"

Welcome to your private "Akademiya Terminal"! **Nahida Bot** is not just a cold machine, but a Python smart framework with an **Agent as its soul**, a **Workspace as its home**, capable of growing into a **Live2D Desktop Pet form**, and able to **freely change outfits via plugins**~ 🌿

Docs: [Docs](https://nahida-bot.cobaltdev.top)

[![QQ 群](https://img.shields.io/badge/QQ_Group-529674493-green?logo=qq)](https://qm.qq.com/q/rXP8DKCyRi) [![Netlify Status](https://api.netlify.com/api/v1/badges/a9eafce9-b879-47be-8220-f4eb728ead1a/deploy-status)](https://app.netlify.com/projects/nahida-bot/deploys)

## ✨ Core Talents

### 💡 Design Philosophy

- **Agent-first (Consciousness-Driven)**: Centered around the Agent Loop, the Large Language Model (LLM) here is not an external tool, but the true mastermind driving the system~
- **Workspace-native (Dedicated Workspace)**: Files are context, and the Workspace is treated as a first-class citizen with care.
- **Plugin-driven (Versatile Wardrobe)**: No need to hardcode new features into the core. Want a new capability? Just install a plugin!
- **Multi-Provider (Unified Backend Support)**: Supports OpenAI Compatible (including Responses API), DeepSeek, Anthropic Claude, GLM, Groq, Minimax, and various other LLM backends. Switch providers seamlessly at runtime~
- **Multi-Channel (Multi-Platform Routing)**: Telegram Bot + Milky QQ (Lagrange.Milky) + OneBot v11/v12 (NapCat/Lagrange/LLOneBot), unified message standardization and ChannelService protocol~
- **Desktop Pet (Dreamlike Avatar)**: Edge-hiding desktop pet built with Tauri + Rust + Vue 3 + Live2D (PixiJS). Summoned from the screen corner on mouse hover / Gateway push / CRON trigger, with local TTS voice support~
- **Multimodal (Omnimodal Image Understanding)**: Native vision image understanding, automatic fallback to text description, or `image_understand` tool. Three modes adapt automatically~
- **Memory & Retrieval (Dream Engraving)**: SQLite session memory + FTS keyword search + vector search + hybrid search + LLM memory consolidation; a separate Document Store keeps knowledge bases and memories organized~
- **Knowledge Base (Sumeru Library)**: Built-in knowledge base plugin for importing PDF/Word/PPT/Excel documents. Converts via MarkItDown, chunks, supports FTS/vector/hybrid retrieval, and optional embeddings~
- **Agent Orchestration (Sub-Agent Collaboration)**: The main Agent can spawn sub-Agents to execute background tasks, supporting the full lifecycle of spawn / wait / stop~
- **Cron & Dreaming (Hourglass Scheduler)**: Scheduled task dispatch + memory dreaming (LLM-driven periodic memory organization and consolidation)~
- **MCP Support (External Magic)**: Model Context Protocol client integration, connecting to external MCP tool servers~
- **Gateway & WebUI (World Tree Console)**: FastAPI REST API + Vue 3 SPA ops panel + SSE real-time event pushing; password/OTP login, visual config management, CRON/Session/File/Knowledge Base/Plugin/Skills/Usage management~
- **Ops-friendly (Worry-Free Debugging)**: Observable, diagnosable, and easy to deploy. Even when bugs appear, they can be caught easily🐞!

## 📈 Growth Progress (Project Status)

Nahida Bot has currently completed **Phase 4 Full Loop + WebUI Core + Desktop Pet + OneBot Channel**: Three channels (Telegram / Milky QQ / OneBot), Multi-Provider, built-in commands/tools/plugin system, Sub-Agent orchestration, Multimodal support, Scheduler & Memory Dreaming, Knowledge Base & Image Generation plugins, Gateway REST API, WebUI ops panel (Vue 3), Desktop Live2D Pet, SSE real-time events, and login/permission systems are all operational.

### 🌟 Ascended Constellations (Completed Features) ✅

- [x] Foundation of the Pure Land & Quality Assurance (Phase 0)
- [x] Core Life Cycle: App container, layered configuration, event bus & observability logs (Phase 1)
- [x] Dedicated Workspace: Space management, file sandbox, instruction injection (Phase 2.1-2.2)
- [x] Wisdom Engine (Agent Loop): Message assembly, model invocation, tool loop, reasoning chain propagation (Phase 2.3-2.4)
- [x] Memory Flow: SQLite session memory, FTS search, vector search, hybrid search (Phase 2.5)
- [x] Reasoning Chain Robustness: Multi-backend reasoning extraction & context backflow for OpenAI/DeepSeek/Claude, Anthropic reasoning effort + 1M context support (Phase 2.8)
- [x] Omnimodal Image Understanding: Native vision image upload, fallback auto-description/tool mode, MediaCache/MediaResolver (Phase 2.9)
- [x] Plugin System: Manifest declaration, Loader discovery & loading, permission checks, lifecycle isolation, command & tool registration (Phase 3.1-3.6)
- [x] Channel Gateway Telegram: Long polling, message standardization, HTML/Markdown conversion, group @mention, media fallback (Phase 4.1-4.5)
- [x] Channel Gateway Milky QQ: Lagrange.Milky WebSocket event stream, message segment modeling, group trigger strategy, merged forward parsing (Phase 4.6)
- [x] Channel Gateway OneBot: Unified abstraction for v11/v12, forward WebSocket + WebHook, CQ code & array segment normalization
- [x] Multi-Provider: Per-request model override, Provider type runtime registration, pre/post-agent staged loading, OpenAI Responses API
- [x] MCP Integration: Model Context Protocol client, tool adaptation, connection management
- [x] Knowledge Base Plugin: Document import (MarkItDown rich-doc conversion), chunking, FTS/vector/hybrid search, optional embedding, SQLite Document Store
- [x] Image Generation Plugin: OpenAI-compatible Images API backend, `/draw`·`/生图` commands, 24h rolling quota management, automatic attachment sending
- [x] Proactive Group Join Plugin (Conversation Joiner): Observed-only group context monitoring + engagement state machine to decide when to naturally join conversations (in-memory MVP)
- [x] Desktop Pet: Tauri + Rust + Vue 3 + Live2D, edge-hiding window, proximity detection, chat bubble/input box, local TTS playback pipeline
- [x] Sub-Agent Orchestration: Spawn sub-Agents, BackgroundTask ledger, policy hooks, parent-child session management (Phase 3.8 core)
- [x] Memory & Document Storage: Separated Memory Store (consolidation/markdown/scope) + Document Store, persistent memory isolated by chat/global scope
- [x] Centralized TaskManager: Unified management of asyncio task lifecycle & graceful shutdown
- [x] Scheduled Dispatch: Cron scheduled tasks + Memory Dreaming LLM memory consolidation
- [x] Session-Level Reasoning Settings: `/reasoning on|off|effort <level>|reset`
- [x] Group Context Injection: Observed-only message recording + inject recent group context on trigger
- [x] 12 Built-in Commands: `/reset`, `/new`, `/status`, `/model`, `/reasoning`, `/help`, `/memory`, `/agents`, `/agent_stop`, `/agent_wait`, `/cron`, `/stop`
- [x] 16+ Built-in Tools: `workspace_read/write`, `send_local_attachment`, `memory_read/write`, `exec`, `web_fetch`, `plan`, `cron_*`, `agent_*`, `image_understand`, plus plugin tools for KB search & image generation
- [x] Gateway REST API: `/api/health`, `/api/status`, `/api/send`, `/api/sessions`, `/api/cron` (global management), `/api/config` (read/write/validate/backup), `/api/files` (workspace file management), `/api/kb` (KB import & search), `/api/plugins`, `/api/skills`, `/api/tokens` (usage), `/api/messages`, `/api/auth` (login/logout/session), `/api/events/stream` (SSE)
- [x] WebUI Ops Panel: Vue 3 + Vite + shadcn-vue + Reka UI, homepage status overview, config page (YAML edit/validate/save/backup), CRON management, session group browsing, file management, KB import, plugin management, Skills, usage stats, system logs, about page
- [x] WebUI Auth System: Admin password (Argon2id) + Session Cookie + login rate limiting; Bearer Token compatible for scripts/API calls
- [x] SSE Real-time Events: `status.updated`, `usage.recorded`, `cron.*`, `session.updated`, `config.saved`, `file.updated`
- [x] Usage Ledger: SQLite persistent token stats (input/output/cached/reasoning), supports aggregation by provider/model/session/source_tag
- [x] Cron Session Modes: `main` (injects into main session), `isolated` (independent session), `named` (persistent named session, cross-run context accumulation)
- [x] Reply Signal Protocol: `NO_REPLY` silent suppression + `HEARTBEAT_OK` heartbeat idle protection
- [x] SDK Independent Package: `nahida-bot-sdk` as a workspace member, plugin authors can use stable APIs to develop & publish
- [x] 17 Design Docs: Agent Core, ChatAddress/SessionID, Cross-Session, Memory System/Scoping, Agent Compaction, Model Routing, Runtime Settings, WebUI, Plugin Web Panels, Cron/WebAPI, OneBot Channel, Conversation Joiner, Knowledge Base, Desktop App, Person Identity, MCP Dynamic Servers, Tool-Produced Image Media

### 🚧 Ongoing Photosynthesis

- [ ] OneBot Channel Finalization: Reverse WebSocket, production-ready WebHook, multi-account support & broader action coverage.
- [ ] Desktop Pet Refinement: Notification/Pomodoro/CRON trigger strategy polishing, cross-platform packaging & signing.
- [ ] KB & Memory Retrieval Merge Refactor: Unify Document Store & Memory retrieval models, introduce LLM summary compression & semantic recall (Phase 2.5b).
- [ ] Proactive Group Join Plugin Persistence, management UI & formal `group_observe_mode`.
- [ ] Media artifact registration, auto-injection & cross-turn persistence for tool-produced images.
- [ ] WebUI Advanced Security: Chat OTP login, deployment modes (loopback/https/http_emergency), CSRF protection.
- [ ] Workspace Sandbox Security Enhancements: Symlink, TOCTOU, file size, special file object protections.

### 📜 Future Development Plans

- [ ] Gateway & Node Distributed Deployment (Phase 5): Node registration, heartbeat, remote execution protocol, WebSocket RPC, remote nodes running heavy models.
- [ ] WebUI Plugin Page Surface: Plugins declare WebUI panels via manifest, loaded in sandbox iframes.
- [ ] Person Identity System: Cross-session user/entity recognition & profile accumulation.
- [ ] MCP Dynamic Servers: Runtime add/remove MCP servers, hot-loading tools.
- [ ] Lightweight Memory Graph Layer: Entity/relation extraction, topic clustering, GraphRAG-style global search.

Want to know the detailed development blueprint? Check out [ROADMAP.md](docs/ROADMAP.md)~

## 🏛️ Akademiya System Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                World Tree Canopy (Interface Layer)           │
│   CLI (typer+rich) + WebUI (Vue 3 SPA) + Desktop (Tauri Pet) │
├──────────────────────────────────────────────────────────────┤
│              World Tree Branches (Gateway-Node Layer)        │
│    FastAPI Gateway (REST + SSE) / Node Distributed Network (Planned) │
├──────────────────────────────────────────────────────────────┤
│                   Charms & Vision (Plugin Layer)             │
│  Plugin loading/permissions/command & tool registration/Channel integration/MCP/KB/Image Gen/Group Chat │
├──────────────────────────────────────────────────────────────┤
│                   Wise Core (Agent Layer)                    │
│ Agent Loop / Sub-Agent Orchestration / Memory & Doc Retrieval / Multimodal / Provider │
├──────────────────────────────────────────────────────────────┤
│                  Dedicated Greenhouse (Workspace Layer)      │
│          Workspace Management / Secure File Sandbox / Instruction File Injection │
├──────────────────────────────────────────────────────────────┤
│                   Pure Land Core (Core Layer)                │
│    App Lifecycle / Layered Config / Event Bus / Session Mgmt / Structured Logs │
└──────────────────────────────────────────────────────────────┘
```

Detailed system diagrams are stored in the Akademiya's [ARCHITECTURE](docs/architecture/README.md).

## 🚀 Boot Terminal (Quick Start)

### Environment Requirements

- Python 3.12+
- [astral-uv](https://docs.astral.sh/uv/)
- [pnpm](https://pnpm.io/) (for WebUI frontend / Desktop Pet build)
- [Rust](https://www.rust-lang.org/) + [Tauri 2](https://v2.tauri.app/) (required only for packaging the Desktop Pet)

### Installation

```bash
git clone https://github.com/AI1379/nahida-bot.git
cd nahida-bot

# Python Backend
uv sync

# For knowledge base import of PDF, Word, PowerPoint, Excel, etc.
uv sync --extra document-import

# For Telegram Channel, install optional dependencies
uv sync --group telegram

# WebUI Frontend (Optional, but recommended)
cd webui
pnpm install
pnpm build    # Outputs to webui/dist/, automatically mounted when Gateway starts
cd ..

# Type checking and unit tests, optional
uv run pyright
uv run pytest

# Edit config.yaml to configure LLM Provider and Channel, then start
uv run nahida-bot start
```

#### Desktop Pet (Optional)

```bash
cd desktop
pnpm install
pnpm setup:live2d-core   # Download Live2D Core runtime
pnpm dev:web             # Frontend preview only (Vite, port 1420)
pnpm build:web           # Build frontend assets
# Package desktop client (requires Rust toolchain):
pnpm build               # = tauri build, artifacts in desktop/dist
cd ..
```

The desktop pet connects to the local Gateway via WebSocket by default, staying hidden at the screen edge. You can configure the connection address, Live2D model mapping, and TTS parameters in the desktop client. See [Desktop Design Doc](docs/design/desktop-app.md) for details.

#### Knowledge Base Document Import

The base installation can directly import UTF-8 encoded `.txt`, `.text`, `.md`, and `.markdown` files. For full knowledge base document import capabilities, install the optional dependency:

```bash
uv sync --extra document-import
```

After installation, you can import PDF, DOCX, PPTX, XLS/XLSX, HTML, CSV, JSON, XML, EPUB, Outlook MSG, and Jupyter Notebook files via the WebUI. Rich documents are first converted to Markdown by [Microsoft MarkItDown](https://github.com/microsoft/markitdown), then processed through the existing chunking and retrieval pipeline. The WebUI supports selecting or dragging up to 20 files at once, with a 25 MiB limit per file. Batch imports report results per file, and failed files will not roll back successfully imported ones.

Legacy `.doc` files are not supported; please save them as `.docx` first. Scanned PDFs or image-heavy documents may not extract enough text; the current integration does not enable MarkItDown third-party plugins or cloud OCR.

### CLI Commands

```bash
nahida-bot version                # Display version info
nahida-bot start [--debug]        # Start app (includes Gateway + WebUI)
nahida-bot config                 # Display current config
nahida-bot config schema          # Display config schema (includes plugin schemas)
nahida-bot config validate        # Validate config file
nahida-bot doctor                 # Run diagnostic checks
nahida-bot gateway                # Start Gateway only (WebAPI + WebUI)
```

### Minimal Configuration Example

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

# Milky QQ Channel (Optional)
# milky:
#   base_url: "http://127.0.0.1:3000"
#   access_token: "${MILKY_ACCESS_TOKEN}"
#   group_trigger_mode: "mention"

# OneBot Channel (Optional, defaults to v11 forward WS)
# onebot:
#   protocol_version: "v11"
#   ws_url: "ws://127.0.0.1:3001"
#   ws_access_token: "${ONEBOT_ACCESS_TOKEN:}"
#   webhook_enabled: false
#   webhook_host: "127.0.0.1"
#   webhook_port: 6186
```

Configuration supports `${VAR}` and `${VAR:default}` environment variable interpolation, with optional `.env` file loading. `config.yaml` contains complete configuration options and detailed comments for Agent Loop, Context Budget, Scheduler, Memory (FTS/Vector/Embedding), Multimodal, Router, Model Tags, all Channels, and all plugins.

## 📚 Documentation

[Documentation Site](https://nahida-bot.cobaltdev.top)

| Document | Content |
| ---- | ---- |
| [ARCHITECTURE](docs/architecture/README.md) | System architecture, layered design, module collaboration |
| [ROADMAP](docs/ROADMAP.md) | Roadmap, phase planning, acceptance checklist |
| [DEVELOPMENT](docs/guide/development.md) | Code style, testing standards, type checking |
| [CONFIGURATION](docs/guide/configuration.md) | Configuration guide, environment variables, complete reference |
| [config.yaml](config.yaml) | Fully commented configuration reference |
| [Design Docs](docs/design/) | 17 specialized design docs covering WebUI, Plugin Web Panels, Memory, OneBot Channel, Knowledge Base, Desktop App, etc. |

## 🧩 Built-in & Example Plugins

| Plugin | Description |
| ---- | ---- |
| `builtin-commands` | Core commands, workspace/memory tools, exec, web_fetch, plan, cron, agent orchestration |
| `mcp` | Model Context Protocol client, connects to external MCP tool servers |
| `knowledge_base` | Knowledge base document import & FTS/vector/hybrid retrieval |
| `image_generation` | OpenAI-compatible Images API generation, includes quota management |
| `conversation_joiner` | Group context observation + proactive join decision |
| `onebot` | OneBot v11/v12 Channel (NapCat/Lagrange/LLOneBot) |
| [plugins/echo](plugins/echo) · [plugins/github-notifier](plugins/github-notifier) · [plugins/rss-notifier](plugins/rss-notifier) | Independently installable example plugins (with separate pyproject) |

Plugin authors can develop in separate repositories based on [`nahida-bot-sdk`](nahida-bot-sdk) and declare capabilities, permissions, and config schemas via `plugin.yaml`.

## 🤝 Reference Projects

Much of the wisdom here relies on the exploration of predecessors:

| Project | Reference/Inspiration |
|-----|-------|
| [OpenClaw](https://github.com/openclaw/openclaw) | Agent + Workspace pattern, Gateway-Node architecture inspiration, sentinel token protocol |
| [Codex](https://github.com/openai/codex) | Classic implementation of Agent core, permission management & sandbox |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) / [MaiBot](https://github.com/Mai-with-u/MaiBot) | Veterans in the Python LLM bot space |
| [nonebot2](https://github.com/nonebot/nonebot2) | Thriving plugin ecosystem, classic cross-platform message adaptation design |
| [Lagrange](https://github.com/LagrangeDev/LagrangeV2) / [NapCat](https://github.com/NapNeko/NapCatQQ) / [LuckyLilliaBot](https://github.com/LLOneBot/LuckyLilliaBot) | QQ protocol backends |
| [pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) / [Tauri](https://v2.tauri.app/) | Desktop pet Live2D rendering & native shell |
| [MarkItDown](https://github.com/microsoft/markitdown) | Rich document to Markdown conversion for knowledge base |

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
