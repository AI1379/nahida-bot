# 内部配置类详解

本文档梳理 nahida_bot 中所有散落的配置 dataclass / BaseModel，描述其字段含义、默认值，以及**配置值的实际来源**。

已有的 [CONFIGURATION.md](../CONFIGURATION.md) 描述了用户可编辑的 YAML 配置格式，本文档则从代码层面补充说明这些 YAML 配置如何流入各个内部配置类，以及哪些配置类目前**完全使用硬编码默认值**、无法通过 YAML 覆盖。

---

## 配置流转总览

```
config.yaml + .env
      │
      ▼
 load_settings()                          ← core/config.py
      │
      ▼
 Settings (BaseModel, extra="allow")      ← core/config.py
  ├── providers → ProviderEntryConfig     → ProviderSlot → ChatProvider 子类
  │                └── ProviderModelConfig → ModelCapabilities
  ├── multimodal → MultimodalConfig       → MediaPolicy
  ├── system_prompt                        → RouterConfig (仅此一项)
  └── extra keys (telegram/milky...)      → PluginManifest.config → 各 Channel Config

 已接入 YAML 的内部配置类（通过 Settings 子模型映射）:
  ├── AgentLoopConfig        ← Settings.agent (AgentConfig)
  ├── ContextBudget          ← Settings.context (ContextConfig)
  ├── SchedulerConfig        ← Settings.scheduler (SchedulerConfigModel)
  └── RouterConfig           ← Settings.router (RouterConfigModel)
```

---

## 一、从 YAML 接收配置的类

### 1.1 Settings — 根配置模型

