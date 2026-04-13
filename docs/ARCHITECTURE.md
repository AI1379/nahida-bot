# Nahida Bot ARCHITECTURE

> 本文档只描述 Python 方案架构，不包含 Rust 方案。

## 1. 架构目标

Nahida Bot 的核心目标：

- Agent-first：以 Agent Loop 为中枢，而不是把 LLM 当外挂。
- Workspace-native：文件即上下文，工作空间是一等对象。
- Plugin-driven：能力扩展通过插件，不通过核心硬编码。
- Gateway-Node-ready：天然支持远程节点和分布式执行。
- Ops-friendly：可观测、可诊断、可发布。

## 2. 总体分层

推荐的逻辑分层如下：

1. **Core Layer** — 应用容器、生命周期、配置、事件、异常
2. **Workspace Layer** — 工作空间、文件沙盒、上下文注入
3. **Agent Layer** — LLM 推理循环、上下文管理、记忆、Provider 抽象
4. **Plugin Layer** — 插件加载、权限管理、工具注册、**Channel 接口** ⚠️
5. **Gateway-Node Layer** — 远程节点通信、分布式执行
6. **Interface Layer** — CLI / WebUI / API

> **关键改进：Channel 不再是独立层，而是作为 Plugin Layer 的标准接口之一。** 参考 OneBot/NapCat 设计，定义 ChannelPlugin 基类，支持多种通信协议（HTTP Server/Client、WebSocket、SSE）。这样可以：
>
> - 复用插件的权限系统、生命周期管理
> - 灵活支持多种平台接入方式
> - 允许第三方开发 Channel 插件

依赖方向约束：

- 上层可依赖下层，下层不可反向依赖上层。
- `core` 不依赖任何具体平台实现。
- `agent` 不依赖具体 `plugin` 或 `channel` 实现。
- `plugins` （包括 ChannelPlugin）通过协议接入，不直接侵入 `core` 内部状态。
- **ChannelPlugin** 的具体实现（如 Telegram、QQ）通过标准 Plugin 接口加载，无需核心改动。

## 3. 建议目录与模块边界

建议使用如下目录边界（按当前仓库渐进演化）：

```text
nahida_bot/
  core/
    app.py
    config.py
    events.py
    logging.py
    exceptions.py
  workspace/
    manager.py
    sandbox.py
    templates/
  agent/
    loop.py
    context.py
    memory.py
    tools.py
    providers/
      base.py
      openai.py
      registry.py
  plugins/
    manager.py
    loader.py
    manifest.py
    permissions.py
    registry.py
    base.py                      # Channel、Tool、Hook 基类
    builtin/
      __init__.py
      channel.py                 # ChannelPlugin 基类 (Abstract)
      # 具体 Channel 实现（内置插件示例）：
      # - telegram_channel.py
      # - qq_channel.py (via NapCat)
      # - matrix_channel.py
      builtin_tools/
        file_reader.py
        command_executor.py
        web_fetcher.py
        memory_retrieval.py
  gateway/
    server.py
    router.py
    node_manager.py
    protocol.py
  node/
    client.py
    connector.py
    executor.py
  db/
    engine.py
    models.py
    repositories/
  cli/
    main.py
```

### 重点说明

1. **ChannelPlugin 基类** 在 `plugins/base.py` 或专属 `plugins/channel_base.py`
   - 定义标准接口（`handle_inbound_event`、`send_message`、`get_user_info` 等）
   - 声明支持的通信方式（HTTP Server/Client、WebSocket、SSE）
   - 嵌入权限声明和生命周期挂钩

2. **内置 Channel 实现** 在 `plugins/builtin/` 下，作为 ChannelPlugin 实例
   - 每个 Channel 是一个标准 Plugin，有 `plugin.yaml` 和实现代码
   - 通过 Plugin Manager 加载，享受权限隔离和热加载机制

3. **第三方 Channel 插件** 结构相同，可外部贡献
   - 遵循同一的 Plugin 接口契约
   - 无须修改核心代码

## 4. 核心运行流程

### 4.1 消息主流程（通过 ChannelPlugin）

```text
外部平台 (QQ/Telegram/Matrix/etc)
  ↓
ChannelPlugin 接收事件
  ├─ HTTP Server: webhook 推送
  ├─ WebSocket: 双向连接
  ├─ HTTP Client: 轮询或长连接
  └─ SSE: 单向推送
  ↓
InboundMessage 标准化
  ↓
Session Resolver （映射平台用户 -> Bot 会话）
  ↓
Context Builder (workspace 文件注入 + 历史记录)
  ↓
Agent Loop
  ├─ 消息入 LLM
  ├─ Tool calls (optional)
  └─ 最终回复组装
  ↓
OutboundMessage
  ↓
ChannelPlugin 发送
  ├─ 调用外部 API
  └─ 或通过 WebSocket 回复
  ↓
持久化历史记录到 SQLite
```

### 4.2 工具调用流程

```text
LLM tool_call
  → Tool Registry lookup （所有 Plugin 注册的 Tool）
  → Permission check （权限系统）
  → Tool execute
  → Tool result message
  → Loop continues
```

### 4.3 Gateway-Node 流程

```text
Node connect
  → auth challenge/response
  → heartbeat
  → command dispatch
  → result return
  → health update
```

## 5. 模块契约（建议先固定）

优先稳定以下契约，后续模块都基于这些契约展开：

- **Message Contract**：`InboundMessage` / `OutboundMessage`
  - 所有 ChannelPlugin 都基于这个统一结构转换平台原生消息
- **ChannelPlugin Contract**（参考 OpenClaw、OneBot）
  - `handle_inbound_event(event: dict) -> None`
  - `send_message(target: str, message: OutboundMessage) -> str`
  - 支持的通信方式声明（HTTP Server/Client、WebSocket、SSE）
  - 权限和生命周期钩子
- **Agent Contract**：`AgentLoop.run()` 输入输出与中断语义
- **Tool Contract**：tool definition、参数校验、执行结果结构
  - 由 Plugin 通过权限系统注册
- **Plugin Manifest Contract**：`plugin.yaml` 字段与版本兼容策略
  - ChannelPlugin 作为标准 Plugin 的一种，需遵循同一 manifest 规范
- **Gateway Protocol Contract**：消息类型、错误码、版本字段

**设计原则**：

- 契约一旦开放给插件或外部节点使用，默认只做向后兼容改动。
- ChannelPlugin 的多通信协议支持（HTTP/WebSocket）需在 manifest 中明确声明，确保 Bot 和外部系统能协商使用哪种方式。
- 参考 NapCat/OneBot 的做法：允许外部系统通过注册 webhook 向 Bot 推送，同时 Bot 也能通过 HTTP/WebSocket 主动向外部系统发送消息。

## 6. 数据与状态边界

状态分层建议：

- 瞬时态：请求上下文、流式响应缓冲、工具调用中间态
- 会话态：聊天历史、当前会话配置、会话级变量
- 长期态：workspace 文件、长期记忆、插件配置、节点信息

存储策略建议：

- 先使用 SQLite 统一落地会话与配置。
- 对外统一通过 repository 接口，不让业务代码直接拼 SQL。
- 文件系统读写全部经过 workspace/sandbox 统一入口。

### 6.1 Phase 2 架构细化（Agent 与 Workspace 联合阶段）

本节对应 ROADMAP 的 Phase 2.x 子阶段，只定义架构约束和模块协作方式。

#### 6.1.1 Workspace 基线与安全边界

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

#### 6.1.2 指令注入与上下文构建

- 上下文拼装顺序建议固定：系统基线 -> `AGENTS.md` -> `SOUL.md` -> `USER.md` -> 会话历史 -> 工具回填。
- `agent.context` 只负责拼装和裁剪，不直接访问 provider。
- 上下文预算策略分两层：
  1. 先执行窗口裁剪（保留最近对话与关键系统指令）。
  2. 超预算时再触发摘要压缩（可插拔策略）。

#### 6.1.3 Agent Loop 与 Provider 适配

- `agent.loop` 维持纯状态机语义：`prepare -> call_provider -> dispatch_tools -> finalize`。
- `agent.providers.base` 固定最小契约：`chat(messages, tools, timeout) -> ProviderResult`。
- Provider 错误必须归一到统一错误码，避免上层针对具体厂商分支。

建议错误码集合（最小可用）：

- `provider_timeout`
- `provider_rate_limited`
- `provider_auth_failed`
- `provider_bad_response`

#### 6.1.4 Tool Calling 协议

- 工具定义由插件系统注册，但执行编排由 `agent.tools` 统一管理。
- 参数校验建议在执行前完成（Pydantic 模型或等价校验器）。
- 工具执行结果统一封装为结构化消息，区分：
  - `ok`: 是否成功
  - `content`: 供模型消费的结果
  - `meta`: 可观测字段（耗时、工具名、截断标记）
  - `error`: 失败原因（可回退提示）

#### 6.1.5 记忆模型与存储抽象

- 短期记忆放在会话上下文层，长期记忆通过 repository 检索。
- SQLite 只作为首个实现，不直接暴露给 `agent.loop`。
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

