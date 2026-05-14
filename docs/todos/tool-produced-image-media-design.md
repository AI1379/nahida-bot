# 工具产出图片的多模态读取设计与实现路径

状态：待实现

背景日期：2026-05-14

## 1. 问题背景

当前 Nahida Bot 已经有一套面向“用户消息附件”的多模态图片处理链路：

- 用户消息中的图片会被标准化为 `InboundAttachment`。
- `SessionRunner._build_user_parts()` 根据当前模型的 `ModelCapabilities.image_input` 决定是否直接传图。
- 支持视觉输入的模型会收到 `ContextPart(type="image_base64")` 或 `ContextPart(type="image_url")`。
- 不支持视觉输入的模型会按 `multimodal.image_fallback_mode` 走 `auto`、`tool` 或 `off`。
- `auto` 会调用 fallback vision 模型，把图片转成文字描述。
- `tool` 会暴露 `image_understand` 工具，由主模型按需调用视觉模型分析图片。

这个链路对“外部平台发来的图片”基本成立，但对“工具调用过程中下载、生成、搜索、抓取到的图片”还不成立。

典型场景：

- 用户说：“从网上下载这张图，然后看一下里面是什么。”
- 模型调用某个下载工具，工具返回 `{ "path": "./data/temp/media/x.png" }`。
- 后续模型上下文里只看到 JSON 文本和本地路径。
- 当前运行时不会自动把这个路径重新解析为图片输入。
- 如果主模型是视觉模型，它也不会自动看到图片像素。
- 如果主模型不是视觉模型，也不会自动走 fallback vision 描述。

因此，当前系统存在一个边界不一致：

- 用户附件图片：是一等多模态对象。
- 工具产出图片：只是普通文本结果。

## 2. 当前实现现状

### 2.1 用户附件图片链路

关键代码：

- `nahida_bot/plugins/base.py`
  - `InboundAttachment`
  - `MediaDownloadResult`
- `nahida_bot/core/session_runner.py`
  - `_build_user_parts()`
  - `_build_vision_parts()`
  - `_build_fallback_parts()`
  - `_auto_describe_image()`
  - `handle_image_understand_tool()`
  - `_resolve_attachment()`
- `nahida_bot/agent/media/resolver.py`
  - `MediaResolver`
  - `ResolvedMedia`
  - `MediaPolicy`
- `nahida_bot/agent/providers/base.py`
  - `ModelCapabilities`
  - `_serialize_openai_part()`
- `nahida_bot/agent/providers/openai_responses.py`
  - `_serialize_user_part()`

当前行为：

1. `SessionRunner.run_stream()` 拿到本轮 `attachments`。
2. 解析当前 session 使用的 provider/model。
3. 读取该模型的 `ModelCapabilities`。
4. 若 `image_input=true`，走 `_build_vision_parts()`。
5. `_build_vision_parts()` 通过 `_resolve_attachment()` 调用 `MediaResolver`。
6. `MediaResolver` 从本地路径或 URL 读取图片，校验大小与 MIME，编码为 base64。
7. provider 把 `ContextPart` 序列化为上游 API 支持的图片输入块。

对于非视觉模型：

1. `_build_user_parts()` 发现 `image_input=false`。
2. 有图片附件时走 `_build_fallback_parts()`。
3. `image_fallback_mode=auto` 时，自动调用 fallback vision provider。
4. `image_fallback_mode=tool` 时，暴露 `image_understand` 工具并插入图片提示。
5. `image_fallback_mode=off` 时，不处理图片。

### 2.2 工具结果链路

关键代码：

- `nahida_bot/agent/loop.py`
  - `_execute_tools()`
  - `_build_tool_message()`
- `nahida_bot/plugins/tool_executor.py`
  - `RegistryToolExecutor.execute()`
- `nahida_bot/plugins/mcp/tool_adapter.py`
  - `serialize_mcp_result()`
- `nahida_bot/channels/telegram/plugin.py`
  - `download_media`
- `nahida_bot/channels/milky/plugin.py`
  - `milky_get_resource_temp_url`

当前行为：

1. provider 发出 tool call。
2. `AgentLoop` 执行工具。
3. 工具返回普通对象或字符串。
4. `_build_tool_message()` 把结果包成 JSON 字符串：

   ```json
   {
     "status": "ok",
     "output": "...",
     "error": null,
     "logs": []
   }
   ```

5. 这个 tool message 作为 `role="tool"` 的文本消息放回 conversation。
6. 后续 provider call 只看到文本内容。