- **类型**: Pydantic `BaseModel`，`frozen=True, extra="allow"`
- **定义**: [config.py:51](../../nahida_bot/core/config.py#L51)
- **实例化**: `load_settings()` 末尾 `Settings(**full_config)`
- **来源**: `config.yaml` + `.env` 环境变量插值 + CLI kwargs 合并

Settings 的已知字段（`providers`, `multimodal`, `system_prompt` 等）由 Pydantic 解析。由于 `extra="allow"`，所有未在模型中声明的顶层 dict 键（如 `telegram:`, `milky:`）会被保留在 `__pydantic_extra__` 中，后续由插件系统注入。

---

### 1.2 ProviderEntryConfig — Provider 配置条目

- **类型**: Pydantic `BaseModel`，`frozen=True, extra="allow"`
- **定义**: [config.py:26](../../nahida_bot/core/config.py#L26)
- **实例化**: Pydantic 自动解析 `Settings.providers` 字典值时创建
- **来源**: `config.yaml` 中 `providers` 下每个 provider 条目

```python
class ProviderEntryConfig(BaseModel):
    type: str = "openai-compatible"     # provider 类型标识
    api_key: str = ""                   # API 密钥
    base_url: str = ""                  # API 端点
    models: list[ProviderModelEntry] = []  # 模型列表
```

`extra="allow"` 使得 provider 特有的字段（如 `merge_system_messages`, `stream_responses`, `store_responses`, `reasoning_effort` 等）可以透传，在 `app.py` 中通过 `getattr()` 读取后传入 `create_provider()`。

---

### 1.3 ProviderModelConfig — 模型能力声明

- **类型**: Pydantic `BaseModel`
- **定义**: [config.py:14](../../nahida_bot/core/config.py#L14)
- **实例化**: Pydantic 解析 `ProviderEntryConfig.models` 中对象形式的条目时创建
- **来源**: YAML 中每个模型条目的 `name` + `capabilities` 字段

```python
class ProviderModelConfig(BaseModel):
    name: str
    capabilities: dict[str, Any] = {}
```

模型可以是纯字符串（`"deepseek-v4-pro"`）或带 capabilities 的对象。capabilities 字典随后在 `app.py` 的 `_model_capabilities_from_config()` 中展开为 `ModelCapabilities` dataclass。

---

### 1.4 MultimodalConfig — 多模态配置

- **类型**: Pydantic `BaseModel`，`frozen=True, extra="allow"`
- **定义**: [config.py:37](../../nahida_bot/core/config.py#L37)
- **实例化**: 作为 `Settings.multimodal` 字段的默认值或由 YAML 解析
- **来源**: `config.yaml` 中 `multimodal` 节

```python
class MultimodalConfig(BaseModel):
    image_fallback_mode: ImageFallbackMode = "auto"        # auto | tool | off
    media_context_policy: MediaContextPolicy = "cache_aware" # cache_aware | description_only | native_recent
    image_fallback_provider: str = ""
    image_fallback_model: str = ""
    max_images_per_turn: int = 4
    max_image_bytes: int = 10485760       # 10 MB
    media_cache_ttl_seconds: int = 3600
```

该配置被传递给 `SessionRunner` 和 `MediaPolicy`，控制图片处理策略。

---

### 1.5 MilkyPluginConfig — Milky (QQ) 频道配置

- **类型**: Pydantic `BaseModel`（含 field validators 和 model validator）
- **定义**: [milky/config.py:12](../../nahida_bot/channels/milky/config.py#L12)
- **实例化**: `parse_milky_config(manifest.config)` → `MilkyPluginConfig.model_validate(raw)`
- **来源**: 两层合并 —— `milky/plugin.yaml` 中的默认值 ← `config.yaml` 中 `milky:` 节覆盖

主要字段分组：

| 分组 | 关键字段 | 说明 |
|------|---------|------|
| 连接 | `base_url`, `access_token`, `api_prefix`, `event_path`, `ws_url` | Milky 实例连接参数 |
| 触发 | `command_prefix`, `group_trigger_mode`, `allowed_friends`, `allowed_groups` | 消息触发与访问控制 |
| 超时/重连 | `connect_timeout`, `heartbeat_timeout`, `reconnect_initial_delay`, `reconnect_max_delay` | 连接稳定性 |
| 发送/媒体 | `send_retry_attempts`, `max_text_length`, `media_download_dir`, `cache_media_on_receive` | 发送与媒体处理 |
| 转发 | `max_forward_depth`, `max_forward_messages`, `forward_render_max_chars` | 合并转发消息限制 |

这是项目中唯一使用完整 Pydantic 验证的 channel config。Telegram 频道直接用 `dict.get()` 读取，没有独立 config 类。

---

### 1.6 ModelCapabilities — 模型能力描述

- **类型**: `dataclass(slots=True, frozen=True)`
- **定义**: [providers/base.py:15](../../nahida_bot/agent/providers/base.py#L15)
- **实例化**: `app.py` 中 `_model_capabilities_from_config()` 从 YAML capabilities 字典构建，或使用 `ModelCapabilities()` 默认值
- **来源**: YAML 中每个模型条目的 `capabilities` 子字段 → `_model_capabilities_from_config()` 展开

```python
@dataclass(slots=True, frozen=True)
class ModelCapabilities:
    text_input: bool = True
    image_input: bool = False
    tool_calling: bool = True
    reasoning: bool = False
    prompt_cache: bool = False
    prompt_cache_images: bool = False
    explicit_context_cache: bool = False
    prompt_cache_min_tokens: int = 0
    max_image_count: int = 0
    max_image_bytes: int = 0
    supported_image_mime_types: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")
    image_generation: bool = False
    web_search: bool = False
    file_search: bool = False
    code_interpreter: bool = False
```

---

### 1.7 MediaPolicy — 媒体验证与缓存策略

- **类型**: `dataclass(slots=True, frozen=True)`
- **定义**: [media/resolver.py:39](../../nahida_bot/agent/media/resolver.py#L39)
- **实例化**: `app.py:291` 中从 `Settings.multimodal` 字段构建
- **来源**: `MultimodalConfig` 的子集映射

```python
@dataclass(slots=True, frozen=True)
class MediaPolicy:
    max_image_bytes: int = 10 * 1024 * 1024       # ← multimodal.max_image_bytes
    supported_mime_types: tuple[str, ...] = (...)   # 硬编码
    max_images_per_turn: int = 4                    # ← multimodal.max_images_per_turn
    cache_ttl_seconds: int = 3600                   # ← multimodal.media_cache_ttl_seconds
    cache_dir: str = ""                             # 根据 db_path 计算
```

`MediaPolicy` 是 `MultimodalConfig` 到 `MediaResolver` 之间的桥梁。`app.py` 在初始化时将 `MultimodalConfig` 的部分字段映射过来。

---

### 1.8 ProviderSlot — Provider 运行时描述

- **类型**: `dataclass(slots=True)`
- **定义**: [providers/manager.py:11](../../nahida_bot/agent/providers/manager.py#L11)
- **实例化**: `app.py` 中 `_init_providers()` 遍历 `Settings.providers` 时逐个创建
- **来源**: 由 `ProviderEntryConfig` + `ModelCapabilities` + `ChatProvider` 实例组装

```python
@dataclass(slots=True)
class ProviderSlot:
    id: str                                         # provider ID (如 "deepseek-main")
    provider: ChatProvider                          # 实例化后的 provider 对象
    context_builder: ContextBuilder                 # 附带的上下文构建器
    default_model: str                              # 默认模型名
    available_models: list[str]                     # 可用模型列表
    capabilities_by_model: dict[str, ModelCapabilities]  # 每个模型的能力
```

---

### 1.9 ChatProvider 子类 — 同时承担配置角色

所有 `ChatProvider` 子类都是 `@dataclass(slots=True)`，其字段既作为配置存储也作为运行时使用。它们通过 `create_provider(type, **kwargs)` 工厂方法实例化，kwargs 来自 `ProviderEntryConfig` 的字段和 extra 属性。

| Provider 类 | 定义位置 | 特有配置字段 |
|-------------|---------|------------|
| `OpenAICompatibleProvider` | [openai_compatible.py:34](../../nahida_bot/agent/providers/openai_compatible.py#L34) | `base_url`, `api_key`, `model`, `merge_system_messages`, `stream_responses`, `reasoning_key` |
| `DeepSeekProvider` | [deepseek.py:13](../../nahida_bot/agent/providers/deepseek.py#L13) | 继承 OpenAI + `thinking_enabled`, `reasoning_effort` |
| `GLMProvider` | [glm.py:12](../../nahida_bot/agent/providers/glm.py#L12) | 继承 OpenAI，无额外字段 |
| `GroqProvider` | [groq.py:13](../../nahida_bot/agent/providers/groq.py#L13) | 继承 OpenAI + `reasoning_key="reasoning"` |
| `AnthropicProvider` | [anthropic.py:49](../../nahida_bot/agent/providers/anthropic.py#L49) | `base_url`, `api_key`, `model`, `max_tokens`, `stream_responses` |
| `MinimaxProvider` | [minimax.py:17](../../nahida_bot/agent/providers/minimax.py#L17) | 继承 Anthropic，无额外字段 |
| `OpenAIResponsesProvider` | [openai_responses.py:54](../../nahida_bot/agent/providers/openai_responses.py#L54) | `store_responses`, `use_previous_response_id`, `stream_responses`, `reasoning_effort`, `max_output_tokens`, `built_in_tools` |

---

### 1.10 RouterConfig — 消息路由配置

- **类型**: `dataclass(slots=True)`
- **定义**: [router.py:41](../../nahida_bot/core/router.py#L41)
- **YAML 接入**: [config.py](../../nahida_bot/core/config.py) 中 `RouterConfigModel` BaseModel → `Settings.router`
- **映射**: `app.py` 中从 `self.settings.router` 构建 `RouterConfig` 并传入 `MessageRouter(config=...)`。`system_prompt` 优先使用顶层 `settings.system_prompt`

```python
@dataclass(slots=True)
class RouterConfig:
    system_prompt: str = "You are a helpful assistant."   # ← settings.system_prompt
    max_history_turns: int = 50                            # ← settings.router.max_history_turns
    agent_enabled: bool = True                             # ← settings.router.agent_enabled
    command_timeout_seconds: float = 30.0                  # ← settings.router.command_timeout_seconds
    command_timeout_message: str = "Command timed out..."  # ← settings.router.command_timeout_message
    reply_to_inbound: bool = True                          # ← settings.router.reply_to_inbound
```

---

## 二、已接入 YAML 的内部配置类（通过 Settings 子模型映射）

以下配置类原本完全硬编码，现已通过 Settings 中的 Pydantic BaseModel 子字段接入 YAML 配置。运行时仍使用原有 dataclass，`app.py` 在初始化时将 Settings 子模型的值映射到 dataclass 实例。

### 2.1 AgentLoopConfig — Agent 循环配置

- **定义**: [loop.py:78](../../nahida_bot/agent/loop.py#L78)
- **YAML 接入**: [config.py](../../nahida_bot/core/config.py) 中 `AgentConfig` BaseModel → `Settings.agent`
- **映射**: `app.py` 中从 `self.settings.agent` 构建 `AgentLoopConfig` 并传入 `AgentLoop(config=...)`

```python
@dataclass(slots=True, frozen=True)
class AgentLoopConfig:
    max_steps: int = 8                       # 最大工具调用迭代轮数
    provider_timeout_seconds: float = 30.0   # 单次 LLM 调用超时
    retry_attempts: int = 2                  # LLM 调用重试次数
    retry_backoff_seconds: float = 0.2       # 重试退避间隔
    tool_timeout_seconds: float = 135.0      # 工具执行超时
    tool_retry_attempts: int = 1             # 工具执行重试次数
    tool_retry_backoff_seconds: float = 0.1  # 工具重试退避间隔
    max_tool_log_chars: int = 400            # 工具日志截断长度
    tool_use_system_prompt: str = "..."      # 注入的工具使用提示
    provider_error_template: str = "..."     # provider 错误消息模板
```

### 2.2 ContextBudget — 上下文窗口预算

- **定义**: [context.py:74](../../nahida_bot/agent/context.py#L74)
- **YAML 接入**: [config.py](../../nahida_bot/core/config.py) 中 `ContextConfig` BaseModel → `Settings.context`
- **映射**: `app.py` 中 `_build_context_budget()` 将 `ContextConfig` 映射为 `ContextBudget`（含 `reasoning_policy` 字符串 → 枚举转换）

```python
@dataclass(slots=True, frozen=True)
class ContextBudget:
    max_tokens: int = 8000                   # 最大 token 数
    reserved_tokens: int = 1000              # 为输出保留的 token
    max_chars: int | None = None             # 字符数上限（兼容旧逻辑）
    reserved_chars: int = 0                  # 字符保留数
    summary_max_chars: int = 600             # 历史摘要最大字符数
    reasoning_policy: ReasoningPolicy = ReasoningPolicy.BUDGET  # reasoning 截断策略
    max_reasoning_tokens: int = 2000         # reasoning chain 预算
```

### 2.3 SchedulerConfig — 定时任务调度配置

- **定义**: [scheduler/models.py:33](../../nahida_bot/scheduler/models.py#L33)
- **YAML 接入**: [config.py](../../nahida_bot/core/config.py) 中 `SchedulerConfigModel` BaseModel → `Settings.scheduler`
- **映射**: `app.py` 中从 `self.settings.scheduler` 构建 `SchedulerConfig` 并传入 `SchedulerService(config=...)`

```python
@dataclass(slots=True, frozen=True)
class SchedulerConfig:
    poll_interval_seconds: float = 1.0       # 调度轮询间隔
    max_concurrent_fires: int = 5            # 最大并发执行数
    job_timeout_seconds: float = 120.0       # 单任务超时
    min_interval_seconds: int = 60           # cron 最小间隔
    max_prompt_chars: int = 4000             # 定时任务 prompt 最大字符数
    max_jobs_per_chat: int = 20              # 每个会话最大任务数
    failure_retry_seconds: int = 300         # 失败重试等待
    max_consecutive_failures: int = 3        # 连续失败上限
```

---

## 三、插件系统内部的配置相关模型

这些不是运行时可调的配置，而是插件元数据声明，记录以供参考。

### 3.1 PluginManifest — 插件清单

- **定义**: [plugins/manifest.py:64](../../nahida_bot/plugins/manifest.py#L64)
- **实例化**: `parse_manifest()` 解析插件目录中的 `plugin.yaml` 文件
- **来源**: 各插件自带的 `plugin.yaml`，非主配置文件

```python
class PluginManifest(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    entrypoint: str
    nahida_bot_version: str = ""
    sdk_version: str = ""
    load_phase: Literal["pre-agent", "post-agent"] = "post-agent"
    permissions: Permissions                  # 网络/文件系统/内存/系统权限
    capabilities: Capabilities                # 工具/事件订阅声明
    config: dict[str, Any] = {}              # ← YAML 中对应插件的配置注入到此处
    depends_on: list[PluginDependency] = []
```

### 3.2 Permissions 相关模型

| 类 | 定义位置 | 说明 |
|----|---------|------|
| `NetworkPermission` | [manifest.py:12](../../nahida_bot/plugins/manifest.py#L12) | `outbound: list[str]`, `inbound: bool` |
| `FilesystemPermission` | [manifest.py:19](../../nahida_bot/plugins/manifest.py#L19) | `read: list[str]`, `write: list[str]` |
| `MemoryPermission` | [manifest.py:26](../../nahida_bot/plugins/manifest.py#L26) | `read: bool`, `write: bool` |
| `SystemPermission` | [manifest.py:33](../../nahida_bot/plugins/manifest.py#L33) | `env_vars`, `subprocess`, `signal_handlers` |
| `Permissions` | [manifest.py:41](../../nahida_bot/plugins/manifest.py#L41) | 组合以上四项 |

---

## 四、配置来源汇总

| 配置类 | YAML 可配置 | 来源路径 |
|--------|:----------:|---------|
| `Settings` | 是 | `config.yaml` → `load_settings()` |
| `ProviderEntryConfig` | 是 | `config.yaml` → `Settings.providers` |
| `ProviderModelConfig` | 是 | `config.yaml` → `ProviderEntryConfig.models` |
| `MultimodalConfig` | 是 | `config.yaml` → `Settings.multimodal` |
| `MilkyPluginConfig` | 是 | `config.yaml` → `Settings.extra` → `PluginManifest.config` |
| `ModelCapabilities` | 是 | `config.yaml` → `ProviderModelConfig.capabilities` → `_model_capabilities_from_config()` |
| `MediaPolicy` | 部分 | `MultimodalConfig` 子集映射，`supported_mime_types` 硬编码 |
| `ProviderSlot` | 间接 | 由 `ProviderEntryConfig` + `ModelCapabilities` 组装 |
| `ChatProvider` 子类 | 是 | `ProviderEntryConfig` + extra 字段 → `create_provider()` |
| `RouterConfig` | 是 | `Settings.router` (`RouterConfigModel`) → `app.py` 映射 |
| `AgentLoopConfig` | 是 | `Settings.agent` (`AgentConfig`) → `app.py` 映射 |
| `ContextBudget` | 是 | `Settings.context` (`ContextConfig`) → `_build_context_budget()` 映射 |
| `SchedulerConfig` | 是 | `Settings.scheduler` (`SchedulerConfigModel`) → `app.py` 映射 |