#### 6.1.6 稳定性与可观测性

- 重试仅用于可恢复错误（超时、限流），认证失败默认不重试。
- 关键路径打点：provider 延迟、工具成功率、上下文裁剪次数、最终回复耗时。
- 最小验收闭环需要可追踪 trace_id，确保从 workspace 注入到最终回复可串联。

**当前实现**：

- `agent/metrics.py` — `MetricsCollector`（含 `Trace`、各 Record 类型），支持 `max_traces` 环形缓冲防内存泄漏。
- `agent/loop.py` — Provider 错误回退（`AgentRunResult.error` + `provider_error_template`），每步记录 provider/tool 指标。
- 全链路 UTC-aware ISO8601 时间戳。

> ⚠️ **架构优化待办**：`MetricsCollector` 当前为纯内存聚合，缺少 flush/export 机制。后续需增加 log sink 或 Prometheus exporter，并考虑将 observability 独立为 `agent/observability/` 子包。

#### 6.1.7 Workspace Sandbox 安全增强

> ⚠️ **当前实现风险提示**：现有 `workspace/sandbox.py` 仅使用简单的路径归一化检查，存在被绕过的风险。需要升级为更健壮的安全方案。

**当前实现的局限性**：

```python
# 当前实现（sandbox.py）- 简单路径检查
normalized = (self.root / candidate).resolve(strict=False)
try:
    normalized.relative_to(self.root)
except ValueError as exc:
    raise WorkspacePathError(...)
```

**已知绕过风险**：

1. **符号链接攻击**：攻击者可通过符号链接跳出沙盒边界
2. **硬链接攻击**：硬链接可能指向沙盒外文件
3. **竞态条件（TOCTOU）**：检查与实际操作之间存在时间窗口
4. **特殊文件系统对象**：设备文件、FIFO、socket 等未处理
5. **Unicode/编码绕过**：特殊编码可能绕过路径检查

**推荐增强方案**：

**方案 A：多层防御（推荐）**

```python
class SecureWorkspaceSandbox:
    """增强版沙盒实现，采用多层防御策略。"""

    def __init__(self, root: Path, *, max_file_size: int = 10 * 1024 * 1024) -> None:
        self.root = root.resolve(strict=True)
        self.max_file_size = max_file_size
        self._allowed_extensions: set[str] | None = None  # 可选：白名单扩展名

    def resolve_safe_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)

        # 第 1 层：拒绝绝对路径
        if candidate.is_absolute():
            raise WorkspacePathError(f"Absolute paths not allowed: {relative_path}")

        # 第 2 层：规范化并检查边界
        normalized = (self.root / candidate).resolve(strict=False)

        # 第 3 层：防止路径穿越（包括 .. 和编码绕过）
        try:
            normalized.relative_to(self.root)
        except ValueError as exc:
            raise WorkspacePathError(f"Path escapes workspace: {relative_path}") from exc

        # 第 4 层：拒绝符号链接（即使指向沙盒内）
        # 在实际操作时检查，避免 TOCTOU
        return normalized

    def _validate_before_operation(self, path: Path, *, for_write: bool = False) -> None:
        """操作前进行实时验证，防止 TOCTOU 攻击。"""
        # 检查是否为符号链接
        if path.is_symlink():
            raise WorkspacePathError(f"Symlinks not allowed: {path}")

        # 检查路径是否仍在沙盒内（实时验证）
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise WorkspacePathError(f"Path escapes workspace after resolution: {path}")

        # 写入操作额外检查
        if for_write:
            # 检查父目录是否为符号链接
            if path.parent.is_symlink():
                raise WorkspacePathError(f"Parent directory is symlink: {path.parent}")

            # 可选：检查文件扩展名白名单
            if self._allowed_extensions and path.suffix.lower() not in self._allowed_extensions:
                raise WorkspacePathError(f"File extension not allowed: {path.suffix}")
```

**方案 B：使用工业级沙盒库（参考 AstrBot）**

考虑引入成熟的沙盒库作为依赖：

| 库 | 特点 | 适用场景 |
|---|------|---------|
| ` RestrictedPython` | Python 代码沙盒 | 工具执行隔离 |
| `pyrate-limiter` | 频率限制 | 防止资源滥用 |
| 自研 + `os` 模块底层检查 | 文件系统沙盒 | 当前推荐 |

**方案 C：系统级隔离（未来扩展）**

- **容器隔离**：每个 workspace 运行在独立容器中
- **用户命名空间**：利用 Linux user namespace 隔离
- **seccomp/AppArmor**：限制系统调用

**实施建议**：

1. **Phase 2.7**：实现方案 A（多层防御），包括：
   - 符号链接检测与拒绝
   - TOCTOU 防护（操作时二次验证）
   - 文件大小限制
   - 可选的扩展名白名单

2. **Phase 3+**：根据实际需求评估方案 C（系统级隔离）

**测试要求**：

```python
# 必须覆盖的安全测试用例
def test_sandbox_rejects_symlink_escape()
def test_sandbox_rejects_symlink_inside_workspace()
def test_sandbox_rejects_hardlink_escape()
def test_sandbox_rejects_unicode_bypass()
def test_sandbox_enforces_max_file_size()
def test_sandbox_rejects_device_files()
```

#### 6.1.8 Provider 响应健壮性与多后端适配

> ⚠️ **当前实现风险提示**：现有 `agent/providers/openai_compatible.py` 仅处理标准 OpenAI 响应格式，未考虑不同 LLM 后端的响应差异，特别是推理链（thinking chain）支持。

**当前实现的局限性**：

```python
# 当前实现（openai_compatible.py）- 仅提取标准字段
content = message.get("content")
normalized_content = content if isinstance(content, str) else None
```

---

##### A. 各后端响应格式调研

> 以下调研基于各厂商公开 API 文档（截至 2026-04）。所有后端可归为三大格式族：
>
> - **OpenAI 兼容族**：OpenAI、DeepSeek、GLM/智谱、Minimax — 共享 `choices[].message` 扁平结构
> - **Anthropic 族**：Claude — 使用 `content[]` 内容块数组结构，与 OpenAI 格式**根本不兼容**
> - **Google Gemini 族**：Gemini 3 — 原生使用 `candidates[].content.parts[]` 结构，但提供 OpenAI 兼容端点

---

###### A.1 OpenAI 标准格式（GPT-5 系列）

**端点**：`POST /v1/chat/completions`

**当前模型系列**：gpt-5.2（旗舰）、gpt-5.1、gpt-5、gpt-5-mini、gpt-5-nano，以及 codex/pro 变体。旧版 o1/o3/o4-mini 仍可用但非当前推荐。

**标准响应**（GPT-5 系列）：

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1677858242,
  "model": "gpt-5.2",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Hello! How can I help you?",
      "refusal": null,
      "annotations": []
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 19,
    "completion_tokens": 12,
    "total_tokens": 31,
    "prompt_tokens_details": {
      "cached_tokens": 0,
      "audio_tokens": 0
    },
    "completion_tokens_details": {
      "reasoning_tokens": 0,
      "audio_tokens": 0,
      "accepted_prediction_tokens": 0,
      "rejected_prediction_tokens": 0
    }
  }
}
```

**推理模型说明**（GPT-5 系列均为推理模型）：

GPT-5 系列模型均使用 RL 训练，会产生内部推理 token，但在 Chat Completions API 中：

- 推理内容**不可见**：`reasoning_tokens` 仅出现在 `usage.completion_tokens_details` 中，是计费统计字段
- `reasoning_effort` 请求参数控制推理深度：`none`、`minimal`、`low`、`medium`、`high`、`xhigh`（GPT-5.2 默认 `none`，GPT-5 默认 `medium`）
- 推理摘要需通过新的 `/v1/responses` 端点获取（`reasoning.summary` 参数：`auto`/`concise`/`detailed`），**不通过 Chat Completions API 暴露**

关键特征：

- 推理 token 在 Chat Completions API 中**始终不可见**，仅计费统计
- `refusal` 字段：当模型拒绝回答时返回拒绝原因字符串，正常响应为 `null`
- `annotations` 字段（新增）：消息级注解数组
- `finish_reason` 取值：`stop`、`length`、`tool_calls`、`content_filter`
- Tool calls 格式：`message.tool_calls[].function.{name, arguments}`
- `verbosity` 参数（新增）：控制回复详细程度（`low`/`medium`/`high`）
- `web_search_options` 参数（新增）：内置网页搜索工具

---

###### A.2 DeepSeek 格式

**端点**：`POST /chat/completions`（OpenAI 兼容）

**标准响应**（DeepSeek-V3 等）：

与 OpenAI 标准格式完全一致。

**推理模型响应**（DeepSeek-R1）：

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "deepseek-reasoner",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "The answer is 42.",
      "reasoning_content": "<think&gt;\nLet me analyze this step by step...\nFirst, I need to...\n</think&gt;"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 50,
    "completion_tokens": 800,
    "total_tokens": 850,
    "prompt_tokens_details": { "cached_tokens": 0 },
    "completion_tokens_details": { "reasoning_tokens": 600 }
  }
}
```

