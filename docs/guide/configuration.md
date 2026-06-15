# 配置参考

Nahida Bot 从 YAML 文件、`.env` 文件和环境变量读取配置。值的合并优先级从高到低：

1. CLI 参数（`--debug`、`--config-yaml`）
2. `.env` 文件中的值
3. YAML 配置文件
4. 内置默认值

所有 YAML 值都支持环境变量插值，语法为 `${VAR}` 或 `${VAR:fallback}`。

---

## 顶层设置

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `app_name` | `str` | `"Nahida Bot"` | 应用名称，用于日志、生命周期事件和 memory dreaming prompt |
| `debug` | `bool` | `false` | 调试模式。开启后若未显式设置 `log_level`，则强制为 `DEBUG` |
| `log_level` | `str` | `"INFO"` | 日志级别：`TRACE`、`DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `log_json` | `bool\|null` | `null` | JSON 日志输出。`null` = 自动（生产环境用 JSON，调试模式用控制台） |
| `log_file` | `str\|null` | `null` | 可选日志文件路径。设置后会额外添加文件 handler，并自动创建父目录 |
| `log_file_level` | `str\|null` | `null` | 文件日志级别。`null` = 跟随 `log_level`，可设为 `DEBUG` 让文件收集更详细日志 |
| `log_file_json` | `bool` | `true` | 文件日志是否使用 JSON Lines 格式 |
| `host` | `str` | `"127.0.0.1"` | 服务器绑定地址（保留） |
| `port` | `int` | `6185` | 服务器绑定端口（保留） |
| `db_path` | `str` | `"./data/nahida.db"` | SQLite 数据库文件路径 |
| `workspace_base_dir` | `str` | `"./data/workspace"` | 工作区存储目录 |
| `plugin_paths` | `list[str]` | `["./plugins"]` | 额外的插件扫描目录 |
| `discover_builtin_channels` | `bool` | `true` | 自动发现内置频道插件 |
| `system_prompt` | `str` | `"You are a helpful assistant."` | Agent 对话的默认系统提示词 |
| `default_provider` | `str` | `""` | 默认使用的 provider ID。空值 = 使用 `providers` 中的第一个 |
| `providers` | `dict` | `{}` | LLM provider 配置（见下文） |
| `multimodal` | `object` | （见下文） | 图片/媒体处理配置 |
| `agent` | `object` | （见下文） | Agent 循环配置 |
| `context` | `object` | （见下文） | 上下文窗口预算配置 |
| `scheduler` | `object` | （见下文） | 定时任务调度配置 |
| `router` | `object` | （见下文） | 消息路由配置 |
| `webapi` | `object` | （见下文） | WebAPI 服务配置 |
| `webui` | `object` | （见下文） | WebUI 控制台配置 |
| `memory` | `object` | （见下文） | 长期记忆与 embedding 配置 |
| `enable_silent_reply` | `bool` | `true` | 全局开关：是否允许 Agent 以 `NO_REPLY` 静默回复 |
| `model_routing` | `dict` | `{}` | ~~Legacy，已忽略。~~ 内部任务路由改用 model spec + `ModelRouter` |

### 示例

```yaml
app_name: "Nahida Bot"
debug: false
log_level: "INFO"
log_file: "./data/logs/nahida.log"
log_file_level: "DEBUG"
db_path: "./data/nahida.db"
system_prompt: "You are a helpful assistant."
default_provider: deepseek-main
```

---

## LLM Providers

`providers` 是一个字典，每个键是自定义的 provider ID，用于 `default_provider` 和 `/model` 命令中引用。

### Provider 条目

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `type` | `str` | `"openai-compatible"` | Provider 类型，见 [Provider 类型](#provider-类型) |
| `api_key` | `str` | `""` | API 密钥，为空时跳过该 provider |
| `base_url` | `str` | `""` | API 端点基础 URL |
| `models` | `list` | `[]` | 模型列表，第一个元素为默认模型 |
| `merge_system_messages` | `bool` | `false` | 发送前合并所有 system 消息为一条（用于需要单一 system 的后端） |
| `stream_responses` | `bool` | `false` | provider 内部使用上游流式接口接收并聚合返回；不改变 channel 发送方式 |

`stream_responses` 当前支持 `openai-compatible` 族（含 `deepseek`、`glm`、`groq`）、`anthropic` 族（含 `minimax`）和 `openai-responses`。开启后 provider 会持续读取上游 SSE 事件，最后仍返回一个完整 `ProviderResponse`；这主要用于长推理/重任务时区分“服务端仍在生成”与“服务端完全无响应”。当前不会把 token 级增量直接发送到聊天频道。

`deepseek` provider 还支持 `thinking_enabled`（默认 `true`）和 `reasoning_effort`；前者控制是否向请求体注入 `thinking: {"type": "enabled"}`，后者可被运行时 `reasoning.effort` 覆盖。

### Anthropic / Minimax 输出上限

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `max_tokens` | `int` | `16000` | Anthropic / Minimax 的输出 token 上限；适用于 `anthropic` 和 `minimax` provider |
| `reasoning_effort` | `str\|null` | `null` | Claude 原生推理深度，注入请求体的 `output_config.effort`（`"low"`/`"medium"`/`"high"`/`"max"`）。可被运行时 `/reasoning` 覆盖。**仅 Claude 原生端点**（`anthropic` provider）——Minimax 兼容端点可能拒绝该字段 |
| `context_1m` | `bool` | `false` | 启用 1M 上下文窗口 beta，发送 `anthropic-beta: context-1m-2025-08-07` 头。付费 beta，默认关闭；仅 `claude-sonnet-4.6`/`claude-opus-4.6` 等支持的模型可用 |

### 模型条目

`models` 中的每个元素可以是纯字符串或对象：

```yaml
models:
  - "deepseek-v4-pro"                      # 纯字符串
  - name: "Qwen/Qwen3.6-35B-A3B"           # 带 capabilities 的对象
    tags: [primary, vision]
    capabilities:
      image_input: true
      max_image_count: 4
      context_window: 128000
