# Configuration Reference

Nahida Bot reads its configuration from a YAML file, a `.env` file for secrets, and environment variables. Values are merged in this precedence order:

1. CLI flags (`--debug`, `--config-yaml`)
2. `.env` file values
3. YAML config file
4. Built-in defaults

Environment variable interpolation is available in any YAML value via `${VAR}` or `${VAR:fallback}` syntax.

---

## Top-Level Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `app_name` | `str` | `"Nahida Bot"` | Application name, used in logs and lifecycle events |
| `debug` | `bool` | `false` | Debug mode. Forces `log_level` to `DEBUG` unless explicitly set |
| `log_level` | `str` | `"INFO"` | Log level: `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `log_json` | `bool\|null` | `null` | JSON log output. `null` = auto (JSON in production, console in debug) |
| `host` | `str` | `"127.0.0.1"` | Server bind host (reserved) |
| `port` | `int` | `6185` | Server bind port (reserved) |
| `db_path` | `str` | `"./data/nahida.db"` | SQLite database file path |
| `workspace_base_dir` | `str` | `"./data/workspace"` | Workspace storage directory |
| `plugin_paths` | `list[str]` | `["./plugins"]` | Additional plugin directories to scan |
| `discover_builtin_channels` | `bool` | `true` | Auto-discover built-in channel plugins |
| `system_prompt` | `str` | `"You are a helpful assistant."` | Default system prompt for agent conversations |
| `default_provider` | `str` | `""` | Provider ID to use by default. Empty = first provider in `providers` |
| `providers` | `dict` | `{}` | LLM provider configuration (see below) |
| `multimodal` | `object` | (see below) | Image/media handling configuration |

### Example

```yaml
app_name: "Nahida Bot"
debug: false
log_level: "INFO"
db_path: "./data/nahida.db"
system_prompt: "You are a helpful assistant."
default_provider: deepseek-main
```

---

## LLM Providers

The `providers` key is a dictionary. Each key is an arbitrary provider ID used in `default_provider` and `/model` commands.

### Provider Entry

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `type` | `str` | `"openai-compatible"` | Provider type. See [Provider Types](#provider-types) |
| `api_key` | `str` | `""` | API key. Provider is skipped if empty |
| `base_url` | `str` | `""` | API endpoint base URL |
| `models` | `list` | `[]` | Model list. First entry is the default model |
| `merge_system_messages` | `bool` | `false` | Coalesce all system messages into one before sending (for backends that require it) |

### Model Entry

Each item in `models` can be a plain string or an object:

```yaml
models:
  - "deepseek-v4-pro"                      # simple string
  - name: "Qwen/Qwen3.6-35B-A3B"           # extended with capabilities
    capabilities:
      image_input: true
      max_image_count: 4