关键特征：

- `reasoning_content`：**与 `content` 同级**，位于 `choices[0].message` 内，是推理过程的完整文本
- `reasoning_content` 中的内容可能包含 `<think/>` 标签（非固定，取决于模型版本）
- `usage.completion_tokens_details.reasoning_tokens`：推理 token 计费统计
- 缓存相关 usage 字段：`usage.prompt_cache_hit_tokens`、`usage.prompt_cache_miss_tokens`
- 请求侧通过 `thinking` 参数控制是否启用推理模式（`{"type": "enabled"}` / `{"type": "disabled"}`）
- 完全 OpenAI 兼容，可复用 OpenAI 适配器 + 扩展 `reasoning_content` 提取

**⚠️ DeepSeek 模型差异（架构影响）**：

DeepSeek 当前有两个主要模型系列，其能力组合不同，对适配器设计有影响：

| 模型 | 推理/思考 | Tool Calling | 说明 |
|------|----------|-------------|------|
| `deepseek-reasoner` (R1) | 始终开启 | **不支持** | 推理能力最强，但无法使用工具 |
| `deepseek-chat` (V3.2) | 可选开启 | 支持 | 请求侧设置 `thinking: {"type": "enabled"}` 可同时获得推理+工具 |

- `deepseek-chat` (V3.2) 是**推荐方案**：可同时启用推理模式和 tool calling，是当前唯一支持"推理+工具"的 DeepSeek 模型
- `deepseek-reasoner` (R1) 虽然推理能力强，但不支持 tool calling，在需要工具的场景中受限
- `finish_reason` 额外取值：`insufficient_system_resource`（DeepSeek 特有，表示系统资源不足）
- DeepSeek 还提供 Anthropic Messages API 兼容端点（`https://api.deepseek.com/anthropic`），支持 `thinking` 内容块类型，但此端点功能有限（不支持图片、文档等）

---

###### A.3 Anthropic/Claude 格式

**端点**：`POST /v1/messages`（**非 OpenAI 兼容**，独立 API 路径和结构）

**当前模型系列**：claude-sonnet-4-6、claude-opus-4-6、claude-opus-4-1-20250805、claude-sonnet-4-20250514。API 响应格式在 Claude 4.x 系列中保持一致。

**标准响应**（Claude Sonnet/Opus 等，无 Extended Thinking）：

```json
{
  "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "Hello! How can I help you?"
    }
  ],
  "model": "claude-sonnet-4-20250514",
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 25,
    "output_tokens": 20
  }
}
```

**Extended Thinking 响应**（Claude 3.7 Sonnet / Claude 4）：

```json
{
  "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "thinking",
      "thinking": "Let me analyze this step by step...\nFirst, I need to consider...",
      "signature": "ErUB6pWIDo9Bkx..."
    },
    {
      "type": "text",
      "text": "The answer is 42."
    }
  ],
  "model": "claude-sonnet-4-20250514",
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 50,
    "output_tokens": 800
  }
}
```

**Redacted Thinking 响应**：

```json
{
  "content": [
    {
      "type": "thinking",
      "thinking": "Normal thinking content...",
      "signature": "ErUB6pWIDo9Bkx..."
    },
    {
      "type": "redacted_thinking",
      "signature": "EqoBCkAaBQ..."
    },
    {
      "type": "text",
      "text": "Based on my analysis..."
    }
  ]
}
```

**Tool Use 响应**：

```json
{
  "content": [
    {
      "type": "text",
      "text": "Let me look that up for you."
    },
    {
      "type": "tool_use",
      "id": "toolu_01A09q90qw90lq917635",
      "name": "get_weather",
      "input": { "location": "San Francisco, CA" }
    }
  ],
  "stop_reason": "tool_use"
}
```

**Interleaved Thinking 响应**（Claude 4，tool call 之间的思考）：

```json
{
  "content": [
    { "type": "thinking", "thinking": "User wants weather...", "signature": "..." },
    { "type": "text", "text": "Let me check the weather." },
    { "type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": { "city": "SF" } },
    { "type": "thinking", "thinking": "Now I have the data, let me summarize...", "signature": "..." },
    { "type": "text", "text": "The weather in SF is sunny." }
  ],
  "stop_reason": "end_turn"
}
```

关键特征：

- **内容块数组**：`content` 是 `ContentBlock[]` 而非 `string`，每个块有 `type` 字段区分
- 内容块类型：`text`、`thinking`、`redacted_thinking`、`tool_use`
- `thinking` 块包含 `thinking`（文本）和 `signature`（加密签名，用于多轮对话中回传）
- `redacted_thinking` **不含文本**，只有 `signature`（因安全策略被脱敏）
- Claude 4 返回的是**摘要思考**（summarized thinking），非完整推理过程
- `stop_reason` 等价于 OpenAI 的 `finish_reason`，完整取值：`end_turn`、`max_tokens`、`tool_use`、`stop_sequence`、`pause_turn`（长任务暂停，可续传）、`refusal`（安全策略拒绝）
- Tool calls 位于 content blocks 中（`type: "tool_use"`），而非独立 `tool_calls` 数组；支持同一响应中多个 `tool_use` 块（并行工具调用）
- 请求侧需要 `thinking` 参数开启 Extended Thinking，且 `max_tokens` 必须足够大（≥ 16000）
- `signature` 字段**必须回传**到后续多轮对话中，否则 API 会报错；`thinking` 和 `redacted_thinking` 的签名都需原样回传
- `usage.output_tokens` 在开启 Extended Thinking 时反映**计费 token 数**（包含完整内部推理），而非可见的摘要 token 数

**Anthropic `stop_reason` 与 OpenAI `finish_reason` 对照**：

| Anthropic `stop_reason` | OpenAI `finish_reason` | 说明 |
|---|---|---|
| `end_turn` | `stop` | 正常结束 |
| `max_tokens` | `length` | 达到 token 上限 |
| `tool_use` | `tool_calls` | 请求工具调用 |
| `stop_sequence` | （无对应） | 命中自定义停止序列 |
| `pause_turn` | （无对应） | 长任务暂停，可将响应用于续传 |
| `refusal` | `content_filter` | 安全策略拒绝 |

---

###### A.4 GLM/智谱 格式

**端点**：`POST /api/paas/v4/chat/completions`（OpenAI 兼容）

**标准响应**（GLM-4 等）：

```json
{
  "id": "8748969001",
  "created": 1677858242,
  "model": "glm-4",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Hello! How can I help you?"
    },
    "finish_reason": "stop"
  }],
  "request_id": "8748969001",
  "usage": {
    "prompt_tokens": 50,
    "completion_tokens": 12,
    "total_tokens": 62
  }
}
```