```

### 模型标签

每个模型可以声明 `tags`（字符串列表），供内部任务 model spec 解析使用。详见 [Model Specs](#model-specs)。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `tags` | `list[str]` | `[]` | 模型标签，用于 model spec 的 tag 匹配 |

### 模型能力声明

在 `capabilities` 下按模型声明其支持的能力：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `text_input` | `bool` | `true` | 接受文本输入 |
| `image_input` | `bool` | `false` | 原生接受图片输入 |
| `tool_calling` | `bool` | `true` | 支持函数/工具调用 |
| `reasoning` | `bool` | `false` | 支持推理/思维链 token |
| `prompt_cache` | `bool` | `false` | 支持 prompt 缓存 |
| `prompt_cache_images` | `bool` | `false` | 在 prompt 中缓存图片 |
| `explicit_context_cache` | `bool` | `false` | 需要显式缓存控制标记 |
| `prompt_cache_min_tokens` | `int` | `0` | 缓存断点的最小 token 数 |
| `max_image_count` | `int` | `0` | 每次请求最大图片数（0 = 不限） |
| `max_image_bytes` | `int` | `0` | 单张图片最大字节数（0 = 不限） |
| `supported_image_mime_types` | `list[str]` | `["image/jpeg", "image/png", "image/webp"]` | 接受的 MIME 类型 |
| `context_window` | `int\|null` | `null` | 模型实际上下文窗口；声明后每轮按模型重算 prompt 预算 |
| `max_context_window` | `int\|null` | `null` | 模型可配置的最大上下文窗口；当 `context_window` 为空时作为 fallback |
| `effective_context_window_percent` | `int` | `95` | 可用于 prompt 的上下文比例；默认保留 5% 余量 |
| `auto_compact_token_limit` | `int\|null` | `null` | 自动摘要/滑窗的软阈值；为空时按 90% context window 派生 |
| `image_generation` | `bool` | `false` | 模型可通过内置工具生成图片 |
| `web_search` | `bool` | `false` | 模型支持内置网页搜索 |
| `file_search` | `bool` | `false` | 模型支持内置文件搜索 |
| `code_interpreter` | `bool` | `false` | 模型支持内置代码解释器 |

### OpenAI Responses API 选项

以下字段仅在 `type: "openai-responses"` 时生效：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `store_responses` | `bool` | `false` | 启用响应持久化，用于 `previous_response_id` 链式调用 |
| `use_previous_response_id` | `bool` | `false` | 开启后从历史 assistant metadata 中查找上一轮 response id，并只发送新增输入 |
| `reasoning_effort` | `str` | `null` | 推理深度：`"low"`、`"medium"`、`"high"` |
| `max_output_tokens` | `int` | `null` | 最大输出 token（替代 `max_tokens`） |
| `built_in_tools` | `list[str]` | `null` | 启用的内置工具：`"web_search"`、`"file_search"`、`"image_generation"`、`"code_interpreter"` |

### Provider 类型

| 类型 | 类 | 说明 |
|------|----|------|
| `openai-compatible` | `OpenAICompatibleProvider` | 通用 `/chat/completions` 端点 |
| `deepseek` | `DeepSeekProvider` | DeepSeek（扩展 OpenAI 兼容，增加思维模式） |
| `glm` | `GLMProvider` | GLM / 智谱（完全 OpenAI 兼容） |
| `groq` | `GroqProvider` | Groq（OpenAI 兼容，不同的 reasoning 字段名） |
| `anthropic` | `AnthropicProvider` | Anthropic Claude（独立协议） |
| `minimax` | `MinimaxProvider` | Minimax（Anthropic 兼容端点） |
| `openai-responses` | `OpenAIResponsesProvider` | OpenAI Responses API（`/v1/responses`），支持内置工具和有状态链式调用 |

### 示例

```yaml
providers:
  deepseek-main:
    type: deepseek
    api_key: "${DEEPSEEK_LLM_API_KEY:}"
    base_url: "${DEEPSEEK_LLM_BASE_URL:https://api.deepseek.com}"
    stream_responses: true
    models: ["deepseek-v4-pro", "deepseek-v4-flash"]

  siliconflow:
    type: "openai-compatible"
    api_key: "${SILICONFLOW_LLM_API_KEY:}"
    base_url: "${SILICONFLOW_LLM_BASE_URL:https://api.siliconflow.cn/v1}"
    merge_system_messages: true
    stream_responses: true
    models:
      - "Pro/zai-org/GLM-5"
      - name: "Qwen/Qwen3.6-35B-A3B"
        capabilities:
          image_input: true
          max_image_count: 4
          max_image_bytes: 10485760

  minimax:
    type: minimax
    api_key: "${MINIMAX_LLM_API_KEY:}"
    base_url: "https://api.minimaxi.com/anthropic"
    stream_responses: true
    models: ["MiniMax-M2.5"]

  openai:
    type: "openai-responses"
    api_key: "${OPENAI_API_KEY:}"
    base_url: "${OPENAI_API_BASE_URL:https://api.openai.com/v1}"
    store_responses: true
    use_previous_response_id: false
    stream_responses: true
    reasoning_effort: "medium"
    built_in_tools: ["web_search", "image_generation"]
    models:
      - name: "gpt-5.2"
        capabilities:
          image_input: true
          image_generation: true
          web_search: true
          tool_calling: true
          reasoning: true

