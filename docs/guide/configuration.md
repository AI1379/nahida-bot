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
| `motion_planner` | `object` | （见下文） | Desktop DisplayPlan 服务端动作规划 |
| `processes` | `object` | （见下文） | 附属进程监管（sidecar 进程） |
| `enable_silent_reply` | `bool` | `true` | 全局开关：是否允许 Agent 以 `NO_REPLY` 静默回复 |

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
| `context_1m` | `bool` | `false` | 为 Anthropic 原生 provider 启用 1M 上下文预算。现代 Claude 4.6+ 模型不需要 `anthropic-beta` 头；仅在上游账号和模型支持 1M context 时有效 |
| `anthropic_beta_headers` | `list[str]\|str` | `[]` | 显式发送 `anthropic-beta` header。官方 1M Claude 4.6+ 不需要；部分 Anthropic-compatible 转发层可能仍用旧 beta 名作为路由开关 |

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
| `codex` | `CodexProvider` | ChatGPT Codex（订阅额度，OAuth 设备码登录） |

`type: codex` 不需要 `api_key`：先运行 `nahida-bot auth login codex` 完成
OAuth 设备码登录，refresh token 写入 SQLite，bot 启动时自动加载并按需刷新。
非官方用法，仅面向 ChatGPT Plus/Pro 订阅者；账号存在被 OpenAI 风控的风险。

```yaml
codex:
  type: codex
  stream_responses: true
  reasoning_effort: "medium"
  models:
    - name: "gpt-5.5"
```

高级覆盖项（可选环境变量）：

| 变量 | 说明 |
|------|------|
| `NAHIDA_OPENAI_ORIGINATOR` | OAuth 归因字符串，默认 `"nahida-bot"` |
| `NAHIDA_OPENAI_CLIENT_ID` | OAuth client id，默认内置；仅作为逃生阀 |

### Provider 配额查询

provider 条目可以声明 `quota`，用于查询 provider 侧的余额 / 订阅额度，配合
聊天命令 `/quota [provider_id] [refresh|force|all]` 使用：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `adapter` | `str` | `""` | 配额适配器：`deepseek`、`minimax-coding-plan`、`kimi`、`zhipu`、`json-v1` 等 |
| `url` | `str` | `""` | 配额查询 API 地址（`json-v1` 需要） |
| `api_key` | `str` | `""` | 配额 API 密钥（敏感字段，会被脱敏） |
| `team` | `bool` | `false` | 是否查询团队额度 |
| `organization_id` | `str` | `""` | 组织 ID |
| `project_id` | `str` | `""` | 项目 ID |
| `windows` | `list` | `[]` | `json-v1` 的窗口定义（used/limit/reset JSON 路径） |

```yaml
minimax:
  type: minimax
  api_key: "${MINIMAX_LLM_API_KEY:}"
  base_url: "https://api.minimaxi.com/anthropic"
  quota:
    adapter: minimax-coding-plan
```

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

## Motion Planner（Desktop DisplayPlan）

在 `motion_planner` 键下配置服务端动作规划。启用后，Agent 回复会调用一个
低成本 LLM 模型分析回复文本，为每一句生成情绪 / 动作 / 语音风格标签，
结果挂在 `OutboundMessage.extra["display_plan"]` 上，并通过
`agent.message.completed` 事件转发给 Desktop Node。任何失败都返回中性计划，
不会阻塞回复。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用服务端 motion planner |
| `model_tag` | `str` | `"cheap"` | 通过 ModelRouter 解析的模型标签 |
| `timeout_seconds` | `float` | `15.0` | 单次规划调用的超时（秒），范围 1–60 |

```yaml
motion_planner:
  enabled: true
  model_tag: "cheap"
  timeout_seconds: 15.0
```

仅在回复路由为 Desktop node 路径（`node:...`）时生效；普通聊天频道不受影响。
生成的情绪 / 动作枚举见 [Live2D 动作智能层](../design/live2d-motion-intelligence.md)。

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
| `memory_dreaming_model` | `str` | `""` | dreaming 模型 spec；空则默认找 `memory` tag，失败后使用会话模型 |

---

## Processes（附属进程监管）

