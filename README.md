# Nahida Bot

> ![Avatar](assets/NahidaAvatar1.jpg)
>
> 这是**摩诃善法大吉祥智慧主**，须弥的**草神大人**，你敢和她对视五秒吗？

一个以 **Agent 为核心**、以 **Workspace 为中心**、可通过 **插件扩展** 的 Python Bot 框架。

## 核心特性

### 设计理念

- **Agent-first**：以 Agent Loop 为中枢，而不是把 LLM 当外挂
- **Workspace-native**：文件即上下文，工作空间是一等对象
- **Plugin-driven**：能力扩展通过插件，不通过核心硬编码
- **Gateway-Node-ready**：天然支持远程节点和分布式执行
- **Ops-friendly**：可观测、可诊断、可发布

### 技术栈

| 领域 | 技术选型 |
|-----|---------|
| 运行时 | Python 3.12+ / asyncio |
| 类型系统 | Pydantic v2 + pyright |
| Web 框架 | FastAPI + Uvicorn |
| 数据存储 | SQLite + aiosqlite |
| CLI | Typer + Rich |
| 日志 | Structlog |
| 包管理 | uv |

## 项目状态

当前处于 **Phase 2**（Agent 与 Workspace 联合阶段），核心运行时已可用。

### 已完成 ✅

- [x] 项目地基与质量闸门（Phase 0）
- [x] 核心运行时：应用容器、配置、事件、日志（Phase 1）
- [x] Workspace 基线：空间管理、文件沙盒、上下文注入（Phase 2.1-2.2）
- [x] Agent Loop：消息组装、模型调用、工具协议（Phase 2.3）
- [x] Provider 抽象：OpenAI 兼容接口、错误归一化（Phase 2.3）
- [x] Provider 感知 Token 预算（Phase 2.3）

### 进行中 🚧

- [ ] Tool Calling 协议闭环（Phase 2.4）
- [ ] 记忆模型与持久化（Phase 2.5）
- [ ] Workspace Sandbox 安全增强（Phase 2.7）⚠️
- [ ] Provider 响应健壮性（Phase 2.8）

### 规划中 📋

- [ ] 插件系统与 Channel 接口（Phase 3）
- [ ] 平台接入实现（Phase 4）
- [ ] Gateway 与 Node 分布式（Phase 5）
- [ ] WebUI 与运维工具（Phase 6）

详细路线图见 [ROADMAP.md](docs/ROADMAP.md)。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    Interface Layer                          │
│                   CLI / WebUI / API                         │
├─────────────────────────────────────────────────────────────┤
│                  Gateway-Node Layer                         │
│              远程节点通信 / 分布式执行                         │
├─────────────────────────────────────────────────────────────┤
│                    Plugin Layer                             │
│          插件加载 / 权限管理 / Channel 接口 / 工具注册          │
├─────────────────────────────────────────────────────────────┤
│                    Agent Layer                              │
│        LLM 推理循环 / 上下文管理 / 记忆 / Provider 抽象        │
├─────────────────────────────────────────────────────────────┤
│                  Workspace Layer                            │
│            工作空间 / 文件沙盒 / 上下文注入                     │
├─────────────────────────────────────────────────────────────┤
│                     Core Layer                              │
│         应用容器 / 生命周期 / 配置 / 事件 / 日志 / 异常         │
└─────────────────────────────────────────────────────────────┘
```

详细架构设计见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 快速开始

### 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
# 克隆仓库
git clone https://github.com/your-org/nahida-bot.git
cd nahida-bot

# 安装依赖
uv sync

# 运行类型检查
uv run pyright

# 运行测试
uv run pytest

# 启动应用
uv run nahida-bot start
```

### CLI 命令

```bash
nahida-bot version          # 显示版本
nahida-bot start [--debug]  # 启动应用
nahida-bot config           # 显示配置
nahida-bot doctor           # 诊断检查
```

## 目录结构

```
nahida_bot/
├── core/           # 核心层：应用容器、配置、事件、日志
├── workspace/      # 工作空间：空间管理、文件沙盒
├── agent/          # Agent 层：推理循环、上下文、Provider
│   └── providers/  # Provider 实现：OpenAI 兼容等
├── plugins/        # 插件系统（规划中）
├── gateway/        # Gateway 服务（规划中）
├── node/           # Node 客户端（规划中）
├── db/             # 数据库层（规划中）
└── cli/            # 命令行接口
```

## 文档

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - 架构设计文档
- [ROADMAP.md](docs/ROADMAP.md) - 开发路线图
- [DEVELOPMENT.md](docs/DEVELOPMENT.md) - 开发指南

## 参考项目

| 项目 | 借鉴点 |
|-----|-------|
| OpenClaw | Agent + Workspace 模式、Gateway-Node 架构 |
| AstrBot | 项目初始化、配置日志、Dashboard 设计 |
| nonebot2 | 插件生态、消息适配设计 |
| LiteLLM | 多 Provider 抽象和错误兼容 |
| OneBot/NapCat | Channel 协议设计、多通信方式支持 |

## 安全说明

⚠️ **重要**：当前 Workspace Sandbox 实现存在已知安全风险（符号链接攻击、TOCTOU 等），Phase 2.7 将完成安全加固。在生产环境使用前，请确保完成安全增强。

详见 [ROADMAP.md](docs/ROADMAP.md) 第 8 节"风险与约束"。

## License

DO WHAT THE FUCK YOU WANT TO.