default_provider: deepseek-main
```

---

## Model Specs

内部任务的模型配置使用单个 model spec 字符串。model spec 可以是 tag、`provider/model` 或裸模型名，统一由 `ModelRouter.resolve()` 解析。

常用默认 tag：

| Tag | 用途 |
|----|------|
| `primary` | 主对话模型 |
| `memory` | memory dreaming / consolidation |
| `embedding` | 文本 embedding |
| `reranker` | 检索重排 |
| `vision` | 图片理解 fallback |
| `cheap` | 低成本后台任务标记 |

示例：

```yaml
memory:
  consolidation:
    rule_based_enabled: false
  embedding:
    model: embedding

multimodal:
  image_fallback_model: siliconflow/Qwen/Qwen3.6-35B-A3B
```

---

## 多模态 / 图片处理

在 `multimodal` 键下配置。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `image_fallback_mode` | `str` | `"auto"` | 主模型不支持图片时的策略：`auto`（自动调用 fallback 视觉模型）、`tool`（注入 `image_understand` 工具）、`off`（跳过图片） |
| `media_context_policy` | `str` | `"cache_aware"` | 历史中的媒体保留方式：`cache_aware`（近期图片保留原生块，旧的降级为描述）、`native_recent`（仅最新图片保留原生）、`description_only`（全部使用文本描述） |
| `image_fallback_provider` | `str` | `""` | Legacy 字段；建议把 provider 写进 `image_fallback_model` |
| `image_fallback_model` | `str` | `""` | Fallback 视觉模型 spec；空则默认找 `vision` tag |
| `max_images_per_turn` | `int` | `4` | 每轮对话处理的最大图片数 |
| `max_image_bytes` | `int` | `10485760` | 单张图片最大字节数（10 MB） |
| `media_cache_ttl_seconds` | `int` | `3600` | 媒体缓存过期时间（秒） |

### 示例

```yaml
multimodal:
  image_fallback_mode: "auto"
  media_context_policy: "cache_aware"
  image_fallback_model: "siliconflow/Qwen/Qwen3.6-35B-A3B"