在 `processes` 键下配置 sidecar 进程监管。被监管的进程在所有 Channel /
插件启用**之前**就绪、在它们**之后**收尾，适合放需要先于 Channel 建立的
SSH 隧道、frpc、cloudflared 等。进程默认**不继承** bot 进程环境，只有
`PATH`、`LANG` 等少量白名单（Windows 额外含 `SYSTEMROOT`、`WINDIR`、
`APPDATA`、`USERPROFILE`）加你声明的 `env` 会传入；密钥请放 `.env` 用
`${VAR}` 引用。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `true` | 总开关；`false` 则不拉起任何进程 |
| `defaults` | `object` | （见下文） | 所有 spec 的默认值，可被单项覆盖 |
| `specs` | `dict` | `{}` | 进程声明，键名为 `[a-z0-9_-]+`，跨配置与插件唯一 |

### `processes.defaults`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `restart_policy` | `str` | `"on-failure"` | `no` / `on-failure` / `always` |
| `backoff_initial_seconds` | `float` | `1.0` | 崩溃后首次重启等待（秒） |
| `backoff_max_seconds` | `float` | `60.0` | 指数退避上限 |
| `backoff_factor` | `float` | `2.0` | 退避倍率 |
| `restart_max_attempts` | `int` | `0` | `0` = 不限；`>0` 触发熔断（停止自动重启） |
| `restart_window_seconds` | `float` | `300.0` | 统计重启次数的滑动窗口 |
| `shutdown_timeout_seconds` | `float` | `10.0` | SIGTERM 后多久 SIGKILL |
| `startup_wait_seconds` | `float` | `0.0` | 启动后等待健康检查的宽限期 |
| `log_buffer_lines` | `int` | `1000` | stdout/stderr 各自的环形缓冲行数 |

### `processes.specs.<name>`

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `command` | `str` | （必填） | 启动命令 |
| `args` | `list[str]` | `[]` | `shell: false` 时的 argv 列表 |
| `shell` | `bool` | `true` | `true` 走 shell；`false` 把 `command` 当可执行文件、`args` 当参数 |
| `env` | `dict[str,str]` | `{}` | 额外环境变量 |
| `working_dir` | `str\|null` | `null` | 工作目录 |
| `restart_policy` | `str\|null` | `null` | 覆盖 `defaults.restart_policy` |
| `depends_on` | `list[str]` | `[]` | 同段内其他 spec 名，决定启动顺序（循环会被校验拒绝） |
| `shutdown_timeout_seconds` | `float\|null` | `null` | 覆盖默认收尾超时 |
| `startup_wait_seconds` | `float\|null` | `null` | 覆盖默认健康检查宽限 |
| `health_check` | `object` | （见下文） | 健康探测 |

`health_check` 字段：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `type` | `str` | `"none"` | `tcp_port`（TCP 连上即健康）/ `none` |
| `host` | `str` | `"127.0.0.1"` | 探测地址 |
| `port` | `int` | `0` | 探测端口 |
| `interval_seconds` | `float` | `15.0` | 探测间隔 |
| `timeout_seconds` | `float` | `3.0` | 单次探测超时 |
| `unhealthy_after` | `int` | `3` | 连续失败 N 次才判不健康并触发重启 |
| `start_period_seconds` | `float` | `0.0` | 启动宽限期内失败不计入 |

```yaml
processes:
  enabled: true
  defaults:
    restart_policy: "on-failure"
    backoff_initial_seconds: 1.0
    backoff_max_seconds: 60.0
    backoff_factor: 2.0
    restart_max_attempts: 0
    shutdown_timeout_seconds: 10.0
    log_buffer_lines: 1000
  specs:
    ssh-db-tunnel:
      command: "ssh -N -L 3306:db.internal:3306 bastion@example.com"
      shell: true
      env:
        SSH_KEY_PATH: "${SSH_KEY_PATH}"
      restart_policy: "always"
      health_check:
        type: "tcp_port"
        host: "127.0.0.1"
        port: 3306
        interval_seconds: 15.0
        timeout_seconds: 3.0
        unhealthy_after: 3
        start_period_seconds: 0.0
```