Telegram 的 `download_media` 工具当前返回：

```json
{
  "path": "...",
  "file_name": "...",
  "file_size": 12345
}
```

Milky 的资源工具当前返回：

```json
{
  "resource_id": "...",
  "url": "...",
  "expires_hint": 300
}
```

MCP image content 当前会被序列化成类似：

```text
[Image: image/png, ~12345 chars base64]
```

这些结果都没有被运行时识别为“新的图片媒体对象”。

## 3. 模型能力标记的意义

`ModelCapabilities.image_input` 当前表达的是：

> 这个模型是否原生接受图片输入。

如果为 `true`：

- 用户附件图片可以直接进入模型请求。
- 图片 part 会按 provider 协议序列化。
- 模型可直接基于像素内容回答。

如果为 `false`：

- 图片不会直接传给主模型。
- 运行时应当把图片转换为文字描述，或者提供按需分析工具。
- 主模型只基于文字描述、OCR、元信息继续推理。

目前这个能力判断只作用于用户附件图片，不作用于工具产出图片。

理想情况下，`image_input` 应该统一作用于所有图片来源：

- 用户上传图片。
- 平台附件下载后的图片。
- 工具下载的图片。
- MCP 返回的图片。
- 内置工具生成的图片。
- 网页截图、浏览器截图、代码生成图表等运行时产物。

## 4. Codex 式处理方式对照

Codex/类似 agent 的做法可以抽象为：

- 文件路径是文本引用。
- 图片内容是多模态输入块。
- 模型不会因为看到路径就自动获得图片像素。
- 必须通过运行时显式把图片注册为可读媒体，并在下一次模型调用时作为 image part 注入。

这有几个重要性质：

- 工具可以下载文件，但下载动作不等于视觉读取动作。
- 运行时需要决定该图片是否安全、是否太大、是否支持 MIME。
- 若当前模型支持视觉输入，运行时可以直接附图。
- 若当前模型不支持视觉输入，运行时可以调用视觉 fallback 生成描述。
- 若用户或模型只是需要保存文件，不应强制读图。

这与当前 Nahida Bot 的用户附件链路一致，但与工具结果链路还没有打通。

## 5. 设计目标

### 5.1 功能目标

1. 工具产出的图片可以被系统识别为媒体对象。
2. 后续模型轮次可以按当前模型能力正确读取该图片。
3. 支持视觉模型时，图片以原生 image part 进入请求。
4. 不支持视觉模型时，复用 `image_fallback_mode` 生成描述或提供工具。
5. `image_understand` 能分析“当前用户附件”和“工具产出图片”。
6. 对 MCP image content、下载工具、生成图片工具提供统一接入方式。

### 5.2 非目标

1. 不在每个工具里重复实现视觉分析。
2. 不让模型仅凭路径字符串假装读过图片。
3. 不把任意本地路径自动暴露给模型读取。
4. 不绕过现有 `MediaResolver` 的大小、MIME、URL 安全策略。
5. 不要求所有工具一次性迁移，可以先兼容旧格式。

## 6. 方案对比

### 方案 A：工具自己调用视觉模型并返回描述

做法：

- 每个会下载图片的工具都自行调用 vision provider。
- 工具结果直接返回图片描述。

优点：

- 实现局部简单。
- 主模型无需多模态能力。
- 对现有 agent loop 改动少。

缺点：

- 每个工具都要重复一套图片解析、限流、大小校验、fallback 路由。
- 用户可能只是想下载图片，不一定想分析图片。
- 无法复用 `image_understand`。
- 难以支持“先下载，后续追问这张图”的会话体验。
- 工具层会侵入 provider 路由逻辑。

结论：

不推荐作为主方案。可以作为个别工具的临时兼容方式。

### 方案 B：工具返回路径，提示模型再调用 `image_understand`

做法：

- 工具继续返回路径或 URL。
- prompt 告诉模型：“如果需要看图片，请调用 image_understand。”
- `image_understand` 增加按路径读取图片的能力。

优点：

- 改动中等。
- 保留按需分析。
- 主模型可以控制是否分析。

缺点：

- 模型需要从 JSON 文本中理解哪个路径是图片。
- `image_understand` 如果接受任意 path，会引入本地文件访问边界问题。
- 对视觉模型也不能自动原生传图。
- 工具产物没有统一生命周期和 media_id。
- 历史追问时容易丢失引用。

结论：