```

---

## Agent Loop

在 `agent` 键下配置。控制 LLM + 工具调用的迭代循环。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `max_steps` | `int` | `8` | 每轮对话最大工具调用迭代次数 |
| `provider_timeout_seconds` | `float` | `120.0` | 单次 LLM API 调用超时时间（秒） |
| `retry_attempts` | `int` | `2` | Provider 瞬态错误重试次数 |
| `retry_backoff_seconds` | `float` | `0.2` | 重试退避间隔（秒） |
| `tool_timeout_seconds` | `float` | `135.0` | 单次工具执行超时时间（秒） |
| `tool_retry_attempts` | `int` | `1` | 工具执行失败重试次数 |
| `tool_retry_backoff_seconds` | `float` | `0.1` | 工具重试退避间隔（秒） |
| `max_tool_log_chars` | `int` | `400` | 工具结果日志截断长度 |
| `tool_use_system_prompt` | `str` | （内置） | 注入的工具使用行为引导提示 |
| `provider_error_template` | `str` | （内置） | Provider 错误时的用户提示模板（支持 `{code}` 占位符） |

---

## Context Budget

在 `context` 键下配置。控制 prompt 上下文组装和 token 预算。
如果当前模型在 `providers.*.models[].capabilities` 中声明了 `context_window`，
运行时会优先使用模型窗口；这里的 `max_tokens` / `reserved_tokens` 是未知模型的 fallback。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `max_tokens` | `int` | `272000` | 未声明模型窗口时的 fallback context window |
| `reserved_tokens` | `int` | `10000` | 未声明模型窗口时为模型响应保留的 token 数 |
| `max_chars` | `int\|null` | `null` | 字符数预算覆盖（兼容旧逻辑，优先使用 `max_tokens`） |
| `reserved_chars` | `int` | `0` | 使用 `max_chars` 时的字符保留数 |
| `summary_max_chars` | `int` | `2000` | 历史消息摘要的最大字符数 |
| `reasoning_policy` | `str` | `"budget"` | reasoning chain 处理方式：`strip`（丢弃）、`append`（始终包含）、`budget`（预算内包含） |
| `max_reasoning_tokens` | `int` | `2000` | reasoning chain 内容的 token 预算 |

---

## Scheduler

在 `scheduler` 键下配置。控制基于 cron 的定时任务调度服务。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `poll_interval_seconds` | `float` | `1.0` | 调度器检查到期任务的间隔（秒） |
| `max_concurrent_fires` | `int` | `5` | 最大并发执行任务数 |
| `job_timeout_seconds` | `float` | `120.0` | 单个定时任务执行超时（秒） |
| `min_interval_seconds` | `int` | `60` | 允许的最小 cron 间隔（防止过于频繁触发） |
| `max_prompt_chars` | `int` | `12000` | 定时任务 prompt 最大字符数 |
| `max_jobs_per_chat` | `int` | `20` | 每个聊天会话的最大定时任务数 |
| `failure_retry_seconds` | `int` | `300` | 任务失败后重试等待时间（秒） |
| `max_consecutive_failures` | `int` | `3` | 连续失败多少次后自动禁用任务 |
| `memory_dreaming_enabled` | `bool` | `true` | 是否启用内部记忆 dreaming 周期任务 |
| `memory_dreaming_interval_seconds` | `int` | `3600` | 记忆 dreaming 周期（秒） |
| `memory_dreaming_initial_delay_seconds` | `int` | `300` | 应用启动后首次 dreaming 延迟（秒） |
| `memory_dreaming_session_limit` | `int` | `20` | 单次 dreaming 最多扫描的最近会话数 |
| `memory_dreaming_recent_turn_limit` | `int` | `40` | 单个会话最多读取的最近 turns 数 |
| `memory_dreaming_provider_id` | `str` | `""` | Legacy 字段；建议把 provider 写进 `memory_dreaming_model` |
| `memory_dreaming_model` | `str` | `""` | dreaming 模型 spec；空则默认找 `memory` tag，失败后使用会话模型 |

---

## Memory

在 `memory` 键下配置长期记忆检索和 embedding。默认保持 FTS-only，不会调用 embedding API。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用 memory 子系统配置 |
| `retrieval.fts_enabled` | `bool` | `true` | 是否允许使用 SQLite FTS/BM25 检索长期记忆 |
| `retrieval.vector_enabled` | `bool` | `false` | 是否启用向量召回；需要 `embedding.enabled=true` |
| `retrieval.hybrid_enabled` | `bool` | `true` | FTS 和 vector 同时可用时是否使用 RRF hybrid fusion |
| `retrieval.vector_backend` | `str` | `"json"` | 向量后端：`json`、`sqlite-vec`、`none` |
| `retrieval.max_injected_items` | `int` | `5` | 单轮最多注入的长期记忆条数 |
| `retrieval.max_injected_chars` | `int` | `4000` | 单轮长期记忆注入字符预算 |
| `embedding.enabled` | `bool` | `false` | 是否启用长期记忆 embedding |
| `embedding.provider_id` | `str` | `""` | Legacy 字段；建议把 provider 写进 `embedding.model` |
| `embedding.model` | `str` | `""` | embedding 模型 spec；空则默认找 `embedding` tag |
| `embedding.dimensions` | `int` | `0` | embedding 维度；`sqlite-vec` 后端必须填写 |
| `embedding.batch_size` | `int` | `16` | embedding 批量大小 |
| `embedding.embed_after_consolidation` | `bool` | `true` | consolidation/dreaming 写入长期记忆后是否刷新 embedding |
| `consolidation.rule_based_enabled` | `bool` | `true` | 是否启用每轮对话结束后的规则抽取；设为 `false` 后只保留后台 dreaming 和显式 `memory_write`/`/memory remember` 写入 |

---

## Router

在 `router` 键下配置。控制消息从频道到命令/Agent 的路由行为。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `system_prompt` | `str` | `"You are a helpful assistant."` | Agent 系统提示词（建议使用顶层 `system_prompt` 字段） |
| `max_history_turns` | `int` | `200` | 每个会话加载的最大对话历史轮数 |
| `agent_enabled` | `bool` | `true` | 是否启用 Agent 循环（设为 `false` 进入纯命令模式） |
| `command_timeout_seconds` | `float` | `30.0` | 命令处理器执行超时（秒） |
| `command_timeout_message` | `str` | `"Command timed out..."` | 命令超时时显示的消息 |
| `reply_to_inbound` | `bool` | `true` | 默认是否让回复引用触发消息；频道插件可用同名配置覆盖 |
| `show_reasoning` | `bool` | `false` | 是否在回复中附带模型推理过程 |
| `reasoning_max_chars` | `int` | `2000` | 推理过程显示的最大字符数 |
| `enable_silent_reply` | `bool` | `true` | 是否允许 Agent 使用 `NO_REPLY` 静默回复（运行时可通过 `/reasoning` 调整） |
| `group_context` | `object` | （见下文） | 群聊观察上下文注入配置 |

### 群聊观察上下文

在 `router.group_context` 下配置。控制群聊中未直接触发 bot 的消息作为上下文注入的行为。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用群聊观察上下文注入 |
| `max_messages` | `int` | `20` | 注入的最大观察消息数 |
| `ttl_seconds` | `int` | `900` | 观察消息过期时间（秒），超出的不注入 |
| `max_chars` | `int` | `4000` | 观察上下文总字符预算 |

---

## WebAPI

在 `webapi` 键下配置。控制 HTTP REST API 服务。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 WebAPI 服务 |
| `auth_token` | `str` | `""` | API 认证令牌；请求需在 `Authorization: Bearer <token>` 中携带 |
| `cors_origins` | `list[str]` | `["*"]` | CORS 允许的来源列表 |
| `host` | `str` | `""` | 绑定地址；空值跟随顶层 `host` |
| `port` | `int` | `0` | 绑定端口；`0` 表示自动分配 |

---

## WebUI

在 `webui` 键下配置。控制浏览器端管理控制台。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用 WebUI（需构建前端资源） |
| `auth` | `object` | （见下文） | 认证配置 |

### WebUI 认证

在 `webui.auth` 下配置。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用登录认证 |
| `admin_password` | `str` | `""` | 明文管理员密码；与 `admin_password_hash` 二选一 |
| `admin_password_hash` | `str` | `""` | bcrypt 哈希密码；优先于明文密码 |
| `session_ttl_seconds` | `int` | `3600` | 登录会话有效期（秒） |
| `login_rate_per_minute` | `int` | `5` | 每分钟最大登录尝试次数 |
| `bind_session_to_ip` | `bool` | `true` | 是否将 session 绑定到客户端 IP |

---

## 频道插件

频道配置通过 `extra="allow"` 机制注入：顶层键名如果匹配某个插件 ID，对应的值会合并到该插件的配置中。

### Telegram

在 `telegram` 键下配置。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `bot_token` | `str` | `""` | Telegram Bot API token（必填），可回退到 `TELEGRAM_BOT_TOKEN` 环境变量 |
| `proxy` | `str` | `""` | SOCKS5/HTTP 代理地址，如 `socks5://127.0.0.1:1080` |
| `polling_timeout` | `int` | `30` | Long polling 超时（秒） |
| `polling_max_backoff` | `float` | `30` | 轮询错误时的最大退避延迟 |
| `allowed_chats` | `list[str]` | `[]` | 聊天 ID 白名单，空 = 接受所有 |
| `reply_to_inbound` | `bool \| null` | `null` | 是否覆盖 `router.reply_to_inbound`；`null`/省略表示跟随全局 |
| `send_retry_attempts` | `int` | `3` | 发送限流时的重试次数 |
| `media_download_dir` | `str` | `"./data/temp/media"` | 媒体文件下载目录 |