完整的监管模型、生命周期顺序、事件与安全边界见
[附属进程监管设计](../design/process-supervisor.md)。WebUI「进程」页与
`/api/processes*` API 可查看状态和日志、启停重启进程。

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
| `topic_gap_seconds` | `int` | `300` | 群消息静默超过此值时开始新的自动上下文话题窗口。0 关闭 |
| `continuity_gap_seconds` | `int` | `1800` | 对话连续性判定（秒）：当前触发与上一次对话 turn 的时间间隔超过此值时，视为新对话并丢弃旧 history，仅保留 observed 窗口。0 关闭（保留旧的纯按条数截断行为）。仅群聊生效 |

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

### Desktop / Gateway 语音合成

`webapi.speech` 启用统一的 Gateway TTS 与音频缓存接口。Desktop 只调用
`POST /api/speech/jobs`，不会直接持有云厂商密钥或依赖具体 Provider 协议。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 Gateway TTS |
| `default_backend` | `str` | `"default"` | 默认后端名称 |
| `backends` | `dict` | `{}` | Provider 后端配置；每项必须包含 `type` |
| `voices` | `dict` | `{}` | 逻辑音色配置；可通过 `backend` 指定后端 |
| `default_voice` | `str` | `""` | 未显式指定音色时使用的逻辑音色 |
| `artifact_cache_dir` | `str` | `"./data/speech_cache"` | 合成音频缓存目录 |
| `artifact_ttl_seconds` | `int` | `21600` | 音频缓存有效时间 |
| `artifact_max_bytes` | `int` | `268435456` | 音频缓存总字节上限（256 MiB），超过后按 LRU 淘汰 |
| `max_text_length` | `int` | `500` | 单次合成文本长度上限 |
| `max_concurrency` | `int` | `1` | 最大并发合成数 |

MiniMax 使用 `type: minimax-t2a-v2`。`voice_id` 可以是系统音色、已激活的
克隆音色或设计音色。Provider 使用同步非流式 `/v1/t2a_v2` 接口，并支持
`mp3`、`wav`、`flac` 和 `pcm` 输出。

```yaml
webapi:
  enabled: true
  auth_token: "${WEBAPI_AUTH_TOKEN}"
  speech:
    enabled: true
    default_backend: minimax
    backends:
      minimax:
        type: minimax-t2a-v2
        api_key: "${MINIMAX_LLM_API_KEY}"
        base_url: "https://api.minimaxi.com"
        model: speech-2.8-hd
        audio_format: mp3
        sample_rate: 32000
        bitrate: 128000
        channel: 1
        language_boost: Chinese
        timeout_seconds: 60
    voices:
      nahida:
        backend: minimax
        voice_id: "your-activated-minimax-voice-id"
        speed: 1.0
        volume: 1.0
        pitch: 0
        emotion: calm  # 可选；请求中的受支持 style 会覆盖它
    default_voice: nahida
    max_text_length: 500
    max_concurrency: 2
```

MiniMax 后端还支持 `tts_path`、`trust_env`、`force_close_connections`、
`aigc_watermark` 和 `extra_body`。`SpeechRequest.speed`、`pitch`、受支持的
情绪 `style` 以及 `output_format` 会按请求覆盖对应默认值。API Key 应通过
环境变量注入，不要写入 YAML。

### Desktop / Gateway Node 协议

`webapi.nodes` 控制 Gateway 与 Desktop App 等 Node 之间的 WebSocket 协议层
（心跳、配对与 node token）。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用 Node 协议层 |
| `heartbeat_interval_ms` | `int` | `15000` | Node 心跳发送间隔（毫秒） |
| `heartbeat_timeout_ms` | `int` | `45000` | 心跳超时（毫秒），超时判定节点离线 |
| `pairing_ttl_seconds` | `int` | `600` | 一次性配对 token 的有效期（秒） |
| `node_token_ttl_seconds` | `int` | `0` | 长期 node token 有效期（秒）；`0` = 永不过期 |

```yaml
webapi:
  enabled: true
  auth_token: "${WEBAPI_AUTH_TOKEN}"
  nodes:
    enabled: true
    heartbeat_interval_ms: 15000
    heartbeat_timeout_ms: 45000
    pairing_ttl_seconds: 600
    node_token_ttl_seconds: 0
```

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
| `admin_password_hash` | `str` | `""` | PBKDF2-SHA256 管理员密码哈希；格式为 `pbkdf2_sha256$迭代次数$salt$digest` |
| `session_ttl_seconds` | `int` | `3600` | 登录会话有效期（秒） |
| `login_rate_per_minute` | `int` | `5` | 每分钟最大登录尝试次数 |
| `bind_session_to_ip` | `bool` | `true` | 是否将 session 绑定到客户端 IP |

