# 数据与状态边界

## 状态分层

- **瞬时态**：请求上下文、流式响应缓冲、工具调用中间态
- **会话态**：聊天历史、当前会话配置、会话级变量
- **长期态**：workspace 文件、长期记忆、插件配置、节点信息

## 存储策略

- 先使用 SQLite 统一落地会话与配置。
- 对外统一通过 repository 接口，不让业务代码直接拼 SQL。
- 文件系统读写全部经过 workspace/sandbox 统一入口。

---

## Phase 2 架构细化（Agent 与 Workspace 联合阶段）

本节对应 ROADMAP 的 Phase 2.x 子阶段，只定义架构约束和模块协作方式。

### Workspace 基线与安全边界

- `workspace.manager` 负责空间生命周期：创建、切换、列举、默认空间选择。
- `workspace.sandbox` 是唯一文件访问入口，必须实现路径归一化和越界拒绝。
- `workspace` 元数据进入独立 repository 层，避免业务逻辑直接操作 SQLite。

建议最小接口：

```python
class WorkspaceManager(Protocol):
    async def ensure_default(self) -> Workspace: ...
    async def create(self, name: str, template: str | None = None) -> Workspace: ...
    async def switch(self, workspace_id: str) -> Workspace: ...


class WorkspaceSandbox(Protocol):
    async def read_text(self, rel_path: str) -> str: ...
    async def write_text(self, rel_path: str, content: str) -> None: ...
```

### 指令注入与上下文构建

- 上下文拼装顺序建议固定：系统基线 -> `AGENTS.md` -> `SOUL.md` -> `USER.md` -> 会话历史 -> 工具回填。
- `agent.context` 只负责拼装和裁剪，不直接访问 provider。
- 上下文预算策略分两层：
  1. 先执行窗口裁剪（保留最近对话与关键系统指令）。
  2. 超预算时再触发摘要压缩（可插拔策略）。

### Agent Loop 与 Provider 适配

- `agent.loop` 维持纯状态机语义：`prepare -> call_provider -> dispatch_tools -> finalize`。
- `agent.providers.base` 固定最小契约：`chat(messages, tools, timeout) -> ProviderResult`。
- Provider 错误必须归一到统一错误码，避免上层针对具体厂商分支。

建议错误码集合（最小可用）：

- `provider_timeout`
- `provider_rate_limited`
- `provider_auth_failed`
- `provider_bad_response`

### Tool Calling 协议

- 工具定义由插件系统注册，但执行编排由 `agent.tools` 统一管理。
- 参数校验建议在执行前完成（Pydantic 模型或等价校验器）。
- 工具执行结果统一封装为结构化消息，区分：
  - `ok`: 是否成功
  - `content`: 供模型消费的结果
  - `meta`: 可观测字段（耗时、工具名、截断标记）
  - `error`: 失败原因（可回退提示）

### 记忆模型与存储抽象

- 短期记忆放在会话上下文层，长期记忆通过 repository 检索。
- SQLite 只作为首个实现，不直接暴露给 `agent.loop`。
- 对话 turn 的 `content` 保存原始文本；消息来源、时间、发送方等给 LLM 使用的 envelope facts 放入 `metadata.message_context`，读取历史时再稳定渲染到 provider context。
- `metadata.message_context` 是 per-turn 状态，不是 session 状态。它应包含消息时间、channel、chat 类型、发送方显示名/ID、短角色标签等可重建字段。
- 普通上下文重建不得动态改写旧 turn 的 envelope。只有 compact/summary 发生时，才允许把多条 turn 重写为一条稳定摘要，并保留时间范围、channel 和关键参与者信息。
- 建议契约：

```python
class MemoryStore(ABC):
  async def ensure_session(self, session_id: str, workspace_id: str | None = None) -> None: ...
    async def append_turn(self, session_id: str, turn: ConversationTurn) -> int: ...
    async def search(self, session_id: str, query: str, limit: int = 5) -> list[MemoryRecord]: ...
    async def get_recent(self, session_id: str, *, limit: int = 50) -> list[MemoryRecord]: ...
    async def evict_before(self, cutoff: datetime) -> int: ...
```

**当前实现**：

- `agent/memory/models.py` — 数据模型（`ConversationTurn`, `MemoryRecord`）。
- `agent/memory/store.py` — `MemoryStore` ABC 契约。
- `agent/memory/sqlite.py` — `SQLiteMemoryStore` 实现（含 `extract_keywords` 工具函数）。
- `db/engine.py` — `DatabaseEngine` 异步 SQLite 引擎。
- `db/repositories/sqlite_memory_repo.py` — `SQLiteMemoryRepository` 纯 SQL 数据访问。

> ⚠️ **架构优化待办**：当前调用链为 `MemoryStore → SQLiteMemoryStore → SQLiteMemoryRepository → DatabaseEngine`，对于仅支持 SQLite 的场景而言存在三层间接。后续应评估是否引入 SQLModel 等 ORM 统一 Repository 与模型层，或在确认无多后端需求后合并中间层。关键词检索目前为简单分词+精确匹配，后续可接入向量检索提升召回。

### 稳定性与可观测性

- 重试仅用于可恢复错误（超时、限流），认证失败默认不重试。
- 关键路径打点：provider 延迟、工具成功率、上下文裁剪次数、最终回复耗时。
- 最小验收闭环需要可追踪 trace_id，确保从 workspace 注入到最终回复可串联。

**当前实现**：

- `agent/metrics.py` — `MetricsCollector`（含 `Trace`、各 Record 类型），支持 `max_traces` 环形缓冲防内存泄漏。
- `agent/loop.py` — Provider 错误回退（`AgentRunResult.error` + `provider_error_template`），每步记录 provider/tool 指标。
- 全链路 UTC-aware ISO8601 时间戳。

> ⚠️ **架构优化待办**：`MetricsCollector` 当前为纯内存聚合，缺少 flush/export 机制。后续需增加 log sink 或 Prometheus exporter，并考虑将 observability 独立为 `agent/observability/` 子包。