### 示例

```yaml
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  polling_timeout: 30
  allowed_chats: []
```

### Milky (QQ)

在 `milky` 键下配置。需要先启动 Lagrange.Milky 实例。

#### 连接

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `base_url` | `str` | `"http://127.0.0.1:3000"` | Milky HTTP 基础 URL |
| `access_token` | `str` | `""` | Milky API 访问令牌 |
| `api_prefix` | `str` | `"/api"` | HTTP API 前缀 |
| `event_path` | `str` | `"/event"` | WebSocket 事件路径 |
| `ws_url` | `str` | `""` | 完整的 WebSocket URL 覆盖（如 `ws://host:3000/event`） |

#### 触发 / 访问控制

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `command_prefix` | `str` | `"/"` | 命令前缀 |
| `group_trigger_mode` | `str` | `"mention"` | 群消息进入 Router/Agent 的方式：`none`（不触发）、`mention`（必须 @ 机器人，命令也需 @）、`command`（命令前缀或 @ 机器人）、`always`（全部消息） |
| `reply_to_inbound` | `bool \| null` | `null` | 是否覆盖 `router.reply_to_inbound`；`null`/省略表示跟随全局 |
| `allowed_friends` | `list[str]` | `[]` | QQ 好友白名单，空 = 不限制 |
| `allowed_groups` | `list[str]` | `[]` | QQ 群白名单，空 = 不限制 |