可以作为过渡方案，但不应让 `image_understand` 直接接受任意本地路径。更好的方式是接受受控 `media_id`。

### 方案 C：Agent loop 识别工具结果中的图片并注册为媒体对象

做法：

- 定义工具结果中的媒体描述格式。
- `AgentLoop` 或 `SessionRunner` 在工具执行后扫描结果。
- 发现图片后注册到本轮或 session 的 media registry。
- 后续模型调用时按能力自动注入图片或描述。

优点：

- 与用户附件链路统一。
- 可以复用 `MediaResolver`。
- 可以复用 `image_fallback_mode`。
- 支持视觉模型原生读图。
- 支持非视觉模型 fallback 描述。
- 支持后续追问。
- 支持不同来源：path、url、base64、MCP image、生成图片。

缺点：

- 需要新增 media registry 或扩展现有 attachments 上下文。
- 需要定义工具媒体结果协议。
- 需要处理注入时机，避免每轮重复注入过多图片。
- 需要测试工具循环中的多模态上下文。

结论：

推荐主方案。

### 方案 D：把所有工具结果都转换为 `ContextPart`

做法：

- 工具执行结果不再只是文本，而是可以返回 `ContextPart` 列表。
- tool message 可携带 text、image、file 等多种 part。
- provider 序列化时直接处理 tool role 的多模态 part。

优点：

- 抽象最完整。
- 对未来音频、视频、文件也更自然。
- 更接近 Responses API 的 item 模型。

缺点：

- 改动面大。
- 不同 provider 对 `role=tool` 的图片支持并不一致。
- OpenAI-compatible/Anthropic/Responses 的 tool result 多模态格式差异较大。
- 很容易破坏当前稳定的 tool call round-trip。

结论：

长期可考虑，但不适合作为第一阶段实现。

## 7. 推荐方案

推荐采用方案 C：

> 工具结果继续以文本/JSON 形式回传给模型，但运行时额外识别并登记其中的图片媒体。下一轮模型调用时，SessionRunner 根据当前模型能力决定把这些媒体以原生图片、fallback 描述或 `image_understand` 可引用媒体的方式注入。

这相当于引入一个“工具产出媒体注册层”：

```text
tool call
  -> tool result
  -> parse media artifacts
  -> register media_id
  -> next provider call
       -> image_input=true: attach image part
       -> image_input=false + auto: attach description
       -> image_input=false + tool: attach hint, image_understand(media_id)
       -> off: only keep textual metadata
```

## 8. 数据模型设计

### 8.1 ToolMediaArtifact

建议新增内部数据类：

```python
@dataclass(slots=True, frozen=True)
class ToolMediaArtifact:
    kind: str  # image, audio, video, file
    media_id: str
    source_tool: str
    path: str = ""
    url: str = ""
    base64_data: str = ""
    mime_type: str = ""
    file_size: int = 0
    width: int = 0
    height: int = 0
    description: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
```

第一阶段只实现 `kind="image"`。

### 8.2 工具结果推荐协议

工具可以返回如下结构：

```json
{
  "media": [
    {
      "kind": "image",
      "path": "./data/temp/media/x.png",
      "mime_type": "image/png",
      "file_size": 12345,
      "description": ""
    }
  ],
  "text": "Downloaded image to ./data/temp/media/x.png"
}
```

也可以支持单个对象：

```json
{
  "kind": "image",
  "path": "./data/temp/media/x.png",
  "mime_type": "image/png"
}
```

为了兼容旧工具，可以启发式识别：

- `path` 后缀为 `.png`、`.jpg`、`.jpeg`、`.webp`。
- `url` 的 content-type 或后缀看起来是图片。
- MCP image content。
- OpenAI image generation result。

启发式识别应当保守，不能把任意本地路径都当成可读文件。

### 8.3 media_id 生成

建议格式：

```text
tool:{tool_name}:{short_hash}
```

例如：

```text
tool:download_media:a1b2c3d4
tool:mcp_browser_screenshot:91ef32ab
tool:image_generation:77aa1010
```

hash 输入可以包含：

- tool name
- path/url/base64 hash
- session id
- timestamp 或 monotonic sequence

需要避免泄露完整本地路径。

### 8.4 与 InboundAttachment 的关系

为了复用现有链路，第一阶段可以把 `ToolMediaArtifact` 转换为 `InboundAttachment`：