**Web Search 响应**（GLM-4 开启 web_search 工具时）：

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "Based on web search results...",
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "web_search",
          "arguments": "{\"query\": \"latest news\"}"
        }
      }]
    },
    "finish_reason": "stop"
  }],
  "web_search": [
    {
      "icon": "https://example.com/favicon.ico",
      "title": "Example Page",
      "link": "https://example.com",
      "content": "Page content snippet...",
      "media": "example.com"
    }
  ]
}
```

关键特征：

- 完全 OpenAI 兼容，额外字段不影响标准解析
- `request_id`：智谱特有的请求追踪字段
- `web_search`：顶层额外字段，包含搜索结果元数据（不影响消息解析）
- `finish_reason` 额外取值：`sensitive`（内容敏感被拦截）、`network_error`
- 当前无可见推理/思考链字段（GLM-4 系列）
- 无 `refusal` 字段

---

###### A.5 Minimax 格式

**端点**：`POST /v1/text/chatcompletion_v2`（OpenAI 兼容）

关键特征：

- 基本兼容 OpenAI 标准格式
- API 路径为 `/v1/text/chatcompletion_v2`，非标准 `/v1/chat/completions`
- 官方文档访问受限，无法确认是否有 Minimax 特有的推理/思考链扩展字段
- 按 OpenAI 标准适配器处理即可，遇到额外字段时按需扩展

---

###### A.6 Google Gemini 3 格式

**端点**（原生）：`POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`

**端点**（OpenAI 兼容）：`POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`

**当前模型系列**：gemini-3-flash-preview、gemini-3-pro-image-preview 等。

**原生响应格式**：

```json
{
  "candidates": [{
    "content": {
      "parts": [
        { "text": "Hello! How can I help you?" }
      ],
      "role": "model"
    },
    "finishReason": "STOP",
    "finishMessage": "",
    "safetyRatings": [
      { "category": "HARM_CATEGORY_HARASSMENT", "probability": "NEGLIGIBLE" }
    ],
    "tokenCount": 150,
    "index": 0
  }],
  "promptFeedback": {
    "blockReason": null,
    "safetyRatings": []
  },
  "usageMetadata": {
    "promptTokenCount": 25,
    "candidatesTokenCount": 150,
    "thoughtsTokenCount": 500,
    "totalTokenCount": 675,
    "cachedContentTokenCount": 0
  },
  "modelVersion": "gemini-3-flash-preview",
  "responseId": "resp_abc123"
}
```

**Thinking/推理响应**（`includeThoughts: true` 时）：

```json
{
  "candidates": [{
    "content": {
      "parts": [
        {
          "text": "Let me analyze this step by step...",
          "thought": true
        },
        {
          "text": "The answer is 42."
        }
      ],
      "role": "model"
    },
    "finishReason": "STOP"
  }],
  "usageMetadata": {
    "promptTokenCount": 25,
    "candidatesTokenCount": 150,
    "thoughtsTokenCount": 500,
    "totalTokenCount": 675
  }
}
```

**Tool Calling 响应**：

```json
{
  "candidates": [{
    "content": {
      "parts": [
        { "text": "Let me check that for you." },
        {
          "functionCall": {
            "name": "get_weather",
            "args": { "location": "San Francisco, CA" }
          }
        }
      ],
      "role": "model"
    },
    "finishReason": "STOP"
  }]
}
```

关键特征：

- 原生格式使用 `candidates[].content.parts[]` 结构，与 OpenAI 和 Anthropic 均不同
- 但提供 **OpenAI 兼容端点**（`/v1beta/openai/chat/completions`），可复用 OpenAI 适配器
- Thinking 通过 `generationConfig.thinkingConfig.thinkingLevel` 控制：`MINIMAL`、`LOW`、`MEDIUM`、`HIGH`（默认 `HIGH`）
- Thinking **不可完全禁用**（Gemini 3 系列强制启用）
- 思考摘要通过 `includeThoughts: true` 获取，在 parts 中用 `"thought": true` 标记
- `thoughtsTokenCount`：推理 token 计费统计
- 思考签名（thought signatures）用于多轮对话连续性（与 Anthropic `signature` 类似）
- `finishReason` 取值极多（20 个），常见的有：`STOP`、`MAX_TOKENS`、`SAFETY`、`RECITATION`、`MALFORMED_FUNCTION_CALL`、`MISSING_THOUGHT_SIGNATURE`、`UNEXPECTED_TOOL_CALL`、`TOO_MANY_TOOL_CALLS`
- 原生 tool calls 位于 parts 中（`functionCall` / `functionResponse`），支持并行函数调用
- Gemini 特有字段：`safetyRatings`（安全评级）、`citationMetadata`（引用来源）、`groundingMetadata`（搜索/地图 grounding）、`promptFeedback.blockReason`
- OpenAI 兼容端点限制：不支持 grounding、code execution 等 Gemini 原生功能

**适配策略建议**：

- **推荐**：使用 OpenAI 兼容端点 + OpenAI 适配器，覆盖基础 chat + tool calling 场景
- **可选扩展**：如需 thinking 内容、safety ratings 等原生功能，实现独立的 `GeminiNativeAdapter`

---

###### A.7 格式族总结

| 特征 | OpenAI (GPT-5) | DeepSeek | Anthropic (Claude) | GLM | Minimax | Gemini 3 |
|------|---------------|----------|-------------------|-----|---------|----------|
| API 路径 | `/v1/chat/completions` | `/chat/completions` | `/v1/messages` | `/api/paas/v4/chat/completions` | `/v1/text/chatcompletion_v2` | 原生: `generateContent` / 兼容: OpenAI 端点 |
| 格式族 | OpenAI | OpenAI | **Anthropic（独立）** | OpenAI | OpenAI | **Gemini（独立）/ OpenAI 兼容** |
| 推理内容 | 不可见（仅 `reasoning_tokens`） | `message.reasoning_content` | `content[].thinking` 块 | 无 | 未知 | 原生: `parts[].thought=true` / 兼容: 不可见 |
| 推理签名 | 无 | 无 | `signature`（必须回传） | 无 | 无 | 原生: thought signatures（需回传） |
| 脱敏推理 | 无 | 无 | `redacted_thinking` 块 | 无 | 无 | 无 |
| Tool calls | `message.tool_calls[]` | `message.tool_calls[]` | `content[].tool_use` 块 | `message.tool_calls[]` | `message.tool_calls[]`（推测） | 原生: `functionCall` parts / 兼容: OpenAI 格式 |
| 拒绝标记 | `message.refusal` | 无 | `stop_reason: refusal` | 无 | 无 | `finishReason: SAFETY` |
| 停止原因字段 | `finish_reason` | `finish_reason` | `stop_reason` | `finish_reason` | `finish_reason`（推测） | `finishReason` |
| 适配器复用 | 基类 | 继承 OpenAI 适配器 | **独立适配器** | 继承 OpenAI 适配器 | 继承 OpenAI 适配器 | 兼容端点用 OpenAI 适配器 / 原生需独立适配器 |

**架构结论**：

- **OpenAI 兼容族**（OpenAI、DeepSeek、GLM、Minimax）+ **Gemini 兼容端点**可共享同一适配器基类，仅通过扩展点处理差异字段
- **Anthropic 族**需要独立适配器，因其内容块数组结构与扁平 message 结构根本不同
- **Gemini 原生端点**（如需 thinking/safety/grounding 等原生功能）需要独立适配器，但推荐优先使用 OpenAI 兼容端点
- Anthropic 和 Gemini 均有签名回传需求（`signature` / thought signatures），对上下文管理有特殊要求

---

##### B. 集成式 Provider 架构

> **设计理念**：借鉴 AstrBot 的类继承模式 + OpenClaw 的 Provider 即 Plugin 注册思路。每个 Provider 类**同时负责** HTTP 传输和响应解析，不使用独立的 ResponseAdapter。通过类继承共享 OpenAI 兼容族的通用逻辑，Anthropic 和 Gemini 各自独立实现。

---

###### B.1 扩展 `ProviderResponse`

```python
# nahida_bot/agent/providers/base.py