#### 超时 / 重连

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `connect_timeout` | `float` | `10.0` | HTTP 连接超时（秒） |
| `heartbeat_timeout` | `float` | `30.0` | WebSocket 心跳超时（秒） |
| `reconnect_initial_delay` | `float` | `1.0` | 初始重连延迟（秒） |
| `reconnect_max_delay` | `float` | `30.0` | 最大重连延迟（秒） |

#### 发送 / 媒体 / 转发

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `send_retry_attempts` | `int` | `3` | 发送消息的重试次数 |
| `send_retry_backoff` | `float` | `1.0` | 重试退避间隔（秒） |
| `max_text_length` | `int` | `4000` | 出站文本最大长度 |
| `media_download_dir` | `str` | `"./data/temp/media"` | 媒体文件下载目录 |
| `enable_media_download_tool` | `bool` | `true` | 是否注册媒体下载工具 |
| `resource_url_ttl_hint` | `int` | `300` | 临时 URL 的 TTL 提示（秒） |
| `cache_media_on_receive` | `bool` | `true` | 收到消息时立即缓存媒体 |
| `max_forward_depth` | `int` | `3` | 合并转发最大嵌套深度 |
| `max_forward_messages` | `int` | `80` | 单次合并转发最大消息数 |
| `forward_render_max_chars` | `int` | `12000` | 转发渲染的文本预算（字符） |
| `scene_cache_size` | `int` | `4096` | Peer-to-scene 缓存条目数 |