```python
InboundAttachment(
    kind="image",
    platform_id=artifact.media_id,
    url=artifact.url,
    path=artifact.path,
    mime_type=artifact.mime_type,
    file_size=artifact.file_size,
    width=artifact.width,
    height=artifact.height,
    alt_text=artifact.description,
    metadata={
        "source": "tool",
        "source_tool": artifact.source_tool,
        ...
    },
)
```

这样可以直接复用：

- `_resolve_attachment()`
- `MediaResolver`
- `_build_vision_parts()`
- `_build_fallback_parts()`
- `_auto_describe_image()`
- `handle_image_understand_tool()`

## 9. 上下文注入策略

### 9.1 注入范围

建议第一阶段只注入“最近一轮工具调用产出的图片”，避免历史图片反复进入上下文。

后续再扩展为：

- 最近 N 张工具图片。
- 用户明确引用的 media_id。
- 当前 conversation 中未消费的媒体。
- 被收藏/持久化的媒体。

### 9.2 视觉模型

如果当前模型 `image_input=true`：

- 自动把最近工具图片作为 `ContextPart(type="image_base64")` 或 `image_url` 注入下一轮。
- 同时保留 tool result 的文本 JSON，方便模型知道图片来源、路径、文件名等。
- 遵守 `max_image_count` 和 `max_image_bytes`。

### 9.3 非视觉模型 + auto

如果当前模型 `image_input=false` 且 `image_fallback_mode=auto`：

- 对最近工具图片调用 fallback vision。
- 把描述作为 `image_description` 注入。
- 描述中包含 `media_id` 和来源工具摘要。

### 9.4 非视觉模型 + tool

如果当前模型 `image_input=false` 且 `image_fallback_mode=tool`：

- 注入文本提示：

  ```text
  [Image produced by tool download_image: media_id=tool:download_image:a1b2c3d4. Use image_understand to analyze it.]
  ```

- `image_understand` 必须能查到这些 tool media。

### 9.5 off

如果 `image_fallback_mode=off`：

- 不自动分析。
- 只保留 tool result 的文本输出。
- 可选：仍记录 media registry，供后续用户切换模式或显式命令使用。

## 10. 存储与生命周期

### 10.1 短期上下文

当前有 `current_attachments` ContextVar，用于本轮用户附件。

可以新增：

```python
current_tool_media: ContextVar[tuple[InboundAttachment, ...]]
```

或者扩展一个更通用的：

```python
current_media_artifacts: ContextVar[tuple[InboundAttachment, ...]]
```

第一阶段建议新增独立变量，降低对用户附件链路的影响。

### 10.2 会话历史

如果希望后续追问“刚才下载的那张图”，需要持久化媒体元数据。

可选方案：

1. 写入 tool message metadata。
2. 写入 assistant turn metadata。
3. 写入单独的 media registry 表。

第一阶段建议：

- 在 tool message metadata 中记录 `media_artifacts`。
- `SessionRunner._find_attachment_in_history()` 扩展为也查 tool/assistant metadata。
- 后续再考虑单独表。

### 10.3 缓存

继续复用 `MediaResolver` 和 `MediaCache`：

- URL 下载走 cache。
- 本地 path 读取后转 base64。
- max size、MIME 校验保持一致。

对于工具已经下载到本地的文件，不应二次复制，除非需要统一 TTL 管理。

## 11. 安全边界

### 11.1 本地路径

风险：

- 工具可能返回任意 path。
- 模型可能诱导工具返回敏感路径。
- 如果 `image_understand` 接受 path，就可能成为本地文件读取通道。

建议：

- `image_understand` 不接受任意 path，只接受 `media_id`。
- 只有运行时登记过的媒体才能被分析。
- 对工具返回的 path 做 allowlist：
  - workspace 目录
  - configured media cache/temp 目录
  - 工具声明的输出目录
- 不允许读取任意绝对路径，除非该工具被明确标记为可信。

### 11.2 URL

当前 `MediaResolver` 已经有 SSRF 防护：

- 只允许 http/https。
- 默认拒绝 localhost、private、loopback、link-local 等地址。
- 只有 `attachment.metadata["trusted_url"]` 为真时才允许私有网络。

工具产出 URL 应继续走这个逻辑。

### 11.3 MIME 与大小

继续遵守：

- `multimodal.max_image_bytes`
- `supported_image_mime_types`
- 模型级 `max_image_bytes`
- 模型级 `max_image_count`

### 11.4 prompt 注入

图片本身可能包含恶意 OCR 文本。