```

### Model Capabilities

Set per-model under `capabilities` to declare what the model supports:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `text_input` | `bool` | `true` | Accepts text input |
| `image_input` | `bool` | `false` | Accepts images natively |
| `tool_calling` | `bool` | `true` | Supports function/tool calling |
| `reasoning` | `bool` | `false` | Supports reasoning/thinking tokens |
| `prompt_cache` | `bool` | `false` | Supports prompt caching |
| `prompt_cache_images` | `bool` | `false` | Cache images in prompts |
| `explicit_context_cache` | `bool` | `false` | Requires explicit cache control markers |
| `prompt_cache_min_tokens` | `int` | `0` | Minimum tokens for cache breakpoint |
| `max_image_count` | `int` | `0` | Max images per request (0 = unlimited) |
| `max_image_bytes` | `int` | `0` | Max bytes per image (0 = unlimited) |
| `supported_image_mime_types` | `list[str]` | `["image/jpeg", "image/png", "image/webp"]` | Accepted MIME types |
| `image_generation` | `bool` | `false` | Model can generate images via built-in tool |
| `web_search` | `bool` | `false` | Model supports built-in web search |
| `file_search` | `bool` | `false` | Model supports built-in file search |
| `code_interpreter` | `bool` | `false` | Model supports built-in code interpreter |

### OpenAI Responses API Options

These fields apply only when `type: "openai-responses"`:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `store_responses` | `bool` | `false` | Enable response persistence for `previous_response_id` chaining |
| `reasoning_effort` | `str` | `null` | Reasoning depth: `"low"`, `"medium"`, `"high"` |
| `max_output_tokens` | `int` | `null` | Max output tokens (replaces `max_tokens`) |
| `built_in_tools` | `list[str]` | `null` | Built-in tools to enable: `"web_search"`, `"file_search"`, `"image_generation"`, `"code_interpreter"` |

### Provider Types

| Type | Class | Description |
|------|-------|-------------|
| `openai-compatible` | `OpenAICompatibleProvider` | Generic `/chat/completions` endpoint |
| `deepseek` | `DeepSeekProvider` | DeepSeek (extends OpenAI-compatible, adds thinking mode) |
| `glm` | `GLMProvider` | GLM / ZhiPu (fully OpenAI-compatible) |
| `groq` | `GroqProvider` | Groq (OpenAI-compatible, different reasoning key) |
| `anthropic` | `AnthropicProvider` | Anthropic Claude (independent protocol) |
| `minimax` | `MinimaxProvider` | Minimax (Anthropic-compatible endpoint) |
| `openai-responses` | `OpenAIResponsesProvider` | OpenAI Responses API (`/v1/responses`) with built-in tools and stateful chaining |

### Example

```yaml
providers:
  deepseek-main:
    type: deepseek
    api_key: "${DEEPSEEK_LLM_API_KEY}"
    base_url: "${DEEPSEEK_LLM_BASE_URL}"
    models: ["deepseek-v4-pro", "deepseek-v4-flash"]

  siliconflow:
    type: "openai-compatible"
    api_key: "${SILICONFLOW_LLM_API_KEY}"
    base_url: "${SILICONFLOW_LLM_BASE_URL}"
    merge_system_messages: true
    models:
      - "Pro/zai-org/GLM-5"
      - name: "Qwen/Qwen3.6-35B-A3B"
        capabilities:
          image_input: true
          max_image_count: 4
          max_image_bytes: 10485760

  minimax:
    type: minimax
    api_key: "${MINIMAX_LLM_API_KEY}"
    base_url: "https://api.minimaxi.com/anthropic"
    models: ["MiniMax-M2.5"]

  openai:
    type: "openai-responses"
    api_key: "${OPENAI_API_KEY}"
    base_url: "https://api.openai.com"
    store_responses: true
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

## Multimodal / Image Handling

Configured under the `multimodal` key.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `image_fallback_mode` | `str` | `"auto"` | Strategy when the primary model lacks image support: `auto` (call fallback vision model), `tool` (inject `image_understand` tool), `off` (skip images) |
| `media_context_policy` | `str` | `"cache_aware"` | How media is retained in history: `cache_aware` (recent images as native blocks, old ones as descriptions), `native_recent` (only latest image is native), `description_only` (all images as text descriptions) |
| `image_fallback_provider` | `str` | `""` | Provider ID for the fallback vision model |
| `image_fallback_model` | `str` | `""` | Model name within the fallback provider |
| `max_images_per_turn` | `int` | `4` | Maximum images processed per conversation turn |
| `max_image_bytes` | `int` | `10485760` | Maximum bytes per individual image (10 MB) |
| `media_cache_ttl_seconds` | `int` | `3600` | Media cache time-to-live in seconds |

### Example

```yaml
multimodal:
  image_fallback_mode: "auto"
  media_context_policy: "cache_aware"
  image_fallback_provider: "siliconflow"
  image_fallback_model: "Qwen/Qwen3.6-35B-A3B"
```

---

## Channel Plugins

Channel configuration is injected via the `extra="allow"` mechanism: top-level keys that match a plugin ID are merged into that plugin's config.

### Telegram

Under the `telegram` key.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `bot_token` | `str` | `""` | Telegram Bot API token (required). Falls back to `TELEGRAM_BOT_TOKEN` env var |
| `polling_timeout` | `int` | `30` | Long polling timeout in seconds |
| `polling_max_backoff` | `float` | `30` | Max backoff delay on poll errors |
| `allowed_chats` | `list[str]` | `[]` | Chat ID allowlist. Empty = accept all |
| `send_retry_attempts` | `int` | `3` | Retry count for rate-limited sends |
| `media_download_dir` | `str` | `"./data/temp/media"` | Download directory for media files |