### 示例

```yaml
milky:
  base_url: "http://127.0.0.1:3000"
  access_token: "${MILKY_ACCESS_TOKEN}"
  group_trigger_mode: "mention"
  allowed_friends: []
  allowed_groups: []
```

---

## 环境变量

所有 YAML 值均支持插值：

- `${VAR_NAME}` — 从 `.env` 文件或环境变量解析
- `${VAR_NAME:fallback}` — 解析并带回退值

配置中常用的环境变量：

| 变量 | 使用者 | 说明 |
|------|--------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram 频道 | Bot API token |
| `DEEPSEEK_LLM_API_KEY` | DeepSeek provider | API 密钥 |
| `DEEPSEEK_LLM_BASE_URL` | DeepSeek provider | API 基础 URL |
| `SILICONFLOW_LLM_API_KEY` | SiliconFlow provider | API 密钥 |
| `SILICONFLOW_LLM_BASE_URL` | SiliconFlow provider | API 基础 URL |
| `MINIMAX_LLM_API_KEY` | Minimax provider | API 密钥 |
| `OPENAI_API_KEY` | OpenAI Responses provider | API 密钥 |
| `OPENAI_API_BASE_URL` | OpenAI Responses provider | API 基础 URL |
| `IMAGE_API_KEY` | image_generation 插件 | 图片生成 API 密钥 |
| `IMAGE_BASE_URL` | image_generation 插件 | 图片生成 API 基础 URL |
| `GITHUB_WEBHOOK_SECRET` | GitHub notifier 插件 | GitHub webhook 签名密钥 |
| `GITHUB_TOKEN` | GitHub notifier 插件 | GitHub API token |
| `MILKY_ACCESS_TOKEN` | Milky 频道 | 访问令牌 |
| `ENV_PATH` | 配置加载器 | 覆盖 `.env` 文件路径 |

变量通常存放在项目根目录的 `.env` 文件中。

---

## 完整示例

项目根目录的 [`config.yaml`](../../config.yaml) 已是多 provider 的完整配置模板。

```yaml
app_name: "Nahida Bot"
debug: false
log_level: "INFO"
log_file: "./data/logs/nahida.log"
log_file_level: "DEBUG"
db_path: "./data/nahida.db"
system_prompt: "You are a helpful assistant."
default_provider: deepseek-main
enable_silent_reply: true

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
        tags: [cheap]

  siliconflow:
    type: "openai-compatible"
    api_key: "${SILICONFLOW_LLM_API_KEY:}"
    base_url: "${SILICONFLOW_LLM_BASE_URL:https://api.siliconflow.cn/v1}"
    merge_system_messages: true
    stream_responses: true
    models:
      - name: "Qwen/Qwen3-Embedding-8B"
        tags: [embedding]
      - name: "Qwen/Qwen3.6-35B-A3B"
        tags: [primary, vision]
        capabilities:
          image_input: true
          max_image_count: 4

multimodal:
  image_fallback_mode: "auto"
  media_context_policy: "cache_aware"
  image_fallback_model: "siliconflow/Qwen/Qwen3.6-35B-A3B"

router:
  max_history_turns: 200
  show_reasoning: false
  reasoning_max_chars: 2000
  group_context:
    enabled: true
    max_messages: 20
    ttl_seconds: 900

webapi:
  enabled: true
  auth_token: "${WEBAPI_AUTH_TOKEN}"
  cors_origins: ["*"]

webui:
  enabled: true
  auth:
    enabled: true
    admin_password: "${WEBUI_ADMIN_PASSWORD}"

memory:
  enabled: true
  retrieval:
    fts_enabled: true
  embedding:
    enabled: true
    model: "embedding"
  consolidation:
    rule_based_enabled: true

scheduler:
  memory_dreaming_enabled: true
  memory_dreaming_model: "memory"
```
