# Cron 系统优化与 WebAPI 架构规划

> 记录时间：2026-05-16
> 最近更新：2026-05-17
> 状态：进行中
> 相关文档：
>
> - [ROADMAP.md](../ROADMAP.md#phase-210---回复信号协议reply-signal-protocol)
> - [runtime-flows.md](../architecture/runtime-flows.md)
> - 讨论来源：nahida-bot 与 OpenClaw CRON 系统对比分析

## 1. 背景

通过对比 nahida-bot 和 OpenClaw 的 CRON 系统，识别出以下几个关键差距和优化方向：

- nahida-bot 的 cron 是"定时往现有 session 塞一条消息"，缺少 session 隔离和多投递目标
- OpenClaw 暴露了完整的 Gateway WebAPI（HTTP + WebSocket RPC），使 CLI、WebUI、脚本都能统一交互
- OpenClaw 的 LLM 在处理确定性 cron 任务时，选择绕过 agent 机制、直接写脚本调 API 发消息——暴露了"纯 LLM turn 对确定性任务来说太重"的问题
- 哨兵值（sentinel token）机制在 cron、心跳、群聊降噪等场景中有实际价值

## 2. 架构分层

从 OpenClaw 的实践可以归纳出三层任务模型：

```
┌──────────────────────────────────────────────────┐
│ Layer 3: Agent Cron（需要 LLM 判断）               │
│   例: "看看今天的新闻，挑我感兴趣的总结"             │
│   流程: cron → agent → 工具调用 → LLM 筛选 → 投递   │
├──────────────────────────────────────────────────┤
│ Layer 2: 脚本 + WebAPI（确定性 + 需要投递）         │
│   例: "每天9点查提醒事项发给我"                     │
│   流程: system crontab → 脚本 → WebAPI.send()      │
├──────────────────────────────────────────────────┤
│ Layer 1: 系统级（跟 bot 无关）                     │
│   例: 日志轮转、数据库备份                          │
│   流程: system crontab → 脚本 → 完事                │
└──────────────────────────────────────────────────┘
```

各层职责明确：Agent Cron 处理需要 LLM 判断的任务，确定性任务交给 system crontab + WebAPI。

## 3. 优化方向

### 3.1 WebAPI 统一投递接口（高优先级）

**目标**：暴露 HTTP REST 端点，让脚本、CLI、未来 WebUI 都能通过标准方式与 bot 交互。

**OpenClaw 参考**：

- Gateway 同时暴露 HTTP（REST + 静态文件）和 WebSocket RPC
- 120+ 个 RPC 方法覆盖 chat、sessions、cron、config、agents 等所有功能
- CLI 命令通过 WebSocket RPC 调用运行中的 Gateway（不需要启动新进程）
- `openclaw message send` 是特例——直接 import 渠道插件发消息，不需要 Gateway

**nahida-bot 最小 API 集**：

```
POST /api/send              — 发消息到指定 session
GET  /api/sessions          — 列出 sessions
GET  /api/sessions/{id}     — 获取 session 历史
GET  /api/health            — 健康检查
GET  /api/cron              — 列出 cron jobs
POST /api/cron              — 创建 cron job
```

先做 REST，后续再加 WebSocket 支持流式响应和实时事件推送。

**实现要点**：

- 基于 FastAPI / aiohttp 暴露 HTTP 端点
- 认证：token-based（支持 header 和 query param）
- 投递接口复用现有的 `Channel.send_message()` 内部 API
- 脚本可通过 `curl` / `requests` 直接调用，不需要 WebSocket 客户端

### 3.2 Agent Cron 增强（中优先级）

**目标**：在现有 cron 基础上增加 session 隔离和灵活投递。

**OpenClaw 参考**：

OpenClaw 有 4 种 session target 模式：

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `main` | 注入系统事件到主 session | 轻量提醒、需要 chat 上下文的任务 |
| `isolated` | 独立 `cron:<jobId>` session | 独立任务，不污染对话，可跨 run 积累自身上下文 |
| `current` | 绑定创建时的 session | 上下文相关任务 |
| `session:<id>` | 持久命名 session，跨 run 积累上下文 | 长期监控任务 |

**nahida-bot 实现状态**：

| Phase | 模式 | 状态 |
|-------|------|------|
| Phase 1 | `main` | ✅ 已实现（2026-05-18），`session_mode="main"` 显式化 |
| Phase 2 | `isolated` | ✅ 已实现（2026-05-18），session 为 `{session_key}:cron:{job_id}`，与主 chat 历史隔离 |
| Phase 3 | `session:<id>` | 🔜 待实现，持久命名 session，跨 run 积累上下文 |

**设计细节**：

- `CronJob.session_mode: Literal["main", "isolated"]`（默认 `"main"`，向后兼容）
- `main` 模式：复用 `router.get_active_session_id()` → 注入 chat 历史，turn 持久化到主 session
- `isolated` 模式：session_id = `f"{session_key}:cron:{job_id}"`，独立的 session 不加载 chat 历史，agent turn 持久化到 cron session
- 两种模式的响应都通过 `Channel.send_message()` 投递到原 chat（除非被哨兵值抑制）
- `session_mode` 仅在创建时设置，不可通过 `update_job()` 修改
- 数据库迁移：`ALTER TABLE cron_jobs ADD COLUMN session_mode TEXT NOT NULL DEFAULT 'main'`

### 3.3 哨兵值 / 回复信号协议（高优先级）

**结论：有必要实现。**

哨兵值在以下场景中有明确价值：

1. **Agent Cron 静默执行** — cron 触发的任务执行成功但无需通知用户时，LLM 回复 `NO_REPLY`，抑制空消息
2. **心跳空转** — scheduler 心跳检查无事可做时，`HEARTBEAT_OK` 抑制回复
3. **群聊噪音控制** — 不是每条消息都需要 bot 回应，`NO_REPLY` 让模型自行判断
4. **工具已发送** — Agent 通过消息工具已直接发送回复，主回复用 `NO_REPLY` 避免重复

详细设计已在 [ROADMAP Phase 2.10](../ROADMAP.md#phase-210---回复信号协议reply-signal-protocol) 中记录。实现要点：

- `nahida_bot/agent/reply_signals.py`：令牌常量 + 检测函数
- `MessageRouter._dispatch_message()` 中集成检测，命中时跳过 `_send_response()`
- `NO_REPLY` / `HEARTBEAT_OK` 的 assistant turn 不持久化
- System prompt 中注入 Silent Replies 和 Heartbeat 章节
- 配置开关 `enable_silent_reply`，默认开启

**与 cron 的协同**：哨兵值是 Agent Cron（Layer 3）能优雅运行的必要前提。没有它，每个 cron turn 都会产生一条消息，即使执行结果无需通知。

### 3.4 CLI `send` 命令（低优先级）

**目标**：提供 `nahida send --platform telegram --chat 12345 "hello"` 形式的 CLI 命令，供脚本直接调用。

**OpenClaw 参考**：`openclaw message send` 直接 import 渠道插件发消息，不需要 Gateway 运行。

**两种实现路径**：

- **Path A**：直接 import 内部模块（类似 OpenClaw），不依赖 Gateway
- **Path B**：调 WebAPI 的 `POST /api/send`，依赖 Gateway 运行

Path B 更简单且与 WebAPI 统一，推荐先做 Path B。Path A 作为离线模式的后备。

### 3.5 Python exec 调内部函数（低优先级，暂缓）

**目标**：让 Agent 通过 exec 工具直接调用 bot 内部函数（如 `bot.api.get_reminders()`），而非硬编码外部 API 端点。

**风险**：安全沙箱问题，需要严格限制可调用的函数白名单。

**建议**：先完成 WebAPI（3.1）和哨兵值（3.3），再考虑此方向。WebAPI 本身已经解决了"脚本调 API"的需求，exec 内部函数只是锦上添花。

### 3.6 心跳增强（基于现有 interval 模式）

**关键认知**：nahida-bot 现有的 `mode="interval"` 就是 Heartbeat。OpenClaw 的 Heartbeat 在调度层面就是 interval，差异只在执行层的行为。

**现有 interval 模式已具备的**：

- 固定间隔触发（`interval_seconds`）
- 主 session 内执行（复用 chat session）
- SQLite 持久化 + claim 锁
- 失败重试 + 自动停用

**需要增强的执行层行为**：

| 行为 | 当前 interval cron | 增强后 |
|------|---|---|
| 空转处理 | 无——LLM 回什么发什么 | HEARTBEAT_OK → 删除本轮对话记录，用户无感知 |
| 对话记录 | 持久化 | 空转时不持久化本轮 user + assistant turn |
| session updatedAt | 被更新 | 空转时恢复到执行前的值 |
| 批量合并 | 每个 job 独立执行一次 agent turn | 同一 chat 到期的多个 interval job 合并为一次 turn |

**不需要改动的**：

- `CronJob` 模型——不新增 `heartbeat` mode，interval 本身就是
- `CronRepository`——到期判断、claim 逻辑完全复用
- 调度基础设施——poll loop 不变

**批量合并的设计**：

OpenClaw 的核心优势是一个心跳轮询批量处理多个检查项。nahida-bot 可以在 `SchedulerService` 层实现，只影响 `mode="interval"` 的 job：

```python
async def _dispatch_interval_batch(self, platform, chat_id):
    """收集同一 chat 所有到期的 interval job，合并为一次 agent turn"""
    due_jobs = self._repo.claim_due_jobs(...)  # 现有逻辑，只筛选 interval 类型

    interval_jobs = [j for j in due_jobs if j.mode == "interval"]
    other_jobs = [j for j in due_jobs if j.mode != "interval"]

    # 其他类型 job 独立执行（现有逻辑）
    for job in other_jobs:
        self._dispatch_fire(job)

    # interval job 批量合并
    if interval_jobs:
        combined_prompt = "\n".join(f"- {job.prompt}" for job in interval_jobs)
        result = await self._runner.run(
            user_message=f"[Heartbeat] 以下任务到期，请检查：\n{combined_prompt}",
            session_id=self._resolve_main_session(platform, chat_id),
            source_tag="heartbeat",
        )
        # HEARTBEAT_OK → 删除本轮对话记录，恢复 updatedAt
        ...
```

**实施阶段**：

1. **Phase 1**：哨兵值落地（`NO_REPLY` + `HEARTBEAT_OK`），为心跳增强做准备
2. **Phase 2**：SchedulerService 中增加 interval job 批量合并逻辑
3. **Phase 3**：实现空转时的对话记录清理和 updatedAt 恢复

**为什么现在不做**：当前场景（单用户、单聊天）的 interval job 数量少，每个独立执行的开销可接受。批量合并和多信息源监控（邮件、日历、提醒等）适合"个人助手"方向明确后实施。

**为什么不用 OpenClaw 的 HEARTBEAT.md 方案**：OpenClaw 用 markdown 文件存结构化调度数据，本质是把数据库该做的事塞进了文件格式。nahida-bot 已有 `CronJob` + SQLite，天然比解析 markdown 更可靠。

## 4. 实施优先级

| 顺序 | 任务 | 优先级 | 理由 |
|------|------|--------|------|
| 1 | 哨兵值 / 回复信号协议 | 高 | Agent Cron 和群聊降噪的必要前提，改动范围可控 |
| 2 | WebAPI 统一投递接口 | 高 | 所有层都需要的基础设施，脚本化场景的入口 |
| 3 | Agent Cron session 隔离 | 中 | 现有模式够用，isolated 模式是改进 |
| 4 | 心跳系统 | 中（未来） | 场景驱动，"个人助手"方向明确后实施 |
| 5 | CLI send 命令 | 低 | WebAPI 完成后自然衍生 |
| 6 | Python exec 内部函数 | 低 | 锦上添花，安全沙箱要先解决 |

## 5. 不做的事

- **不替代 system crontab** — 调度本身不是 bot 该管的，确定性任务交给系统
- **不做工作流引擎** — "先调 A 再调 B"的 DAG 流程靠 LLM 运行时编排，不预定义
- **不急着做 WebSocket** — 先 REST 够用，流式响应和实时推送是后续需求
- **不用 markdown 文件存心跳任务** — 结构化数据用数据库，不绕路

## 6. 任务清单

### 3.1 WebAPI 统一投递接口

- [ ] 搭建 HTTP 服务器（FastAPI / aiohttp）
- [ ] `POST /api/send` — 发消息到指定 session
- [ ] `GET /api/sessions` — 列出 sessions
- [ ] `GET /api/sessions/{id}` — 获取 session 历史
- [ ] `GET /api/health` — 健康检查
- [ ] `GET /api/cron` — 列出 cron jobs
- [ ] `POST /api/cron` — 创建 cron job
- [ ] token-based 认证（header + query param）
- [ ] 投递接口复用 `Channel.send_message()`

### 3.2 Agent Cron 增强

- [ ] Phase 1：优化 cron turn 的 system prompt 隔离
- [ ] Phase 2：增加 `isolated` 模式，cron turn 用临时 session
- [ ] Phase 3：增加 `session:<id>` 模式，支持跨 run 上下文累积

### 3.3 哨兵值 / 回复信号协议

- [x] 创建 `nahida_bot/core/sentinel.py`，实现 `detect_sentinel()` 三层检测
- [x] 创建 `tests/test_sentinel.py`，覆盖精确匹配、JSON 包络、尾部剥离、正常文本不误判
- [x] `Settings` / `RouterConfigModel` 增加 `enable_silent_reply: bool = True`
- [x] `message_context.py` 增加 `SILENT_REPLY_INSTRUCTION` 和 `HEARTBEAT_INSTRUCTION` 常量
- [x] `SessionRunner._append_envelope_instruction` → `_build_system_prompt`，根据 `source_tag` 注入哨兵/心跳 prompt
- [x] `MessageRouter._run_agent_in_background` 中 `"text"` 和 `"done"` 事件加哨兵检测
- [x] `SchedulerService._do_fire` 中加哨兵检测，抑制空消息发送
- [x] `app.py` 中 `enable_silent_reply` 传递给 `SessionRunner`、`RouterConfig`、`SchedulerService`
- [x] 路由器集成测试（NO_REPLY 抑制、HEARTBEAT_OK 抑制、尾部剥离、禁用、JSON 包络）
- [x] 调度器集成测试（NO_REPLY 抑制、HEARTBEAT_OK 抑制、尾部剥离、禁用）
- [ ] 哨兵 turn 的持久化策略（当前仍正常持久化，可选：空转时不持久化 assistant turn）

### 3.4 CLI `send` 命令

- [ ] 基于 WebAPI `POST /api/send` 实现 `nahida send` 子命令
- [ ] 可选：Path A 离线模式（直接 import 内部模块）

### 3.5 Python exec 调内部函数

- [ ] 设计安全沙箱和可调用函数白名单
- [ ] 实现 exec 工具的基础框架

### 3.6 心跳增强（基于现有 interval 模式）

- [x] Phase 1：哨兵值落地（`NO_REPLY` + `HEARTBEAT_OK`）
- [ ] Phase 2：`SchedulerService` 中增加 interval job 批量合并逻辑
- [ ] Phase 3：空转时删除本轮对话记录，恢复 session updatedAt

- **不替代 system crontab** — 调度本身不是 bot 该管的，确定性任务交给系统
- **不做工作流引擎** — "先调 A 再调 B"的 DAG 流程靠 LLM 运行时编排，不预定义
- **不急着做 WebSocket** — 先 REST 够用，流式响应和实时推送是后续需求
- **不用 markdown 文件存心跳任务** — 结构化数据用数据库，不绕路
