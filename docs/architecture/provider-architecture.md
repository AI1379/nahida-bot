# Provider 多后端架构

> ⚠️ **当前实现风险提示**：现有 `agent/providers/openai_compatible.py` 仅处理标准 OpenAI 响应格式，未考虑不同 LLM 后端的响应差异，特别是推理链（thinking chain）支持。

## A. 各后端响应格式调研

> 以下调研基于各厂商公开 API 文档（截至 2026-04）。所有后端可归为三大格式族：
>
> - **OpenAI 兼容族**：OpenAI、DeepSeek、GLM/智谱、Minimax — 共享 `choices[].message` 扁平结构
> - **Anthropic 族**：Claude — 使用 `content[]` 内容块数组结构，与 OpenAI 格式**根本不兼容**
> - **Google Gemini 族**：Gemini 3 — 原生使用 `candidates[].content.parts[]` 结构，但提供 OpenAI 兼容端点

---

### A.1 OpenAI 标准格式（GPT-5 系列）

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

### A.2 DeepSeek 格式

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

### A.3 Anthropic/Claude 格式

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

### A.4 GLM/智谱 格式

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

### A.5 Minimax 格式

**端点**：`POST /v1/text/chatcompletion_v2`（OpenAI 兼容）

关键特征：

- 基本兼容 OpenAI 标准格式
- API 路径为 `/v1/text/chatcompletion_v2`，非标准 `/v1/chat/completions`
- 官方文档访问受限，无法确认是否有 Minimax 特有的推理/思考链扩展字段
- 按 OpenAI 标准适配器处理即可，遇到额外字段时按需扩展

---

### A.6 Google Gemini 3 格式

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

### A.7 格式族总结

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

## B. 集成式 Provider 架构

> **设计理念**：借鉴 AstrBot 的类继承模式 + OpenClaw 的 Provider 即 Plugin 注册思路。每个 Provider 类**同时负责** HTTP 传输和响应解析，不使用独立的 ResponseAdapter。通过类继承共享 OpenAI 兼容族的通用逻辑，Anthropic 和 Gemini 各自独立实现。

---

### B.1 扩展 `ProviderResponse`

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

### B.2 扩展 `ContextMessage`

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

### B.3 Provider 类层次

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

### B.4 Provider 注册表

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

### B.5 `_ReasoningMixin` — 共享推理提取逻辑

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

### B.6 推理链上下文策略

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

### B.7 工具 Schema 转换

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

### B.8 历史回放（签名/Thinking 块在多轮对话中的回传）

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

## C. 文件布局

```
nahida_bot/agent/providers/
    __init__.py              # ✅ 已更新：新增导出（AnthropicProvider, TokenUsage, ReasoningPolicy, registry 函数等）
    base.py                  # ✅ 已扩展：TokenUsage, ProviderResponse 新字段, api_family, format_tools, serialize_messages
    registry.py              # ✅ 已新增：@register_provider, get_provider_class, create_provider, list_providers
    reasoning.py             # ✅ 已新增：extract_think_tags, _ReasoningMixin（ReasoningPolicy 定义在 context.py 以避免循环导入）
    openai_compatible.py     # ✅ 已演进：继承 _ReasoningMixin，填充新 ProviderResponse 字段，serialize_messages 注入推理
    deepseek.py              # ✅ 已新增：@register_provider("deepseek")，空子类
    glm.py                   # ✅ 已新增：@register_provider("glm")，空子类
    groq.py                  # ✅ 已新增：@register_provider("groq")，reasoning_key 覆盖 + 历史 strip
    minimax.py               # ✅ 已新增：@register_provider("minimax")，空子类
    anthropic.py             # ✅ 已新增：独立 AnthropicProvider（content blocks 解析 + 签名回传 + tool_use）
    gemini.py                # ☐ 未实现：独立 Provider（Phase 3）
    errors.py                # 不变

nahida_bot/agent/
    context.py               # ✅ 已扩展：ReasoningPolicy 枚举, ContextMessage 新字段, ContextBudget 新增 reasoning_policy/max_reasoning_tokens
    loop.py                  # ✅ 已扩展：_build_assistant_message 传播推理/签名字段

tests/
    test_reasoning.py                              # ✅ 14 tests: extract_think_tags, _ReasoningMixin, ReasoningPolicy
    test_provider_registry.py                      # ✅ 7 tests: 注册表、查找、重复检测
    test_provider_reasoning_integration.py         # ✅ 12 tests: DeepSeek reasoning, think-tag fallback, Groq stripping, TokenUsage, refusal
    test_provider_anthropic.py                     # ✅ 14 tests: text/thinking/redacted/tool_use blocks, system prompt, signature replay
    integration/test_provider_multibackend_live.py # ✅ 10 tests: OpenAI/DeepSeek/Anthropic live roundtrips + contract validation
```