@dataclass(slots=True, frozen=True)
class TokenUsage:
    """Token 使用统计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True, frozen=True)
class ProviderResponse:
    """统一的 Provider 响应结构。"""

    # 标准字段（已有）
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    raw_response: dict[str, object] | None = None

    # 推理链（Phase 2.8 新增）
    reasoning_content: str | None = None       # DeepSeek reasoning_content / Anthropic thinking
    reasoning_signature: str | None = None     # Anthropic signature / Gemini thought_signature（base64）
    has_redacted_thinking: bool = False        # Anthropic 存在 redacted_thinking 块

    # 拒绝/安全
    refusal: str | None = None                 # OpenAI 内容拒绝原因

    # 使用统计
    usage: TokenUsage | None = None

    # Provider 扩展包
    extra: dict[str, object] = field(default_factory=dict)
```

**字段设计说明**：

- `reasoning_content`：扁平字符串。Anthropic 的 interleaved thinking（多个 thinking 块穿插 tool_use）合并为 `\n` 连接的单字符串。如需 per-block 位置元数据，放入 `extra`。
- `reasoning_signature`：Anthropic 的 `signature` 加密串，或 Gemini 的 `thought_signature`（base64）。对于无签名机制的 Provider（OpenAI、DeepSeek、GLM），保持 `None`。
- `has_redacted_thinking`：布尔标记。`redacted_thinking` 块不含文本，仅有签名。标记的存在提醒上下文管理器必须回传签名。
- `extra`：Provider 特有数据的逃逸口。例如 GLM 的 `web_search` 元数据、Gemini 的 `safety_ratings`、OpenAI 的 `annotations`。避免 ProviderResponse 字段无限膨胀。

---

###### B.2 扩展 `ContextMessage`

```python
# nahida_bot/agent/context.py

@dataclass(slots=True, frozen=True)
class ContextMessage:
    """上下文消息单元。"""

    role: MessageRole
    content: str
    source: str
    metadata: dict[str, object] | None = None

    # 推理链支持（Phase 2.8 新增，均有默认值，向后兼容）
    reasoning: str | None = None
    reasoning_signature: str | None = None
    has_redacted_thinking: bool = False
```

> **为什么用扁平字段而非 AstrBot 的 ThinkPart 列表**：AstrBot 使用 `list[ContentPart]`（其中 `ThinkPart` 是一种变体），这需要 Pydantic 模型层次和自定义反序列化器。nahida-bot 作为聊天机器人，扁平字段更简单，pyright strict 模式下无需类型窄化（type-narrowing）体操。

Agent loop 中的 `_build_assistant_message` 方法将新字段从 `ProviderResponse` 传播到 `ContextMessage`：

```python
# nahida_bot/agent/loop.py（伪代码）
def _build_assistant_message(self, response: ProviderResponse) -> ContextMessage | None:
    ...
    return ContextMessage(
        role="assistant",
        source="provider_response",
        content=response.content or "",
        metadata=metadata or None,
        reasoning=response.reasoning_content,
        reasoning_signature=response.reasoning_signature,
        has_redacted_thinking=response.has_redacted_thinking,
    )
```

---

###### B.3 Provider 类层次

```text
ChatProvider (ABC)                              # base.py — 现有抽象类，扩展 api_family/format_tools/serialize_messages
├── OpenAICompatibleProvider(_ReasoningMixin)   # openai_compatible.py — 演进自当前实现
│   ├── DeepSeekProvider                        # deepseek.py — @register_provider，空子类
│   ├── GLMProvider                             # glm.py — @register_provider，空子类
│   ├── GroqProvider                            # groq.py — reasoning_key="reasoning" + 历史 strip
│   └── MinimaxProvider                         # minimax.py — @register_provider，空子类
├── AnthropicProvider                           # anthropic.py — 独立实现（Phase 2.8b）
└── GeminiProvider                              # gemini.py — 独立实现（Phase 3）
```

**ChatProvider 基类扩展**：

```python
class ChatProvider(ABC):
    """Provider 基类，被 agent loop 消费。"""

    name: str
    api_family: str  # "openai-completions" | "anthropic-messages" | "google-generative-ai"

    @property
    @abstractmethod
    def tokenizer(self) -> Tokenizer | None: ...

    @abstractmethod
    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProviderResponse: ...

    def format_tools(self, tools: list[ToolDefinition]) -> list[object]:
        """将 ToolDefinition 列表转换为 Provider 原生工具格式。默认 OpenAI 格式。"""
        return [
            {
                "type": tool.type,
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def serialize_messages(self, messages: list[ContextMessage]) -> list[dict[str, object]]:
        """将 ContextMessage 列表转换为 Provider 原生请求格式。默认 OpenAI 格式。"""
        result: list[dict[str, object]] = []
        for msg in messages:
            result.append(self._serialize_one_message(msg))
        return result

    def _serialize_one_message(self, message: ContextMessage) -> dict[str, object]:
        """默认 OpenAI 序列化。子类按 Provider 族覆盖。"""
        ...  # 当前 OpenAICompatibleProvider._serialize_message 的逻辑
```

**三族 Provider 的关键差异**：

| 维度 | OpenAI 兼容族 | Anthropic 族 | Gemini 族 |
|------|-------------|-------------|----------|
| `api_family` | `"openai-completions"` | `"anthropic-messages"` | `"google-generative-ai"` |
| HTTP 端点 | `POST /v1/chat/completions` | `POST /v1/messages` | 原生: `generateContent` / 兼容: OpenAI 端点 |
| 响应结构 | `choices[].message` 扁平 | `content[]` 内容块数组 | `candidates[].content.parts[]` |
| `format_tools()` | `{"type":"function","function":{...}}` | `{"name","description","input_schema"}` | `{"function_declarations":[...]}` |
| 推理提取 | `reasoning_key` 字段 + tag 兜底 | `thinking` 内容块 | `part.thought=true` 标记 |
| 签名回传 | 无 | `signature` 必须回传 | `thought_signature` 需回传 |
| 工具调用格式 | `message.tool_calls[]` | `content[].tool_use` 块 | `part.functionCall` |

**OpenAI 兼容族（当前 `OpenAICompatibleProvider` 演进）**：

```python
@dataclass(slots=True)
class OpenAICompatibleProvider(_ReasoningMixin, ChatProvider):
    """OpenAI 兼容 Provider。处理 HTTP 传输 + 响应解析。"""

    base_url: str
    api_key: str
    model: str
    name: str = "openai-compatible"
    api_family: str = "openai-completions"
    tokenizer_impl: Tokenizer | None = None

    # ... chat() 方法演进：使用 self._extract_reasoning_from_message() 填充 reasoning_content
    # ... serialize_messages() 演进：注入 reasoning_content 到 assistant 消息历史中
```

**空子类示例（AstrBot 模式）**：

```python
# nahida_bot/agent/providers/deepseek.py
@register_provider("deepseek", "DeepSeek Provider")
class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek Provider。reasoning_key 默认 'reasoning_content'，无需覆盖。"""
    name: str = "deepseek"

# nahida_bot/agent/providers/glm.py
@register_provider("glm", "GLM/ZhiPu Provider")
class GLMProvider(OpenAICompatibleProvider):
    """GLM Provider。完全 OpenAI 兼容，无需任何覆盖。"""
    name: str = "glm"

# nahida_bot/agent/providers/groq.py
@register_provider("groq", "Groq Provider")
class GroqProvider(OpenAICompatibleProvider):
    """Groq Provider。reasoning_key 为 'reasoning'，且需 strip 历史中的推理字段。"""
    name: str = "groq"
    reasoning_key: str = "reasoning"

    def serialize_messages(self, messages: list[ContextMessage]) -> list[dict[str, object]]:
        serialized = super().serialize_messages(messages)
        for msg in serialized:
            if msg.get("role") == "assistant":
                msg.pop("reasoning_content", None)
                msg.pop("reasoning", None)
        return serialized
```

**Anthropic Provider（独立实现）**：

```python
# nahida_bot/agent/providers/anthropic.py
@register_provider("anthropic", "Anthropic Claude Provider")
class AnthropicProvider(ChatProvider):
    """独立 Anthropic Provider。不继承 OpenAICompatibleProvider。"""

    api_family: str = "anthropic-messages"

    # format_tools: Anthropic 使用 input_schema 而非 parameters
    def format_tools(self, tools: list[ToolDefinition]) -> list[object]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    # serialize_messages: 将 ContextMessage 转换为 Anthropic 格式
    #   - system 消息提取为独立 system 参数
    #   - assistant 消息带 reasoning_signature 时注入 thinking 块
    #   - tool 消息转换为 user/tool_result 格式
    def serialize_messages(self, messages: list[ContextMessage]) -> list[dict[str, object]]:
        ...

    # chat(): POST /v1/messages, 解析 content blocks
    async def chat(self, ...) -> ProviderResponse:
        # 遍历 content blocks:
        #   text → content
        #   thinking → reasoning_content + reasoning_signature
        #   redacted_thinking → has_redacted_thinking = True
        #   tool_use → ToolCall(call_id, name, input)
        ...
```

**Gemini Provider（Phase 3，使用 OpenAI 兼容端点起步）**：

```python
# nahida_bot/agent/providers/gemini.py
@register_provider("gemini", "Google Gemini Provider")
class GeminiProvider(ChatProvider):
    """Gemini Provider。初期使用 OpenAI 兼容端点，后续可扩展原生端点。"""
    api_family: str = "google-generative-ai"
    # 使用 /v1beta/openai/chat/completions 端点时，可复用 OpenAI 兼容逻辑
    ...
```

---

###### B.4 Provider 注册表

借鉴 OpenClaw 的"Provider 即 Plugin"概念，但保持简洁。装饰器 + 模块级字典，与 AstrBot 的 `register.py` 一致。

```python
# nahida_bot/agent/providers/registry.py

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nahida_bot.agent.providers.base import ChatProvider

@dataclass(slots=True, frozen=True)
class ProviderDescriptor:
    """已注册 Provider 的元数据。"""
    provider_type: str
    description: str
    cls: type[ChatProvider]

_REGISTRY: dict[str, ProviderDescriptor] = {}


def register_provider(provider_type: str, description: str = ""):
    """装饰器：注册一个 Provider 类。"""
    def decorator(cls: type[ChatProvider]) -> type[ChatProvider]:
        if provider_type in _REGISTRY:
            raise ValueError(f"Provider type '{provider_type}' already registered")
        _REGISTRY[provider_type] = ProviderDescriptor(
            provider_type=provider_type,
            description=description,
            cls=cls,
        )
        return cls
    return decorator


def get_provider_class(provider_type: str) -> type[ChatProvider] | None:
    """按类型名查找已注册的 Provider 类。"""
    descriptor = _REGISTRY.get(provider_type)
    return descriptor.cls if descriptor else None


def create_provider(provider_type: str, **kwargs) -> ChatProvider:
    """工厂方法：按类型名创建 Provider 实例。"""
    cls = get_provider_class(provider_type)
    if cls is None:
        raise ValueError(f"Unknown provider type: {provider_type}")
    return cls(**kwargs)


def list_providers() -> list[ProviderDescriptor]:
    """返回所有已注册的 Provider 描述符。"""
    return list(_REGISTRY.values())
```

> **与 Phase 3 插件系统的衔接**：当前注册表是模块级字典，Provider 模块通过 `import` 触发 `@register_provider` 装饰器。Phase 3 的完整插件系统可通过 entry points 或 manifest 自动发现并 import Provider 模块，无需改动注册 API。

---

###### B.5 `_ReasoningMixin` — 共享推理提取逻辑

```python
# nahida_bot/agent/providers/reasoning.py

import re
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

class ReasoningPolicy(Enum):
    """推理内容注入上下文的策略。"""
    STRIP = "strip"      # 丢弃推理文本，仅保留签名（节省 token）
    APPEND = "append"    # 完整注入推理内容（最完整上下文）
    BUDGET = "budget"    # 预算允许时注入（推荐默认值）


# <think/> 标签正则（兼容 <think|thinking|thought> 标签名）
_THINK_TAG_PATTERN = re.compile(r"<think(?:ing)?\s*>(.*?)</think(?:ing)?\s*>", re.DOTALL)