可在部署目录交互式生成哈希，密码不会进入 shell 历史：

```bash
nahida-bot webui hash-password
```

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

### Discord

在 `discord` 键下配置。基于 discord.py 网关连接；需先在 [Discord Developer Portal](https://discord.com/developers/applications) 创建 Bot 并**手动开启 Message Content Intent**（Privileged Intent），再执行 `uv sync --group discord`。

会话地址映射：私信 → `discord:private:<dm_channel_id>`，服务器文字频道 → `discord:channel:<channel_id>`，Thread/论坛帖 → `discord:thread:<thread_id>`（每个 Thread 独立会话）。

已注册的 `/` 命令会自动同步为 Discord 原生 slash command（服务器级、即时生效；插件启停后自动重推）。带 `choices`/`completer` 参数元数据的命令（如 `/model`）在 Discord 里获得原生参数自动补全；slash 调用与文本 `/` 调用走同一套 handler。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `bot_token` | `str` | `""` | Discord Bot token（必填），可回退到 `DISCORD_BOT_TOKEN` 环境变量 |
| `proxy` | `str` | `""` | HTTP/SOCKS 代理地址（国内部署通常需要），可回退到 `DISCORD_PROXY` 环境变量 |
| `group_trigger_mode` | `str` | `"mention"` | 服务器频道/Thread 内的触发方式：`none` / `mention`（@bot）/ `command`（命令前缀或 @bot）/ `always`；私信始终响应 |
| `group_context_capture` | `bool` | `false` | `true` 时未触发的服务器消息记录为观察上下文 |
| `reply_to_inbound` | `bool \| null` | `null` | 是否覆盖 `router.reply_to_inbound`；`null`/省略表示跟随全局 |
| `allowed_guilds` | `list[str]` | `[]` | 服务器（guild）ID 白名单，空 = 不限制；私信不受此限制 |
| `allowed_dm_users` | `list[str]` | `[]` | 私信用户 ID 白名单，空 = 不限制 |
| `blocked_channels` | `list[str]` | `[]` | 在允许的服务器内排除特定频道/Thread |
| `register_slash_commands` | `bool` | `true` | 将已注册命令同步为 Discord 原生 slash command |
| `message_max_length` | `int` | `2000` | 出站消息拆分长度上限（Discord 硬限制 2000） |
| `send_retry_attempts` | `int` | `3` | 发送限流时的重试次数 |
| `media_download_dir` | `str` | `"./data/temp/media"` | 媒体文件下载目录 |

### 示例

```yaml
discord:
  bot_token: "${DISCORD_BOT_TOKEN}"
  proxy: "${DISCORD_PROXY:}"
  group_trigger_mode: "mention"
  allowed_guilds: []
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
| `pending_file_ttl_seconds` | `float` | `600.0` | 待投递文件队列的 TTL（秒）；纯文件消息不会单独触发 Agent，文件会排队等下一个触发消息一并注入 |
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

### OneBot (v11)

在 `onebot` 键下配置。目前仅支持 OneBot v11 正向 WebSocket 模式
（`protocol_version: "v12"` 与 WebHook 模式未实现）。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `protocol_version` | `str` | `"v11"` | 仅支持 `v11`；`v12` 未实现 |
| `ws_url` | `str` | `""` | 正向 WS 地址（必填），如 `ws://127.0.0.1:6700` |
| `ws_access_token` | `str` | `""` | WS 鉴权 token |
| `command_prefix` | `str` | `"/"` | 命令前缀 |
| `group_trigger_mode` | `str` | `"mention"` | 群消息触发方式：`none` / `mention` / `command` / `always` |
| `group_context_capture` | `bool` | `false` | 是否捕获群聊观察上下文 |
| `reply_to_inbound` | `bool \| null` | `null` | 覆盖 `router.reply_to_inbound` |
| `allowed_friends` | `list[str]` | `[]` | 好友白名单，空 = 不限制 |
| `allowed_groups` | `list[str]` | `[]` | 群白名单，空 = 不限制 |
| `reconnect_initial_delay` | `float` | `1.0` | 初始重连延迟（秒） |
| `reconnect_max_delay` | `float` | `30.0` | 最大重连延迟（秒） |
| `max_text_length` | `int` | `4000` | 出站文本最大长度 |
| `split_long_text` | `bool` | `true` | 超长文本自动分段发送 |
| `max_forward_depth` | `int` | `3` | 合并转发最大嵌套深度（0–10） |
| `max_forward_messages` | `int` | `80` | 单次合并转发最大消息数 |
| `forward_render_max_chars` | `int` | `12000` | 合并转发渲染的文本预算 |
| `media_download_dir` | `str` | `"./data/temp/onebot"` | 媒体文件下载目录 |
| `enable_media_download_tool` | `bool` | `true` | 是否注册媒体下载工具 |
| `cache_media_on_receive` | `bool` | `true` | 收到消息时立即缓存媒体 |

合并转发在收到后会被**解析**而非占位：通过 `get_forward_msg` 递归拉取，
按 `- {sender_name}: {content}` 渲染，超过 `max_forward_depth` 时截断为
`[Forward: {id}, messages={n}, truncated=true]`，超过
`forward_render_max_chars` 时以 `[Truncated]` 标记截断；转发内部的图片、
语音、视频、文件会提取为一等 `InboundAttachment`。

### 示例

```yaml
onebot:
  ws_url: "ws://127.0.0.1:6700"
  ws_access_token: "${ONEBOT_ACCESS_TOKEN}"
  group_trigger_mode: "mention"
  allowed_friends: []
  allowed_groups: []
```

---

## 插件配置

以下为常见内置插件的顶层配置块（同样通过 `extra="allow"` 机制注入）。

### image_generation（图片生成）

顶层 `image_generation:` 启用 `/draw`、`/生图` 命令和 `image_generate`
工具。`backends` 是后端字典，`provider` 选择默认使用的后端名。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `provider` | `str` | `"default"` | 默认后端名 |
| `backends` | `dict` | （见下文） | 后端配置 |
| `output_dir` | `str` | `"generated/images"` | 输出目录（相对 workspace，不能越界） |
| `auto_send` | `bool` | `true` | 生成后自动发送 |
| `command_names` | `list[str]` | `["draw", "生图"]` | 注册的命令名 |
| `caption_template` | `str` | `""` | 图片说明模板 |
| `max_images_per_24h` | `int` | `0` | 每 24h 生成上限，`0` = 不限 |

#### `openai-images`（OpenAI 兼容 Images API）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `type` | `str` | `"openai-images"` | 后端类型 |
| `base_url` | `str` | `"https://api.openai.com/v1"` | API 端点 |
| `api_key` | `str` | `""` | API 密钥 |
| `model` | `str` | `"gpt-image-1"` | 模型名 |
| `size` | `str` | `"1024x1024"` | 图片尺寸 |
| `quality` | `str` | `"auto"` | 质量 |
| `timeout_seconds` | `float` | `120.0` | 生成超时 |
| `download_timeout_seconds` | `float` | `60.0` | 下载超时 |
| `max_concurrency` | `int` | `1` | 最大并发（1–16） |
| `max_images_per_request` | `int` | `1` | 单请求最多图片（1–10） |

#### `minimax`（MiniMax 图片生成）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `type` | `str` | `"minimax"` | 后端类型 |
| `base_url` | `str` | `"https://api.minimaxi.com"` | API 端点（`/v1/image_generation`） |
| `api_key` | `str` | `""` | API 密钥 |
| `model` | `str` | `"image-01"` | 模型名 |
| `aspect_ratio` | `str` | `"1:1"` | 比例：`1:1`、`16:9`、`4:3`、`3:2`、`2:3`、`3:4`、`9:16`、`21:9` |
| `width` / `height` | `int` | `0` | 尺寸；`0` 时用 `aspect_ratio`（`image-01` 需为 8 的倍数） |
| `style_type` | `str` | `""` | 风格（`image-01-live`：漫画/元气/中世纪/水彩） |
| `style_weight` | `float` | `0.8` | 风格强度（0.01–1.0） |
| `response_format` | `str` | `"url"` | `url` 或 `b64_json`（映射为 `base64`） |
| `seed` | `int\|null` | `null` | 随机种子 |
| `prompt_optimizer` | `bool` | `false` | 提示词优化 |
| `aigc_watermark` | `bool` | `false` | AIGC 水印 |
| `max_images_per_request` | `int` | `1` | 单请求最多图片（1–9） |

#### `codex-images`（ChatGPT Codex 订阅）

复用 `type: codex` LLM provider 的 OAuth token 调用
`chatgpt.com/backend-api/codex/images/generations`，走 Plus/Pro 订阅额度。
前提是先运行 `nahida-bot auth login codex`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `type` | `str` | `"codex-images"` | 后端类型 |
| `provider_id` | `str` | `"codex"` | 必须匹配 `providers` 中 `type: codex` 的 key |
| `base_url` | `str` | `"https://chatgpt.com/backend-api/codex"` | API 端点 |
| `model` | `str` | `"gpt-image-2"` | 模型名 |
| `size` / `quality` / `background` | `str` | `"auto"` | 生成参数 |
| `timeout_seconds` | `float` | `180.0` | 生成超时 |
| `max_images_per_request` | `int` | `1` | 单请求最多图片（1–10） |

```yaml
image_generation:
  enabled: true
  provider: codex
  backends:
    codex:
      type: codex-images
      provider_id: codex
      model: "gpt-image-2"
      size: "auto"
      quality: "auto"
      background: "auto"
      timeout_seconds: 180
  output_dir: "generated/images"
  auto_send: true
  max_images_per_24h: 20
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
| `DISCORD_BOT_TOKEN` | Discord 频道 | Bot token |
| `DISCORD_PROXY` | Discord 频道 | HTTP/SOCKS 代理地址 |
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
| `ONEBOT_ACCESS_TOKEN` | OneBot 频道 | WS 访问令牌 |
| `NAHIDA_CONFIG` | 配置加载器 | 覆盖 `config.yaml` 路径 |
| `ENV_PATH` | 配置加载器 | 覆盖 `.env` 文件路径 |
| `NAHIDA_OPENAI_ORIGINATOR` | Codex provider | OAuth 归因字符串，默认 `"nahida-bot"` |
| `NAHIDA_OPENAI_CLIENT_ID` | Codex provider | OAuth client id（逃生阀） |
| `NAHIDA_BOOTSTRAP_PROVIDER` | bootstrap | 非交互模式要配置的 provider 类型 |
| `NAHIDA_BOOTSTRAP_PROVIDER_ID` | bootstrap | 非交互模式 provider ID，默认 `main` |
| `NAHIDA_BOOTSTRAP_CHANNELS` | bootstrap | 非交互模式要接入的 channel 列表（逗号分隔） |

变量通常存放在项目根目录的 `.env` 文件中。CLI 命令（`start`、`doctor`、
`config` 等）会自动发现 `./config.yaml` 与 `./.env`，解析顺序见
[快速开始](./getting-started.md#配置文件自动发现)。也可用 `nahida-bot bootstrap`
交互式生成最小配置。

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
    topic_gap_seconds: 300
    continuity_gap_seconds: 1800

webapi:
  enabled: true
  auth_token: "${WEBAPI_AUTH_TOKEN}"
  cors_origins: ["*"]

webui:
  enabled: true
  auth:
    enabled: true
    admin_password_hash: "${WEBUI_ADMIN_PASSWORD_HASH}"

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

processes:
  enabled: true
  defaults:
    restart_policy: "on-failure"
  specs:
    ssh-db-tunnel:
      command: "ssh -N -L 3306:db.internal:3306 bastion@example.com"
      shell: true
      restart_policy: "always"
      health_check:
        type: "tcp_port"
        host: "127.0.0.1"
        port: 3306

motion_planner:
  enabled: false
  model_tag: "cheap"
  timeout_seconds: 15.0
```

## 身份与管理员授权

开启 `identity` 后，`identity.admins` 中的平台账号可以直接调用特权工具
（`exec` / `message` / `workspace_write` / `identity_manage` 等）。
Person 链接只用于身份和记忆归属，不会自动赋予管理员权限。

```yaml
identity:
  enabled: true
  admins:
    - channel: "milky"
      platform_account_id: "123456789"
```

非管理员调用特权工具会被 `AuthorizationGate` 拒绝（fail-closed）：开启
`identity` 但不声明 admins 时，所有特权调用都会被锁死，避免被静默放行。