### Example

```yaml
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  polling_timeout: 30
  allowed_chats: []
```

### Milky (QQ)

Under the `milky` key. Requires a running Lagrange.Milky instance.

#### Connection

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `base_url` | `str` | `"http://127.0.0.1:3000"` | Milky HTTP base URL |
| `access_token` | `str` | `""` | Milky API access token |
| `api_prefix` | `str` | `"/api"` | HTTP API prefix |
| `event_path` | `str` | `"/event"` | WebSocket event path |
| `ws_url` | `str` | `""` | Full WebSocket URL override (e.g. `ws://host:3000/event`) |

#### Trigger / Access Control

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `command_prefix` | `str` | `"/"` | Command prefix |
| `group_trigger_mode` | `str` | `"mention"` | How group messages trigger the bot: `mention`, `command`, `always` |
| `allowed_friends` | `list[str]` | `[]` | QQ friend allowlist. Empty = no restriction |
| `allowed_groups` | `list[str]` | `[]` | QQ group allowlist. Empty = no restriction |

#### Timeouts / Reconnect

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `connect_timeout` | `float` | `10.0` | HTTP connection timeout (seconds) |
| `heartbeat_timeout` | `float` | `30.0` | WebSocket heartbeat timeout (seconds) |
| `reconnect_initial_delay` | `float` | `1.0` | Initial reconnect delay (seconds) |
| `reconnect_max_delay` | `float` | `30.0` | Maximum reconnect delay (seconds) |

#### Sending / Media / Forward

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `send_retry_attempts` | `int` | `3` | Retry count for sending messages |
| `send_retry_backoff` | `float` | `1.0` | Backoff between retries (seconds) |
| `max_text_length` | `int` | `4000` | Max outbound text length |
| `media_download_dir` | `str` | `"./data/temp/media"` | Media download directory |
| `enable_media_download_tool` | `bool` | `true` | Register the media download tool |
| `resource_url_ttl_hint` | `int` | `300` | TTL hint for temp URLs (seconds) |
| `cache_media_on_receive` | `bool` | `true` | Eagerly cache inbound media |
| `max_forward_depth` | `int` | `3` | Max nested forward depth |
| `max_forward_messages` | `int` | `80` | Max messages per resolved forward |
| `forward_render_max_chars` | `int` | `12000` | Text budget for rendered forwards |
| `scene_cache_size` | `int` | `4096` | Peer-to-scene cache entries |

### Example

```yaml
milky:
  base_url: "http://127.0.0.1:3000"
  access_token: "${MILKY_ACCESS_TOKEN}"
  group_trigger_mode: "mention"
  allowed_friends: []
  allowed_groups: []
```

---

## Environment Variables

Any YAML value supports interpolation:

- `${VAR_NAME}` — resolve from `.env` file or environment
- `${VAR_NAME:fallback}` — resolve with fallback

Common environment variables referenced in config:

| Variable | Used By | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram channel | Bot API token |
| `DEEPSEEK_LLM_API_KEY` | DeepSeek provider | API key |
| `DEEPSEEK_LLM_BASE_URL` | DeepSeek provider | API base URL |
| `SILICONFLOW_LLM_API_KEY` | SiliconFlow provider | API key |
| `SILICONFLOW_LLM_BASE_URL` | SiliconFlow provider | API base URL |
| `MINIMAX_LLM_API_KEY` | Minimax provider | API key |
| `OPENAI_API_KEY` | OpenAI Responses provider | API key |
| `MILKY_ACCESS_TOKEN` | Milky channel | Access token |
| `ENV_PATH` | Config loader | Override `.env` file path |

Variables are typically stored in a `.env` file in the project root.

---

## Full Example

A complete multi-provider configuration is available in [`config-multiproviders.yaml`](../config-multiproviders.yaml) in the project root.