fallback vision 描述建议明确标注为“图片内容描述”，并在系统层避免把图中文字当作指令执行。

## 12. 实现路径

### 阶段 1：文档与协议冻结

目标：

- 确认工具媒体结果协议。
- 确认 media_id 格式。
- 确认注入策略。

产物：

- 本文档。
- 可选：更新 `docs/CONFIGURATION.md`，说明 `image_fallback_mode` 也适用于工具产出媒体。

### 阶段 2：内部媒体提取器

新增模块建议：

```text
nahida_bot/agent/media/artifacts.py
```

职责：

- 从工具结果 `output` 中提取图片 artifact。
- 支持 dict、JSON string、list。
- 支持显式 `media` 字段。
- 保守支持旧格式 `path`/`url`。
- 生成 `media_id`。
- 转换为 `InboundAttachment`。

建议 API：

```python
def extract_media_artifacts(
    output: object,
    *,
    tool_name: str,
    session_id: str = "",
) -> list[InboundAttachment]:
    ...
```

测试：

- 显式 `media` 数组。
- 单个 image 对象。
- Telegram 旧格式 path。
- Milky 旧格式 url。
- 非图片 path 不提取。
- 不合法 JSON 不提取。

### 阶段 3：AgentLoop 记录工具媒体

修改 `AgentLoop._build_tool_message()`：

- 从 `ToolExecutionResult.output` 提取媒体 artifact。
- 在 tool message metadata 中写入：

  ```python
  metadata={
      "tool_call_id": ...,
      "tool_name": ...,
      "media_artifacts": [...],
  }
  ```

注意：

- 不改变现有 `content` JSON，避免破坏模型看到的工具结果。
- 不在这里做图片解析或调用 vision。
- 只做结构化登记。

可能的问题：

- `AgentLoop` 当前不直接知道 `session_id`。
- media_id 生成可以先不依赖 session id。
- 或在 `run_stream()` 参数中增加 `session_id`，但这会扩大改动。

更低改动方式：

- media_id 基于 tool call id + tool name + path/url hash。

### 阶段 4：SessionRunner 从 tool messages 收集媒体

修改 `SessionRunner.run_stream()` 的 agent loop 调用与事件处理：

- 当前 `AgentLoop` 内部生成 tool messages 并继续下一轮 provider call。
- 如果要让下一轮 provider call 立即看到工具图片，仅在 `SessionRunner` 外层收集 `done` 事件已经太晚。

因此阶段 4 有两个选择：

#### 选择 4A：在 AgentLoop 内部完成注入

需要让 `AgentLoop` 在工具执行后把 media artifact 转为下一轮 conversation 的额外 context。

问题：

- `AgentLoop` 不知道模型 capabilities。
- `AgentLoop` 不知道 `MediaResolver`。
- 会让 loop 层变胖。

不推荐。

#### 选择 4B：让 AgentLoop 暴露 hook，由 SessionRunner 提供工具媒体处理器

建议新增可选接口：

```python
class ToolResultMediaHandler(Protocol):
    async def build_context_messages(
        self,
        artifacts: list[InboundAttachment],
        capabilities: ModelCapabilities | None,
    ) -> list[ContextMessage]:
        ...
```

或者更简单：

```python
AgentLoop.run_stream(..., tool_result_context_builder=callable)
```

执行流程：

1. `_execute_tools()` 得到 tool messages。
2. 从 metadata 提取 `media_artifacts`。
3. 调用 hook 生成额外 `ContextMessage`。
4. 把这些 context messages 加入 conversation，供下一轮 provider call 使用。

优点：

- `SessionRunner` 仍负责多模态策略。
- `AgentLoop` 只负责在合适时机调用 hook。

推荐。

### 阶段 5：SessionRunner 实现工具媒体上下文构建

新增方法：

```python
async def _build_tool_media_context_messages(
    self,
    artifacts: list[InboundAttachment],
    *,
    capabilities: ModelCapabilities | None,
) -> list[ContextMessage]:
    ...
```

逻辑：

- 如果无 artifact，返回空。
- 如果 `capabilities.image_input=true`：
  - 调用 `_build_vision_parts("", artifacts, ...)`。
  - 返回 `ContextMessage(role="user", source="tool_media", content="", parts=parts)`。
- 如果 `image_input=false`：
  - 调用 `_build_fallback_parts("", artifacts)`。
  - 返回 `ContextMessage(role="user", source="tool_media", content=..., parts=parts)`。

需要注意：

