# Nahida Bot 架构文档

> 本文档只描述 Python 方案架构，不包含 Rust 方案。

## 1. 架构目标

Nahida Bot 的核心目标：

- **Agent-first**：以 Agent Loop 为中枢，而不是把 LLM 当外挂。
- **Workspace-native**：文件即上下文，工作空间是一等对象。
- **Plugin-driven**：能力扩展通过插件，不通过核心硬编码。
- **Gateway-Node-ready**：天然支持远程节点和分布式执行。
- **Ops-friendly**：可观测、可诊断、可发布。

## 2. 总体分层

推荐的逻辑分层如下：

1. **Core Layer** — 应用容器、生命周期、配置、事件、异常
2. **Workspace Layer** — 工作空间、文件沙盒、上下文注入
3. **Agent Layer** — LLM 推理循环、上下文管理、记忆、Provider 抽象
4. **Plugin Layer** — 插件加载、权限管理、工具注册、**Channel 接口** ⚠️
5. **Gateway-Node Layer** — 远程节点通信、分布式执行
6. **Interface Layer** — CLI / WebUI / API

> **关键改进：Channel 不再是独立层，而是作为 Plugin Layer 的标准接口之一。** 当前实现采用普通 Plugin 暴露 `ChannelService` 协议，通过 manifest 声明通信能力（HTTP Server/Client、WebSocket、SSE）。这样可以：
>
> - 复用插件的权限系统、生命周期管理
> - 灵活支持多种平台接入方式
> - 允许第三方开发 Channel 插件

依赖方向约束：

- 上层可依赖下层，下层不可反向依赖上层。
- `core` 不依赖任何具体平台实现。
- `agent` 不依赖具体 `plugin` 或 `channel` 实现。
- `plugins` （包括 channel service plugin）通过协议接入，不直接侵入 `core` 内部状态。
- Channel 的具体实现（如 Telegram、QQ）通过标准 Plugin 接口加载，无需核心改动。

## 文档目录

| 文档 | 内容 |
|------|------|
| [directory-structure.md](directory-structure.md) | 目录结构、模块边界与文件组织 |
| [runtime-flows.md](runtime-flows.md) | 核心运行流程与模块契约 |
| [data-and-state.md](data-and-state.md) | 数据状态边界、Workspace/Agent/Memory 联合设计 |
| [sandbox-security.md](sandbox-security.md) | Workspace 沙盒安全增强方案 |
| [provider-architecture.md](provider-architecture.md) | Provider 多后端架构、格式调研与实现细节 |
| [agent-orchestration.md](agent-orchestration.md) | Agent/Subagent 编排、后台任务、跨会话管理与本地队列设计 |
| [plugin-system.md](plugin-system.md) | Plugin 系统完整设计（SDK、Manifest、生命周期、权限、事件集成） |
| [channel-plugin.md](channel-plugin.md) | ChannelService 设计与通信协议 |
| [security-observability.md](security-observability.md) | 安全基线与可观测性要求 |
| [priorities-and-strategy.md](priorities-and-strategy.md) | 模块优先级与开发策略 |
| [event-system.md](event-system.md) | 类型安全事件系统设计 |

## 与 ROADMAP 的关系

- [docs/ROADMAP.md](../ROADMAP.md)：回答"做什么、做到什么程度"。
- [docs/architecture/](.)：回答"怎么分层、模块如何协作、先做哪些模块、Channel 作为 Plugin 的设计"。

两份文档应同步更新，不允许只改其中一份。
