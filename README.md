# 🍃 Nahida Bot

> ![Avatar](assets/NahidaAvatar1.jpg)
>
> 「这是**摩诃善法大吉祥智慧主**，须弥的**草神大人**，你敢和她对视五秒吗？」

欢迎连接到属于你的私有「虚空终端」！**Nahida Bot** 不仅仅是一个冷冰冰的机器，而是一个 **Agent 为灵魂**、以 **Workspace 为家**，还能通过 **插件随意换装** 的 Python 智能框架哦~ 🌿

QQ 群：529674493

## ✨ 核心天赋

### 💡 设计理念

- **Agent-first（意识主导）**：以 Agent Loop 为中枢，大语言模型（LLM）在这里不是外挂的工具人，而是真正的主导大脑~
- **Workspace-native（专属花盆）**：文件就是上下文，工作空间（Workspace）作为一等公民被温柔对待。
- **Plugin-driven（百变衣橱）**：不需要在核心代码里硬编码，想要什么新能力？装个插件就好啦！
- **Gateway-Node-ready（世界树网络）**：天然支持远程节点和分布式执行，把智慧的枝蔓延伸到各个角落~
- **Ops-friendly（无忧除虫）**：可观测、可诊断、好发布，就算遇到了 Bug 也能轻松捉虫🐞！

## 📈 成长进度（项目状态）

目前小吉祥草王已完成 **Phase 4（Telegram 接入 + Multi-Provider + 内置命令）** 的主体闭环，已经具备真实 Channel、Agent、Workspace、Plugin 与记忆持久化的最小可用运行路径。

### 🌟 已点亮的命座 ✅

- [x] 净土的基石与质量把控（Phase 0）
- [x] 核心生命循环：应用容器、配置文件、事件脉络与观测日志（Phase 1）
- [x] 专属花盆（Workspace）基石：空间管理、文件沙盒隔离、记忆上下文注入（Phase 2.1-2.2）
- [x] 智慧运转（Agent Loop）：消息拼装、模型调用、工具调用闭环、推理链传播（Phase 2.3-2.8）
- [x] 记忆流转与梦境刻录：SQLite 会话记忆与关键词检索（Phase 2.5）
- [x] 插件系统：Manifest、Loader、权限检查、生命周期、命令与工具注册（Phase 3）
- [x] 接引通道：Telegram 长轮询、消息标准化、媒体降级、出站 HTML 转换（Phase 4）
- [x] 内置命令：`/reset`、`/new`、`/status`、`/model`、`/help`（Phase 4.5）

### 🚧 正在进行的光合作用

- [ ] Provider 模型切换增强：同一 Provider 下按请求覆盖具体 model，而不是只切 provider slot。
- [ ] Workspace Sandbox 安全增强：符号链接、TOCTOU、文件大小、特殊文件对象等防护。
- [ ] 插件配置、MockBotAPI 和 SDK 分离整理。
- [ ] Gateway 与 Node 分布式执行闭环。

### 📜 未来的须弥建设计划

- [ ] 虚空终端的广域部署（Gateway 与 Node 分布式）（Phase 5）
- [ ] 漂亮的可视化面板与教令院运维工具（WebUI）（Phase 6）

想要了解更详细的须弥建设蓝图？请翻阅 [ROADMAP.md](docs/ROADMAP.md) 吧~

## 🏛️ 虚空系统架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    世界树树冠 (Interface Layer)             │
│                   CLI / WebUI / API                         │
├─────────────────────────────────────────────────────────────┤
│                  世界树树枝 (Gateway-Node Layer)            │
│              远程节点通讯 / 智慧分布式网络                  │
├─────────────────────────────────────────────────────────────┤
│                    挂件与神之眼 (Plugin Layer)              │
│          插件加载 / 权限管理 / 外部接引通道 / 道具注册      │
├─────────────────────────────────────────────────────────────┤
│                    智慧主脑 (Agent Layer)                   │
│        LLM 推理循环 / 梦境上下文机制 / 记忆 / 模型连接抽象  │
├─────────────────────────────────────────────────────────────┤
│                  专属温室 (Workspace Layer)                 │
│            工作空间管理 / 安全文件沙盒 / 记忆浇灌           │
├─────────────────────────────────────────────────────────────┤
│                     净土核心 (Core Layer)                   │
│         应用生命树 / 法则配置 / 事件脉搏 / 观测日志 / 异常  │
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

# 类型检查与单元测试，可选
uv run pyright
uv run pytest

uv run nahida-bot start
```

### CLI 命令

```bash
nahida-bot version
nahida-bot start [--debug]
nahida-bot config
nahida-bot doctor
```

## 📚 文档

- [ARCHITECTURE](docs/architecture/README.md)
- [ROADMAP.md](docs/ROADMAP.md)
- [DEVELOPMENT.md](docs/DEVELOPMENT.md)

## 🤝 参考项目

这里的很多智慧结晶，都离不开前辈们的探索：

| 项目 | 参考内容 |
|-----|-------|
| OpenClaw | Agent + Workspace 的模式、Gateway-Node 架构灵感 |
| AstrBot | Python LLM bot 领域的重量级项目 |
| nonebot2 | 繁荣的插件生态、跨平台消息适配的绝佳设计 |
| LiteLLM | 多 Provider 模型的优雅抽象和防报错平权 |

## License

**DO WHAT THE FUCK YOU WANT TO.**