- `role` 是否用 `user` 还是 `system` 要谨慎。
- 图片 part 通常只有 user role 才被 provider 接受。
- 推荐用 `role="user"`，`source="tool_media"`，content 明确说明“以下图片来自工具结果，不是用户新消息”。

### 阶段 6：扩展 image_understand 查找范围

当前 `handle_image_understand_tool()` 通过 `_find_attachment_in_history()` 查当前附件和历史用户附件。

需要扩展：

- 当前工具媒体 registry。
- tool message metadata 中的 `media_artifacts`。
- assistant metadata 中可能保存的 generated images。

新增方法可以命名为：

```python
async def _find_media_attachment(self, media_id: str) -> InboundAttachment | None:
    ...
```

并让 `_find_attachment_in_history()` 变成它的子路径。

### 阶段 7：工具适配迁移

优先迁移：

1. Telegram `download_media`
2. Milky `milky_get_resource_temp_url`
3. MCP `serialize_mcp_result()` image content
4. OpenAI Responses image generation result
5. 浏览器截图或网页下载类工具

迁移方式：

- 保留旧字段 `path`/`url`。
- 新增 `media` 数组。

示例：

```json
{
  "path": "./data/temp/media/photo.png",
  "file_name": "photo.png",
  "file_size": 12345,
  "media": [
    {
      "kind": "image",
      "path": "./data/temp/media/photo.png",
      "mime_type": "image/png",
      "file_size": 12345
    }
  ]
}
```

### 阶段 8：测试

单元测试：

- `extract_media_artifacts()` 显式协议。
- `extract_media_artifacts()` 旧工具格式。
- 非图片不提取。
- 超大图片被 `MediaResolver` 拒绝。
- unsupported MIME 被跳过。
- `image_understand` 可读取 tool media。

集成测试：

- 视觉模型：工具返回本地图片 path，下一轮 provider 收到 `image_base64`。
- 非视觉 + auto：工具返回图片，下一轮 provider 收到 image description。
- 非视觉 + tool：下一轮 provider 看到 `image_understand` hint，调用工具后得到描述。
- MCP image content 不再只变成占位文本，而是登记为 media artifact。

回归测试：

- 普通工具文本结果不受影响。
- 工具调用 JSON round-trip 不受影响。
- 用户附件图片链路不受影响。
- 模型 `tool_calling=false` 时不暴露工具，但 auto fallback 仍可工作。

## 13. 建议的第一版最小实现

为了降低风险，第一版可以只做：

1. 新增 `extract_media_artifacts()`。
2. 识别工具返回 JSON 中的显式 `media` 数组。
3. 支持 Telegram 旧格式 path 和 Milky 旧格式 url。
4. 在 tool message metadata 记录 `media_artifacts`。
5. 新增 AgentLoop hook，让 SessionRunner 在工具结果后插入一条 `tool_media` user message。
6. 只处理“刚刚工具调用产出的图片”，不做跨轮持久化。
7. `image_understand` 暂时仍只处理用户附件，第二版再扩展。

这个最小版本可以验证核心价值：

- 工具下载图片后，视觉模型能在下一轮真正看到图片。
- 非视觉模型能自动拿到 fallback 描述。

## 14. 开放问题

1. 工具产出图片是否应默认自动注入，还是只有用户意图包含“看/分析/识别”时才注入？
2. 一次工具调用返回多张图片时，默认传几张？
3. 工具返回图片后，是否应立即触发 fallback vision，还是等主模型下一轮需要时再触发？
4. `tool_media` 用 `role="user"` 是否会影响对话语义？是否需要 provider 层支持非用户多模态 context？
5. 是否需要单独的 `media_registry` 数据库表？
6. 是否需要 `/media list`、`/media clear` 等调试命令？
7. 图片生成结果应该作为 outbound attachment 发给用户，还是只作为上下文媒体？

## 15. 当前结论

当前系统已经有较完整的“用户附件图片”多模态处理能力，但“工具产出图片”还停留在文本结果层。后续推荐引入工具媒体 artifact 注册机制，把工具产出的图片统一转为受控 `media_id` 和 `InboundAttachment`，再复用现有 `MediaResolver`、`ModelCapabilities.image_input`、`image_fallback_mode` 和 `image_understand`。

这样可以保持架构边界清晰：

- 工具负责获取媒体。
- 运行时负责登记、校验、缓存和按模型能力注入。
- provider 负责序列化成上游 API 格式。
- 模型只根据自己真实获得的输入进行回答。