def extract_think_tags(content: str) -> tuple[str, str | None]:
    """从 content 中提取并移除 <think/> 标签。

    返回 (cleaned_content, extracted_reasoning)。
    用于无结构化 reasoning 字段的 Provider 的兜底推理提取。
    """
    if not content:
        return content, None

    matches = _THINK_TAG_PATTERN.findall(content)
    if not matches:
        return content, None

    reasoning = "\n".join(match.strip() for match in matches if match.strip())
    cleaned = _THINK_TAG_PATTERN.sub("", content).strip()
    return cleaned, reasoning or None


class _ReasoningMixin:
    """OpenAI 兼容族共享的推理提取逻辑。"""

    reasoning_key: str = "reasoning_content"     # 响应中推理字段的键名
    reasoning_output_mode: str = "native"        # "native"（结构化字段）| "tagged"（<think/> 标签）

    def _extract_reasoning_from_message(self, message: dict[str, object]) -> str | None:
        """从响应消息中提取推理内容。

        优先级：(1) 结构化字段 (2) <think/> 标签兜底。
        """
        # 优先级 1：native 字段
        raw = message.get(self.reasoning_key)
        if isinstance(raw, str) and raw.strip():
            return raw

        # 优先级 2：tag-based 提取
        content = message.get("content")
        if isinstance(content, str):
            cleaned, reasoning = extract_think_tags(content)
            if reasoning:
                # 注意：需要同时更新 content（去掉 think 标签）
                # 调用方负责处理 cleaned content
                return reasoning

        return None
```

**`reasoning_key` 的设计**：不同 Provider 在 OpenAI 兼容格式中使用不同的推理字段名。DeepSeek 用 `"reasoning_content"`，Groq 用 `"reasoning"`。子类只需覆盖 `reasoning_key` 属性即可，无需重写解析逻辑（借鉴 AstrBot 模式）。

---

###### B.6 推理链上下文策略

`ReasoningPolicy` 控制 `ContextBuilder` 在组装上下文时如何处理推理内容：

```python
# nahida_bot/agent/context.py（ContextBudget 扩展）

@dataclass(slots=True, frozen=True)
class ContextBudget:
    """上下文预算设置。"""

    max_tokens: int = 8000
    reserved_tokens: int = 1000
    max_chars: int | None = None
    reserved_chars: int = 0
    summary_max_chars: int = 600

    # Phase 2.8 新增
    reasoning_policy: ReasoningPolicy = ReasoningPolicy.BUDGET
    max_reasoning_tokens: int = 2000  # 推理内容最大 token 数

    @property
    def usable_tokens(self) -> int:
        ...  # 现有逻辑不变
```

**三种策略的行为**：

| 策略 | 推理文本 | 推理签名 | 适用场景 |
|------|---------|---------|---------|
| `STRIP` | 丢弃 | **始终保留** | 纯聊天，节省 token |
| `APPEND` | 完整注入 | 保留 | 调试、复杂推理任务 |
| `BUDGET` | 预算内注入，超出则丢弃 | **始终保留** | 通用场景（推荐默认值） |

**关键不变量**：无论策略如何，`reasoning_signature` **始终保留**在历史中。Anthropic 和 Gemini 的多轮对话依赖签名回传，丢弃签名会导致 API 报错。

---

###### B.7 工具 Schema 转换

`format_tools()` 方法在 `ChatProvider` 上提供默认 OpenAI 格式，各 Provider 族覆盖：

```python
# Anthropic 族覆盖
def format_tools(self, tools: list[ToolDefinition]) -> list[object]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,  # 注意：Anthropic 用 "input_schema" 而非 "parameters"
        }
        for tool in tools
    ]

# Gemini 族覆盖（未来）
def format_tools(self, tools: list[ToolDefinition]) -> list[object]:
    # Gemini 需要清理不支持的 JSON Schema 关键字（default, additionalProperties 等）
    # 确保 array 类型始终有 items schema
    return [
        {"function_declarations": [convert_schema_for_gemini(tool) for tool in tools]}
    ]
```

---

###### B.8 历史回放（签名/Thinking 块在多轮对话中的回传）

每个 Provider 的 `serialize_messages()` 方法负责将 `ContextMessage` 中的 `reasoning`/`reasoning_signature` 转换为 Provider 原生格式：

**OpenAI 兼容族**：

```python
# OpenAICompatibleProvider.serialize_messages
for msg in messages:
    payload = {"role": msg.role, "content": msg.content}
    if msg.role == "assistant" and msg.reasoning:
        payload["reasoning_content"] = msg.reasoning
    # ... tool_calls handling ...
```

**Anthropic 族**：

```python
# AnthropicProvider.serialize_messages
for msg in messages:
    if msg.role == "assistant":
        blocks: list[dict] = []
        # 注入 thinking 块（如有签名）
        if msg.reasoning_signature:
            blocks.append({
                "type": "thinking",
                "thinking": msg.reasoning or "",
                "signature": msg.reasoning_signature,
            })
            if msg.has_redacted_thinking:
                blocks.append({
                    "type": "redacted_thinking",
                    "signature": msg.reasoning_signature,
                })
        if msg.content:
            blocks.append({"type": "text", "text": msg.content})
        # ... tool_use blocks from metadata ...
```

**Gemini 族**（原生端点）：

```python
# GeminiProvider.serialize_messages（Phase 3）
# thought_signature 需 base64 解码后附加到对应 Part 上
for msg in messages:
    if msg.role == "assistant" and msg.reasoning_signature:
        part = types.Part(text=msg.reasoning or "", thought_signature=base64.b64decode(msg.reasoning_signature))
```

---

##### C. 文件布局

```
nahida_bot/agent/providers/
    __init__.py              # 更新：新增导出
    base.py                  # 扩展：TokenUsage, ProviderResponse 新字段, api_family, format_tools, serialize_messages
    registry.py              # 新增：@register_provider, get_provider_class, create_provider, list_providers
    reasoning.py             # 新增：ReasoningPolicy, extract_think_tags, _ReasoningMixin
    openai_compatible.py     # 演进：继承 _ReasoningMixin，填充新 ProviderResponse 字段，serialize_messages 注入推理
    deepseek.py              # 新增：@register_provider("deepseek")，空子类
    glm.py                   # 新增：@register_provider("glm")，空子类
    groq.py                  # 新增：@register_provider("groq")，reasoning_key 覆盖 + 历史 strip
    minimax.py               # 新增：@register_provider("minimax")，空子类
    anthropic.py             # 新增：独立 Provider（Phase 2.8b）
    gemini.py                # 新增：独立 Provider（Phase 3）
    errors.py                # 不变

nahida_bot/agent/
    context.py               # 扩展：ContextMessage 新字段，ContextBudget 新增 reasoning_policy/max_reasoning_tokens
    loop.py                  # 扩展：_build_assistant_message 传播新字段
```

---

##### D. 实施计划

**Phase 2.8（当前）**：

1. 扩展 `base.py`：`TokenUsage` dataclass + `ProviderResponse` 新字段 + `ChatProvider` 新方法
2. 扩展 `context.py`：`ContextMessage` 新字段 + `ContextBudget` 新字段 + `ReasoningPolicy` 引用
3. 新建 `reasoning.py`：`ReasoningPolicy` 枚举 + `extract_think_tags()` + `_ReasoningMixin`
4. 新建 `registry.py`：`@register_provider` + 工厂方法
5. 演进 `openai_compatible.py`：继承 `_ReasoningMixin`，填充新字段，`serialize_messages` 处理推理历史
6. 新建空子类：`deepseek.py`、`glm.py`、`groq.py`、`minimax.py`
7. 扩展 `loop.py`：`_build_assistant_message` 传播推理/签名字段
8. 编写完整测试套件
9. 更新 ARCHITECTURE.md 文档

**Phase 2.8b**：

1. 新建 `anthropic.py`：独立 Anthropic Provider（content blocks 解析 + 签名回传）
2. Anthropic 相关测试
3. 可选：`gemini.py` 使用 OpenAI 兼容端点起步

**Phase 3+**：

1. 流式响应支持
2. `GeminiProvider` 原生端点实现
3. 更多后端适配器
4. 完整插件系统（通过 entry points 发现 Provider 模块）

---

##### E. 流式响应（未来扩展）

```python
# Phase 3: 扩展 ChatProvider.chat 签名
async def chat(
    self,
    *,
    messages: list[ContextMessage],
    tools: list[ToolDefinition] | None = None,
    timeout_seconds: float | None = None,
    stream: bool = False,
) -> ProviderResponse | AsyncIterator[StreamChunk]:
    ...
```

流式适配需要处理：

| Provider 族 | 流式机制 | 推理增量 |
|------------|---------|---------|
| OpenAI 族 | `choices[0].delta.content` | `delta.reasoning_content`（DeepSeek） |
| Anthropic | `content_block_delta` 事件 | `thinking_delta` + `signature_delta` |
| Gemini 原生 | `streamGenerateContent` | thought parts 增量 |

---

##### F. 测试要求

```python
# === TokenUsage ===
def test_token_usage_total_sums_input_output()