---

## D. 实施计划

**Phase 2.8 ✅ — 已完成**：

1. ✅ 扩展 `base.py`：`TokenUsage` dataclass + `ProviderResponse` 新字段 + `ChatProvider` 新方法
2. ✅ 扩展 `context.py`：`ContextMessage` 新字段 + `ContextBudget` 新字段 + `ReasoningPolicy` 枚举
3. ✅ 新建 `reasoning.py`：`extract_think_tags()` + `_ReasoningMixin`
4. ✅ 新建 `registry.py`：`@register_provider` + 工厂方法
5. ✅ 演进 `openai_compatible.py`：继承 `_ReasoningMixin`，填充新字段，`serialize_messages` 处理推理历史
6. ✅ 新建空子类：`deepseek.py`、`glm.py`、`groq.py`、`minimax.py`
7. ✅ 扩展 `loop.py`：`_build_assistant_message` 传播推理/签名字段
8. ✅ 编写完整测试套件
9. ✅ 更新 architecture 文档

**Phase 2.8b ✅ — 已完成**：

1. ✅ 新建 `anthropic.py`：独立 Anthropic Provider（content blocks 解析 + 签名回传 + tool_use）
2. ✅ Anthropic 单元测试（14 tests）
3. ✅ 多后端集成测试（OpenAI/DeepSeek/Anthropic，通过 `.env.test` 配置 API Key）

**Phase 3+**：

1. 流式响应支持
2. `GeminiProvider` 原生端点实现
3. 更多后端适配器
4. 完整插件系统（通过 entry points 发现 Provider 模块）

---

## E. 当前实现状态（Phase 2.8 完成后）

> 本节记录 Phase 2.8/2.8b 完成后的实际代码状态，供后续开发参考。

**设计决策与偏离**：

1. **`ReasoningPolicy` 定义位置**：architecture 文档原设计将其放在 `reasoning.py`，实际放在 `context.py` 以避免循环导入（`context.py` → `providers.reasoning` → `providers.__init__` → `base` → `context.py`）。`reasoning.py` 通过 `from nahida_bot.agent.context import ReasoningPolicy` 重新导出。

2. **`_ReasoningMixin._extract_reasoning_from_message` 返回值**：architecture 文档原设计返回 `str | None`，实际返回 `tuple[str | None, str | None]`（reasoning_content, cleaned_content）。当 think-tag 被提取后，调用方需要使用 cleaned content 替换原始 content。

3. **`GroqProvider.serialize_messages`**：不使用 `super()` 而是显式调用 `OpenAICompatibleProvider.serialize_messages(self, ...)`，因为 `@dataclass(slots=True)` + 多重继承导致 `super()` 的 MRO 解析失败。

4. **Anthropic `serialize_messages` 返回值**：返回 `tuple[str | None, list[dict]]` 而非 `list[dict]`，因为 Anthropic 要求 system prompt 作为独立顶层参数传递。

**Provider 注册表状态**（通过 `list_providers()` 可查）：

| Provider Type | Class | API Family | Key Features |
|---|---|---|---|
| `openai-compatible` | `OpenAICompatibleProvider` | `openai-completions` | Base class, `_ReasoningMixin`, think-tag fallback |
| `deepseek` | `DeepSeekProvider` | `openai-completions` | Empty subclass, inherits `reasoning_key` |
| `glm` | `GLMProvider` | `openai-completions` | Empty subclass |
| `groq` | `GroqProvider` | `openai-completions` | `reasoning_key="reasoning"`, strips reasoning from history |
| `minimax` | `MinimaxProvider` | `openai-completions` | Empty subclass |
| `anthropic` | `AnthropicProvider` | `anthropic-messages` | Independent impl, content blocks, signature passback |

**集成测试环境**：

```
.env.test.example    # 模板文件，包含所有支持的 Provider 环境变量
.env.test            # 实际密钥文件（gitignored），从 .env.test.example 复制
```

测试用 fixtures（`conftest.py`）：

- `live_llm_config` → OpenAI 兼容后端
- `live_deepseek_config` → DeepSeek 后端
- `live_anthropic_config` → Anthropic 后端

---

## F. 流式响应（未来扩展）

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

## G. 测试要求

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