# === ProviderResponse ===
def test_provider_response_frozen_with_new_fields()
def test_provider_response_extra_default_empty_dict()

# === extract_think_tags ===
def test_extract_think_tags_extracts_think_content()
def test_extract_think_tags_handles_no_tags()
def test_extract_think_tags_handles_empty_content()
def test_extract_think_tags_strips_from_content()

# === _ReasoningMixin ===
def test_reasoning_mixin_extracts_native_field()
def test_reasoning_mixin_falls_back_to_tags()
def test_reasoning_mixin_respects_custom_reasoning_key()

# === Provider Registry ===
def test_register_provider_registers_class()
def test_register_provider_rejects_duplicate()
def test_create_provider_instantiates_correct_class()
def test_list_providers_returns_all()

# === OpenAICompatibleProvider ===
def test_openai_extracts_standard_content()
def test_openai_extracts_tool_calls()
def test_openai_extracts_refusal()
def test_openai_extracts_reasoning_tokens()
def test_openai_extracts_reasoning_via_key()
def test_openai_extracts_reasoning_via_tags_fallback()
def test_openai_serialize_messages_injects_reasoning_history()

# === DeepSeekProvider ===
def test_deepseek_extracts_reasoning_content()
def test_deepseek_inherits_openai_base()

# === GLMProvider ===
def test_glm_inherits_openai_base()

# === GroqProvider ===
def test_groq_uses_reasoning_key()
def test_groq_strips_reasoning_from_history()

# === AnthropicProvider（Phase 2.8b）===
def test_anthropic_extracts_text_blocks()
def test_anthropic_extracts_thinking_blocks()
def test_anthropic_extracts_tool_use_blocks()
def test_anthropic_handles_redacted_thinking()
def test_anthropic_handles_interleaved_thinking()
def test_anthropic_extracts_signature()
def test_anthropic_serializes_thinking_replay()
def test_anthropic_format_tools_uses_input_schema()

# === ContextBuilder 推理策略 ===
def test_context_builder_strip_policy_drops_text_keeps_signature()
def test_context_builder_append_policy_includes_all()
def test_context_builder_budget_policy_includes_when_room()
def test_context_builder_budget_policy_strips_when_over()
def test_context_builder_always_preserves_signature()

# === 集成测试 ===
def test_agent_loop_propagates_reasoning_to_context_message()
def test_agent_loop_propagates_signature_to_context_message()
def test_multi_turn_preserves_signature_across_turns()
```

## 7. ChannelPlugin 设计细节（Plugin 系统的扩展）

### 7.1 背景与设计目标

**为什么 Channel 是 Plugin？**

- 复用插件权限系统、生命周期管理和能力注册机制
- 支持第三方 Channel 插件无须修改核心代码
- 灵活支持多种通信协议（HTTP、WebSocket、SSE）组合

**参考项目**：OpenClaw Gateway-Channel 模型、OneBot 协议、NapCat 的多通道设计

### 7.2 ChannelPlugin 核心接口

```python
class ChannelPlugin(Plugin):
    """所有 Channel 插件的基类。"""

    # 通信方式支持声明（在 plugin.yaml 中对应 capabilities）
    SUPPORT_HTTP_SERVER: bool = False      # Bot 提供 HTTP 端点，外部推送
    SUPPORT_HTTP_CLIENT: bool = False      # Bot 主动 HTTP 请求外部系统
    SUPPORT_WEBSOCKET_SERVER: bool = False # Bot 提供 WebSocket，外部连接
    SUPPORT_WEBSOCKET_CLIENT: bool = False # Bot 连接到外部 WebSocket
    SUPPORT_SSE: bool = False              # Bot 通过 SSE 单向推送

    async def handle_inbound_event(self, event: dict) -> None:
        """处理来自外部系统的事件（webhook、WebSocket 消息等）。

        将平台原生事件格式转换为 InboundMessage，并触发 Agent Loop。
        """
        ...

    async def send_message(
        self,
        target: str,  # 平台用户/群组 ID
        message: OutboundMessage
    ) -> str:
        """向外部系统发送消息，返回平台消息 ID。

        支持流式响应分片、速率限制等。
        """
        ...

    async def get_user_info(self, user_id: str) -> dict:
        """获取用户信息（可选）。"""
        ...

    async def get_group_info(self, group_id: str) -> dict:
        """获取群组信息（可选）。"""
        ...
```

### 7.3 通信方式详解

各 Channel 可选择一种或多种组合：

| 方式 | 说明 | 场景 | 示例 |
| ---- | ---- | ---- | ---- |
| **HTTP Server** | Bot 提供 HTTP POST 端点接收 webhook | 外部系统主动推送事件 | NapCat `POST /channels/qq/webhook` |
| **HTTP Client** | Bot 主动轮询或通过 HTTP 请求向外部发送消息 | Bot 需主动控制消息流向 | Telegram Polling API、HTTP 心跳 |
| **WebSocket Server** | Bot 监听 WebSocket 端口，外部系统连接 | 需要持久连接、双向实时通信 | Web 端、自定义客户端 |
| **WebSocket Client** | Bot 连接到外部 WebSocket 端点 | Bot 作为客户端和中心网关通信 | 云服务集中管理 |
| **SSE** | Bot 通过 HTTP SSE 单向推送事件 | 只需事件流，不需请求-响应模式 | 浏览器、Node.js 客户端 |

### 7.4 典型设计模式（参考 NapCat/OneBot）

**模式 A：HTTP Server + HTTP Client**（推荐开始）

```text
外部系统 ──webhook──> Bot HTTP Server ──处理──> InboundMessage
        <────HTTP────                          OutboundMessage
```

优点：

- 无需长连接，可跨域
- 支持负载均衡和水平扩展
- 外部系统可异步推送，Bot 可异步发送

缺点：

- 需要鉴权机制（防 webhook 伪造）
- 频率限制和重试需自行管理

NapCat 示例配置：

```yaml
channels:
  qq:
    type: qq
    plugin: qq_channel
    config:
      webhook_url: "http://bot.local:8888/channels/qq/webhook"
      api_endpoint: "http://napcat.local:3000"
      token: "secret_token"
```

**模式 B：WebSocket 双向**（长连接、实时）

```text
外部系统 ◄──► Bot WebSocket Server
```

优点：

- 真正双向、低延迟
- 一个连接复用，性能好

缺点：

- 需要连接管理和重连机制
- 难以水平扩展（需要 session 亲和性）

**模式 C：混合模式**（最灵活）

```text
接收事件用 WebSocket Server
发送消息用 HTTP Client
```

或其他组合，根据平台特性选择。

### 7.5 plugin.yaml 示例

```yaml
id: qq_channel
name: "QQ Channel (via NapCat)"
version: "0.1.0"
description: "QQ 平台接入（通过 NapCat OneBot 协议）"

# 权限声明
permissions:
  - resource: "network"
    action: ["http_post", "http_get", "websocket"]
    targets:
      - "http://napcat.local:*"
      - description: "NapCat 本地服务"

# 能力声明
capabilities:
  - type: "channel"
    name: "qq_channel"
    protocols:
      - "http_server"    # Bot 提供 webhook 端点
      - "http_client"    # Bot 向 NapCat 发送消息

# 配置架构
configSchema:
  type: "object"
  properties:
    webhook_secret:
      type: "string"
      description: "Webhook 鉴权密钥（玄性）"
    napcat_endpoint:
      type: "string"
      description: "NapCat API 端点"
      default: "http://localhost:3000"
    bot_qq:
      type: "string"
      description: "Bot 的 QQ 号"
```

### 7.6 权限与安全

- ChannelPlugin 需声明 network 权限（HTTP、WebSocket）
- Webhook 端点需要鉴权（HMAC 签名或 Token）
- 频率限制由 Plugin 或 Gateway 负责
- 敏感信息（Token、密钥）走系统级 Secret 管理

### 7.7 与 Phase 的关系

- **Phase 3**：定义 ChannelPlugin 基类、接口规范、manifest 契约
- **Phase 4**：实现第一个 ChannelPlugin（如 Telegram 或 QQ/NapCat）
- **Phase 5+**：第三方 Channel 插件生态，无需核心改动

## 8. 安全与可观测性基线

安全基线：

- 路径穿越防护。
- 插件最小权限。
- 节点认证和命令白名单。
- Webhook 鉴权（HMAC 签名或 Token）。
- 敏感信息脱敏日志。

可观测性基线：

- 结构化日志（请求 ID、会话 ID、节点 ID）。
- 基础健康检查（服务、数据库、节点连接）。
- 关键链路指标（响应时延、错误率、工具调用成功率）。
- Channel 级指标（消息发送成功率、延迟）。

## 9. 模块优先级

不严格按时间，而按“依赖风险 + 价值密度”排序。

### P0 - 必须先做

- `core`：应用容器、配置、日志、异常、事件
- `workspace`：安全文件边界、模板、上下文读取
- `agent.loop`：基础回路和终止条件
- `agent.providers.base + openai`：首个可用 Provider
- `agent.tools`：工具协议与执行器

原因：这些模块决定系统是否能形成最小闭环。

### P1 - MVP 关键能力

- `plugins.base + plugins.manifest/loader/manager`：插件发现、加载和生命周期管理
  - 包括 ChannelPlugin 基类定义（参考 OneBot/NapCat 多协议设计）
  - Plugin manifest 要明确支持的通信方式
- `plugins.permissions`：声明式权限系统（文件、网络、环境变量等）
- 首个具体 ChannelPlugin 实现（内置或内置示例）
  - 选择 **Telegram**（推荐：API 简单、建议 aiogram 库参考）
  - 或 **NapCat/QQ**（参考 OneBot webhook + HTTP 双向通信）
  - 设计重点：支持至少两种通信方式（如 HTTP Server + HTTP Client）
- `agent.memory + db.engine + repositories`：会话与记忆持久化

原因：把"单机可跑"推进到"多平台真实可用"，且 **Channel 通过插件系统接入，无需改动核心代码**。

### P2 - 可扩展与分布式

- `gateway.server/router/node_manager`：Gateway 消息路由和节点管理
- `node.client/connector/executor`：Node 连接器和远程执行
- `gateway.protocol`（版本化）：WebSocket 协议定版
- `plugins.hook` 和热加载：Hook 系统和插件动态加载
- 更多 ChannelPlugin 实现（第二、第三个平台）：
  - 参考第一个 Channel 的设计，复用 ChannelPlugin 基类
  - 可通过开发文档指导第三方贡献 Channel 插件

原因：进入多节点和复杂扩展场景，同时 Channel 生态可独立扩展。

### P3 - 体验与生态

- `cli` 完整命令集
- WebUI 管理与可视化
- 文档体系、示例插件、发布流水线

原因：提升可维护性、可交付性和社区可接入性。

## 10. 开发策略建议

- 先契约后实现：先固定输入输出模型，再写具体逻辑。
- 每完成一个优先级层级，进行一次接口冻结。
- 每个模块至少同时交付：代码、测试、文档最小集。
- 避免跨层捷径调用，宁可加一个小的协议层。

## 11. 类型安全事件系统（面向开发者与 AI 约束）

### 11.1 设计目标

事件系统不是“字符串 + dict”的通知器，而是核心契约层。目标：

- 类型安全：事件类型、载荷结构、处理器签名可被 pyright 严格检查。
- 行为可预测：发布语义、错误策略、并发模型固定。
- 可注入：处理器依赖通过统一 DI 注入，避免隐式全局状态。
- 可治理：对开发者与 AI 都有明确边界，减少“随手加事件名”和“任意 payload”。

### 11.2 Python 是否有统一规范

Python 对“进程内事件总线”没有单一官方规范（标准库未给出统一 EventBus 抽象）。实践上通常是：

- 用 `asyncio` 自研轻量总线并固定项目契约。
- 或选用轻量库（如 blinker/pyee）再叠加类型和并发约束。

对 nahida-bot，推荐第一条：自研轻量总线 + 强类型事件模型。

### 11.3 参考 Rust/C++ 项目的可迁移思路

可借鉴的“机制”而不是语法：

- Rust Tokio：`broadcast`/`mpsc` 的边界清晰，强调背压和关闭语义。
- Rust Bevy：事件是显式类型，系统按类型消费事件。
- C++ Boost.Signals2：订阅关系可管理、连接可断开、生命周期安全。

映射到 Python：

- 借鉴 Tokio：引入 `max_queue_size`、`publish` 超时、`shutdown` 明确语义。
- 借鉴 Bevy：事件用独立类建模，按事件类订阅，不用裸字符串驱动主逻辑。
- 借鉴 Signals2：`subscribe` 返回可取消句柄，避免遗忘反注册导致泄漏。

### 11.4 核心类型契约（建议固定）

建议目录：

```text
nahida_bot/core/events/
  __init__.py
  bus.py               # EventBus 实现
  types.py             # 事件基类与类型定义
  registry.py          # 允许事件类型白名单（可选）
  errors.py            # 事件系统错误定义
```

建议类型模型：

```python
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Generic, TypeVar
from uuid import UUID, uuid4

PayloadT = TypeVar("PayloadT")

@dataclass(slots=True, frozen=True)
class Event(Generic[PayloadT]):
  event_id: UUID = field(default_factory=uuid4)
  trace_id: str = ""
  source: str = ""
  occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  payload: PayloadT = field()
```

业务事件建议用独立类型，不建议用 `dict[str, Any]`：

```python
@dataclass(slots=True, frozen=True)
class AppStartedPayload:
  app_name: str
  debug: bool

@dataclass(slots=True, frozen=True)
class AppStarted(Event[AppStartedPayload]):
  ...
```

处理器签名建议：

```python
from typing import Protocol, TypeVar, Awaitable

EventT = TypeVar("EventT", bound=Event[object])

class EventHandler(Protocol[EventT]):
  async def __call__(self, event: EventT) -> None: ...
```

### 11.5 EventBus 语义（建议作为硬约束）

事件系统采用双层 API：

1. Core API（底层执行层，稳定、可预测、低魔法）

- `subscribe(event_type, handler) -> Subscription`
- `unsubscribe(event_type, handler) -> None`
- `publish(event) -> PublishResult`
- `publish_nowait(event) -> bool`
- `shutdown(timeout: float | None = None) -> None`

2. Facade API（上层开发体验层，Pythonic、声明式）

- `@events.on(EventType)` 装饰器式注册。
- `@events.on(EventType, depends=[Depends(check_xxx)])` 前置依赖。
- `events.emit(EventType(...))` 语义别名（内部仍委派到 `publish`）。
- `events.listener(EventType)` 上下文管理器（自动注册/解绑）。

语义约束：

- 订阅按 `event_type`（类）匹配，不按字符串名匹配核心逻辑。
- 默认策略：同一事件内按订阅顺序串行执行，事件与事件之间可并发。
- 单个 handler 失败不阻断同事件其他 handler；失败收敛到 `PublishResult.errors`。
- `publish` 可配置超时；超时和取消需要可观测（日志 + 指标）。
- `shutdown` 后拒绝新事件，并等待在途处理完成。

### 11.6 与 FastAPI/NoneBot2 风格 DI 的结合

可借鉴 FastAPI 的思路：

- handler 不直接读取全局单例，而是显式声明依赖。
- 依赖由容器/解析器提供，便于测试替身注入。

可借鉴 NoneBot2 的思路：

- 将处理器参数视作“可解析依赖”，由框架在调用前构建。
- 把上下文对象（session、bot、state）作为受控注入项，而非任意获取。
- 依赖求值采用 parse/check/solve 三段式，而非直接反射调用。

建议在事件系统中引入 `EventContext`：

```python
@dataclass(slots=True)
class EventContext:
  settings: Settings
  logger: logging.Logger
  app: Application
```

处理器签名建议统一为：

```python
async def handle_app_started(event: AppStarted, ctx: EventContext) -> None:
  ...
```

由 EventBus 在 dispatch 前注入 `ctx`，避免 handler 到处访问全局对象。

建议补充 `Depends` 风格语义（用于 Facade API）：

```python
from typing import Annotated

async def get_repo(ctx: EventContext) -> Repo: ...
async def guard_feature(ctx: EventContext) -> None: ...

@events.on(AppStarted, depends=[Depends(guard_feature)])
async def bootstrap(
  event: AppStarted,
  repo: Annotated[Repo, Depends(get_repo)],
) -> None:
  ...
```

依赖解析流程建议固定为：

1. parse：解析函数签名和 `Depends` 元信息。
2. check：执行 parameterless/pre-check 依赖（返回值忽略）。
3. solve：解析事件参数与子依赖值（支持缓存和校验）。
4. call：调用 handler。
5. teardown：清理生成器依赖/上下文资源。

### 11.7 约束开发者与 AI 的规则

为减少错误扩展，建议把以下规则写入实现注释与测试：

- 禁止在核心事件总线里传裸 `dict` payload（仅测试夹具可例外）。
- 新事件必须定义 payload 类型和事件类，并添加至少一条类型检查测试。
- 新 handler 必须显式声明 `event` 和 `ctx` 参数，不允许 `*args/**kwargs`。
- 事件类型命名固定在 `core.events.types`，禁止分散到任意模块。
- 事件处理副作用需可测试，不允许只写日志不暴露可断言行为。

### 11.8 渐进实施计划

Phase 1 建议拆成三步：

1. 建立 `core/events` 目录与类型契约（Event、EventHandler、Subscription）。
2. 在 `Application.initialize/start/stop` 中接入四个生命周期事件：
   - `AppInitializing`
   - `AppStarted`
   - `AppStopping`
   - `AppStopped`
3. 补齐测试：类型检查、订阅行为、异常隔离、shutdown 语义、生命周期事件触发。

## 12. 与 ROADMAP 的关系

- [docs/ROADMAP.md](docs/ROADMAP.md)：回答"做什么、做到什么程度"。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：回答"怎么分层、模块如何协作、先做哪些模块、Channel 作为 Plugin 的设计"。

两份文档应同步更新，不允许只改其中一份。
