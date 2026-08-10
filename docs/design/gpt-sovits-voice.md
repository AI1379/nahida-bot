# 语音输出设计（统一 TTS 层，GPT-SoVITS 为首个后端）

> 记录时间：2026-07-07
> 状态：**Part A + `speak` 工具 + OneBot 出站 `record` 已实现** —— 统一 TTS 抽象（`nahida_bot/speech/`：`TtsProvider` registry + `SpeechService` + GPT-SoVITS adapter）已落地；`speak` 工具已注册（`plugins/tts/plugin.py`）；OneBot 出站已构造 `RecordSegment`（`channels/onebot/segment_models.py` + `message_converter.py`）。**仍待实现**：Telegram 语音出站（仅支持 photo/document）、memory 投影（§11）。
> 相关文档：
>
> - [Desktop App §9.3.1](desktop-app.md#931-统一-tts-provider-与音频分发边界) — **统一 TTS Provider 愿景**（SpeechService / TtsProvider registry / SpeechArtifactStore）。本设计实现其 Part A（可换后端）；Part B（ArtifactStore / Gateway Media / Desktop 播放）留待 Desktop Phase 7。
> - [工具产出的图片/媒体](tool-produced-image-media-design.md) — 本设计与之同构，复用其 media artifact 协议（`kind="audio"`）
> - [Agent Core](agent-core.md)
> - [Desktop App](desktop-app.md) — 已有浏览器 SpeechSynthesis 本地 TTS，需明确与服务端 TTS 的关系
> - [配置参考](../guide/configuration.md)

## 1. 背景

Nahida Bot 目前已经具备「工具产出图片 → 自动发送 + 注册为多模态媒体」的完整链路（`image_generation` 插件 + `tool-produced-image-media-design.md`）。语音输出在能力层面是对称需求：模型在某些场景下希望「说」一句话，而不是「写」一句话。

GPT-SoVITS 是一个支持少量参考音频即可克隆/合成高质量语音的 TTS 引擎，通常以独立 HTTP 服务部署（自带 `api_v2` 接口）。本设计的核心问题是：

> **「模型如何表达『这条要发语音』？」**

这是一个跨多层的决策：工具调用层、回复信号层、运行时配置层、通道发送层。本文档给出推荐方案与备选方案，并明确各层的改动点。

## 2. 与现有链路的关系

### 2.1 可直接复用的基础设施

- **`image_generation` 插件**（`nahida_bot/plugins/image_generation/plugin.py`）是「工具产出媒体并发送」的现成模板：
  - 同时注册 `/draw` 命令 + `image_generate` 工具
  - 工具参数含 `send: bool` 与 `caption: str`，模型自主决定是否发送
  - 生成后通过 `api.send_message(..., attachments=[Attachment(...)])` 投递
  - 返回值同时含 `images`（给模型）和 `media`（给运行时媒体注册层）
- **`Attachment.type`** 已支持 `"audio"`（`nahida-bot-sdk/nahida_bot_sdk/messaging.py:97`），`OutboundMessage.attachments` 即语音载体。
- **哨兵协议**（`nahida_bot/core/sentinel.py`）已支持 `NO_REPLY` / `HEARTBEAT_OK` 的纯文本与 `{"action": "..."}` JSON envelope 两种表达，且 `SILENT_REPLY_INSTRUCTION` 明确允许「工具已经把回复发出去后，主回复用 `NO_REPLY` 静默」（`nahida_bot/core/message_context.py:88-98`）。
- **media artifact 协议**（`tool-produced-image-media-design.md` §8）的 `ToolMediaArtifact.kind` 已经预留了 `"audio"`。
- **`send_local_attachment` 工具**（`nahida_bot/plugins/builtin/commands.py:2353`）已支持 `attachment_type="audio"`，是「已有本地音频文件 → 发送」的底层工具。

### 2.2 尚未实现的缺口

| 缺口 | 位置 | 说明 |
| ---- | ---- | ---- |
| 通道层无 `audio` 发送分支 | `nahida_bot/channels/telegram/plugin.py:493` | Telegram `_send_attachment` 只处理 `photo` / `document`，无 `send_voice` / `send_audio` |
| OneBot outbound 无 `record` 段 | `nahida_bot/channels/onebot/segment_models.py:87` | 入站能解析 `record`/`voice`，出站尚未构造 |
| ~~Milky outbound 无 `voice` 段~~ | ~~`nahida_bot/channels/milky/segment_converter.py:75`~~ | **已实现**：`OutgoingRecordSegment` + converter 映射 + send 路径 + 文字降级均有，测试覆盖（`test_milky_segment_converter.py:34,45`）。Milky 无需额外通道层工作。 |
| 无服务端 TTS 客户端 | — | GPT-SoVITS 客户端、配额、并发控制均不存在 |
| Desktop 本地 TTS 与服务端 TTS 无优先级 | `desktop/src/services/ttsSettingsStorage.ts` | 浏览器 SpeechSynthesis 与 GPT-SoVITS 如何并存/切换未定义 |

## 3. 目标与非目标

### 3.1 功能目标

1. 模型能够主动决定「这条回复用语音发送」。
2. 支持「只发语音、不发文字」的纯语音回复。
3. 支持单条消息中「文字 + 语音」共存。
4. 语音文件落地 workspace，可被后续追问引用、被多模态模型读取（若模型支持 audio input）。
5. 多通道（Telegram / OneBot / Milky）一致发送，遵循各平台对「语音消息」的语义（Telegram 区分 `voice` 与 `audio`）。
6. 复用现有 media artifact registry，不另起一套生命周期。

### 3.2 非目标

1. 不在第一阶段做实时流式语音（边合成边播放）。
2. 不在第一阶段做「说话人分离」「多角色对话」。
3. 不强制所有回复都转语音（那是配置开关，不是默认行为）。
4. 不在 `image_understand` 之外新增「音频理解」工具（待 audio-input 模型普及后再做）。
5. 不重写 Desktop 的本地 TTS；服务端 TTS 是补充而非替代（见 §9）。

## 4. 方案对比

### 方案 A：`speak` 工具驱动（推荐）

模型通过调用 `speak(text=..., voice?=..., emotion?=...)` 工具来表达「发语音」意图。工具内合成音频、落地、发送，并回传结果。

```
模型 → tool_call(speak, text="你好呀")
     → 插件：调用 GPT-SoVITS 合成 wav
     → 落地 workspace/generated/audio/*.wav
     → api.send_message(attachments=[Attachment(type="audio", path=...)])
     → 返回 { status, audio: {path, duration}, media: [{kind:"audio", ...}] }
模型 → 最终文本 "你好呀"（或 NO_REPLY）
```

优点：

- 与 `image_generate` 完全对称，学习成本为零。
- 模型对「是否发语音」「说什么」「用什么音色」有完整 agency。
- 语音内容与文字内容可以不同（模型可以让语音是文字的精简口语版）。
- 天然复用配额、并发、media artifact 注册层。
- 「只发语音」通过 `speak` + `NO_REPLY` 组合即可，无需新机制（见 §5）。

缺点：

- 依赖 tool calling 开启；`tool_calling=false` 的会话用不了。
- 模型可能误用（每条都发语音）或漏用；需要 prompt 引导与可选的配额限制。
- 工具往返增加一轮延迟（合成在 tool 执行阶段，用户看到语音比看到文字晚）。

结论：**主方案。**

### 方案 B：输出格式约定（哨兵扩展）

在系统提示里约定：要发语音就用 `<voice>...</voice>` 包裹，或 `{"action":"voice","text":"..."}`。运行时在 router 层 parse、剥离、路由到 TTS。

优点：

- 不依赖 tool calling。
- 适合「把这段话念出来」的轻量场景。

缺点：

- 与 streaming 强冲突：router 是流式 `_send_response`，要在拼接完整后才安全 parse，等于放弃流式文字体验。
- 哨兵机制的语义是「抑制/信号」，不是「携带内容」；挪用会让 `sentinel.py` 的职责膨胀。
- 模型对包裹格式的遵守不稳定，且与 `NO_REPLY` / `HEARTBEAT_OK` 的 JSON envelope 容易混淆。
- 难以表达「语音内容 ≠ 文字内容」。

结论：**不采用。** 「只发语音」用方案 A + 现有 `NO_REPLY` 即可达成，无需扩展哨兵语义。

### 方案 C：后置自动 TTS（配置/会话级开关）

不让模型决定，由会话级或全局配置 `voice_mode` 控制是否对每条回复自动 TTS。

```
模型 → 文本回复
     → router 检测 session.voice_mode == "always"
     → 调用 TTS 合成
     → 发送语音（可选同时发送文字）
```

优点：

- 实现简单，行为可预测。
- 适合「纯语音频道」「无障碍模式」「语音陪伴」等场景。
- 不需要模型理解语音这件事。

缺点：

- 模型没有 agency，无法选择性发语音。
- 语音内容 == 文字内容，无法做口语化。
- 对所有回复都合成，成本与延迟翻倍。

结论：**作为方案 A 的补充保留**，以 `/voice on|off` 会话级命令 + `voice_mode` 配置项形式提供，第一阶段可暂不实现，但本文档保留设计位置（见 §7.4）。

## 5. 「只发语音」：`speak` + `NO_REPLY` 协议

这是本设计的关键点，单独成节。

用户关心的场景：「我希望模型只说话、不输出文字」。**现有哨兵机制已经能表达，无需新增 `VOICE_ONLY` 之类的哨兵。**

时序：

1. 模型调用 `speak(text="你好呀")`。
2. 插件在 tool 执行阶段直接 `api.send_message(..., attachments=[...])` 发出语音附件 —— 这走的是与 router 文本回复**完全独立**的发送路径（见 `nahida_bot/core/router.py:940-982`，`detect_sentinel` 只作用于 router 自己的 `_send_response`）。
3. 模型随后把最终 assistant 文本写成 `NO_REPLY`（或 `{"action":"NO_REPLY"}`）。
4. router 检测到哨兵，跳过 `_send_response`，不发送任何文字。
5. 用户只收到那条语音。

这与 `SILENT_REPLY_INSTRUCTION` 里明确列举的合法场景「after a tool already delivered the reply」完全吻合。`image_generate` 已经在用同样的模式（图片由工具发出，主回复可静默）。

**不新增 `VOICE_ONLY` 哨兵的理由：**

- 语义重复：`speak` + `NO_REPLY` 已能表达。
- 哨兵的职责是「抑制信号」，让它携带语音内容会污染 `sentinel.py` 的单一职责。
- 新哨兵会和 `NO_REPLY` 竞争（模型不知道该用哪个）。

**prompt 引导**：在 `speak` 工具描述里明确写：「发完语音后，如果你不希望再附带文字，请把最终回复设为 `NO_REPLY`」。

## 6. 数据模型

### 6.1 `speak` 工具 schema

```json
{
  "type": "object",
  "properties": {
    "text": {
      "type": "string",
      "description": "要合成语音的文本。建议用口语化短句，避免长段落。这句话会成为角色对用户说的话。"
    },
    "emotion": {
      "type": "string",
      "description": "可选情绪提示，如 happy/sad/calm。是否生效取决于后端配置。"
    },
    "text_lang": {
      "type": "string",
      "description": "可选文本语言代码，如 zh/ja/en。留空由后端自动检测。"
    },
    "send": {
      "type": "boolean",
      "description": "是否发送到当前会话。默认 true。设为 false 时只合成落地、不发送（例如后续用 send_local_attachment 自定义发送）。"
    },
    "caption": {
      "type": "string",
      "description": "可选附件说明文字，部分通道会作为语音消息的附带文字显示。"
    }
  },
  "required": ["text"]
}
```

> **注意：没有 `voice` 参数。** 音色（参考音频、提示语）由人设（persona）绑定，在部署时于配置文件中固定，**运行时不切换、模型不可选**。理由：一个角色的音色是其身份的一部分，定好后不应每轮漂浮（详见 §6.4）。

### 6.2 工具返回协议

```json
{
  "status": "ok",
  "delivered_text": "你好呀",
  "audio": {
    "path": "generated/audio/voice-20260707-120000-a1b2c3d4.wav",
    "mime_type": "audio/wav",
    "file_size": 123456,
    "duration": 3.2,
    "persona_voice": "nahida",
    "text": "你好呀"
  },
  "media": [
    {
      "kind": "audio",
      "path": "generated/audio/voice-20260707-120000-a1b2c3d4.wav",
      "mime_type": "audio/wav",
      "file_size": 123456,
      "description": "GPT-SoVITS synthesized voice",
      "metadata": {
        "source": "gpt_sovits",
        "source_tool": "speak",
        "duration": 3.2,
        "persona_voice": "nahida"
      }
    }
  ],
  "sent_message_ids": ["msg_123"]
}
```

- `media` 数组遵循 `tool-produced-image-media-design.md` §8.2 的协议，`kind="audio"`。一旦该设计落地媒体注册层，工具产出的音频即可被未来的 audio-input 模型读取，与图片路径完全对称。
- **`delivered_text`** 遵循 §11.3 的统一话语约定：SessionRunner 投影层据此把 spoken text 记为 assistant turn 进 memory。`speak` 成功时**始终**附带（即使 voice-only + `NO_REPLY` 也不丢）；失败降级（§10.5）时**不带**此字段。`delivered_text` 与 `audio.text` 通常相等，但语义不同——前者是「给 memory 的话语声明」，后者是「合成所用的文本」；分开放留出未来「合成文本 ≠ 被记住文本」的余地。

### 6.3 GPT-SoVITS 后端配置（adapter）

GPT-SoVITS 是统一 TTS 层的一个 `TtsProvider` adapter，配置位于 `nahida_bot/speech/providers/gpt_sovits.py` 的 `GPTSoVITSBackendConfig`，挂在统一 `tts.backends.<name>` 下（`type: gpt-sovits-v2` 判别）。字段基于 api_v2 的**真实契约**（已核实 `api_v2.py` 源码）：

```python
class GPTSoVITSBackendConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "gpt-sovits-v2"          # 判别器，匹配 TtsProvider.type
    base_url: str = "http://127.0.0.1:9880"  # api_v2 默认 127.0.0.1:9880
    tts_path: str = "/tts"                # api_v2 合成端点（POST JSON）
    timeout_seconds: float = 180.0        # 单次合成超时；GPT-SoVITS 首次推理 + 长文本较慢
    trust_env: bool = False
    force_close_connections: bool = True

    # —— api_v2 请求参数（per-request，都有默认值，可被 voice/speak 覆盖）——
    media_type: str = "wav"               # api_v2 字段名就叫 media_type；wav/raw/ogg/aac
    text_split_method: str = "cut5"       # 文本切分：cut0=不切，cut5=按标点
    top_k: int = 15
    top_p: float = 1.0
    temperature: float = 1.0
    speed_factor: float = 1.0             # 后端默认；request.speed=0.0 时回退到此值
    repetition_penalty: float = 1.35
    batch_size: int = 1
    sample_steps: int = 32               # VITS V3 模型采样步数
    super_sampling: bool = False          # V3 超采样
    parallel_infer: bool = True
    extra_body: dict[str, Any] = Field(default_factory=dict)  # 透传其他 api_v2 参数（fragment_interval/seed 等）
```

> 注：统一 `TtsConfig`（`nahida_bot/speech/config.py`）的 `backends`/`voices` 是 raw dict，由对应 provider 的 `parse_backend_config`/`parse_voice_config` 解析。`GPTSoVITSBackendConfig` 不含并发控制（`max_concurrency`）——并发由上层 `speak` 插件的 `Semaphore` 管（§7.3）。

**与 image_generation backend 的关键差异（来自 api_v2 源码）：**

1. **无鉴权**：标准 api_v2 **没有** access token / api key。所以 backend 配置里**没有** `api_key` / `require_api_key` 字段。（如部署在反代后需鉴权，走反代的 header，不在此层处理。）
2. **无 `/health` 端点**：api_v2 只有 `/tts`、`/control`、`/set_refer_audio`、`/set_gpt_weights`、`/set_sovits_weights`。客户端不做健康检查，靠 per-request 错误分类（同 image_generation 实践）。
3. **响应非统一 JSON**：成功 HTTP 200 + `audio/{media_type}` 字节流；失败 HTTP 400 + JSON `{message, Exception}`。adapter 按 status code 分流（200→读音频字节，400→解析 JSON 错误消息）。
4. **模型切换是全局的**：`/set_gpt_weights`、`/set_sovits_weights` 把权重加载进全局 pipeline，慢且影响整个实例。所以 **一个 GPT-SoVITS 实例 ≈ 一个模型 ≈ 一个角色**；adapter**不**在请求里切模型，模型由部署者在 api_v2 启动时或通过管理脚本设定。
5. **`ref_audio_path` 是服务器侧路径**：api_v2 的 `POST /set_refer_audio`（上传文件）在源码里是**注释掉的**，只保留 `GET /set_refer_audio?refer_audio_path=`（传服务器本地路径）。所以 bot 没法在请求时推送参考音频——必须引用 api_v2 服务器上**已存在**的文件路径。`ref_audio_path` 不在 backend 配置里，而在 `tts.voices`（§6.4），语义是「TTS 服务器视角的路径」。

`extra_body` 用于透传 api_v2 演进中的其他参数（`fragment_interval`、`seed`、`batch_threshold`、`split_bucket` 等）；合并时**显式字段覆盖 extra_body**（extra_body 仅补未建模参数，与 image_generation 优先级一致）。

### 6.4 音色（voice）绑定

GPT-SoVITS 的「音色」本质是 `(ref_audio_path, prompt_text, prompt_lang)` 三元组，由 `GPTSoVITSVoice`（`nahida_bot/speech/providers/gpt_sovits.py`）建模，挂在统一 `tts.voices.<name>` 下。**音色不由模型选择，而是与人设（persona）绑定，在配置文件中固定。**

```yaml
tts:
  # 音色绑定：key 是 voice 名（通常对应 persona），value 是 GPT-SoVITS 参考音频三元组
  # ⚠️ ref_audio_path 是 GPT-SoVITS api_v2 服务器视角的路径，不是 bot 本地路径
  voices:
    nahida:
      ref_audio_path: "/data/voice/nahida.wav"   # 必须是 api_v2 服务器上已存在的文件
      prompt_text: "你好呀，我是纳西妲。"          # 参考音频对应的文本
      prompt_lang: "zh"
      text_lang: "zh"                             # 可选：该 voice 默认合成语言
      backend: default                            # 可选：指向 tts.backends 的某个实例，空→default_backend
    # 同一角色的其他参考音频变体（如不同情绪），共用同一模型：
    nahida_soft:
      ref_audio_path: "/data/voice/nahida_soft.wav"
      prompt_text: "……轻轻地说。"
      prompt_lang: "zh"
  default_voice: nahida   # 未指定 voice 时的兜底
```

运行时（`SpeechService.resolve_voice` + provider 解析）：

- `SpeechService.synthesize(text, voice=<name>)` 按 name 查 `tts.voices`，取出 `(voice_raw, backend_name)`；解析顺序：显式 name → `default_voice` → 唯一 voice。
- voice 的 `backend` 字段指向用哪个 `tts.backends` 实例（空则用 `default_backend`）；adapter 用 `parse_voice_config` 把 raw 解析成 `GPTSoVITSVoice`。
- api_v2 的 `/tts` 支持 per-request `ref_audio_path`，无需用全局 `set_refer_audio`；模型感知不到音色选择这件事。

> **persona 与 voice 的关系**：当前 `voices` 的 key 就是 voice 名。`speak` 插件（§7.1，阶段 3）会从当前 session 的 persona（人设系统）查出对应的 voice 名，再交给 `SpeechService`。即 persona→voice 映射在插件层，统一 TTS 层只认 voice 名。

**部署拓扑约束（重要）：**

- 因 api_v2 无上传端点，`ref_audio_path` 必须是 **api_v2 服务器上已存在**的文件路径。部署者负责把参考音频放到 TTS 服务器并填入正确的服务器侧路径。
- 因模型切换全局且慢，**一个 api_v2 实例只稳定服务一个角色模型**。上例 `nahida` / `nahida_soft` 是同一模型下的不同参考音频变体（同角色不同语气），合法；若要支持**不同角色**（不同模型），需开多个 api_v2 实例（多个 backend），voice 条目里用 `backend` 字段指向对应实例。

**为什么不在 `speak` 工具暴露 `voice` 参数：**

- 音色是身份的一部分，一人设一定音，运行时切换会让角色「变声」，破坏一致性。
- 模型不该承担「我这次用什么嗓子」的决策，这是部署者的职责。
- 与未来 person-identity-system 对齐：persona 决定呈现层（头像、语气、音色）。

若将来需要「同一人设多情绪音色」，应由 persona 配置内部根据 `emotion` 选择不同参考音频，而非让模型直接指定 `voice`。

## 7. 设计要点

### 7.1 模块结构

统一 TTS 层是**核心模块**（非插件），因为 Channel、Desktop、`speak` 工具都要消费它（对齐 §9.3.1 的「Python SpeechService」定位）：

```
nahida_bot/speech/                     ← 核心模块（Application 实例化，经 plugin API 暴露）
├── base.py        # SpeechRequest / SpeechArtifact / TtsError / TtsProvider(ABC)
├── config.py      # TtsConfig：backends/voices 是 raw dict，按 type 判别
├── service.py     # SpeechService：registry + 按 type dispatch + voice 解析
└── providers/
    └── gpt_sovits.py   # GPTSoVITSProvider(TtsProvider) + GPTSoVITSBackendConfig + GPTSoVITSVoice
```

`speak` 工具 + `/speak` 命令仍由**插件**注册（阶段 3，对照 `image_generation` 插件形态），调核心 `SpeechService`——与 `builtin-commands` 用核心 `memory_store` 的模式一致。

**加新后端零摩擦**：写 `nahida_bot/speech/providers/<engine>.py`（`XxxProvider(TtsProvider)`，注册进 `service._BUILTIN_PROVIDERS`）+ 配置里加个 `type: <engine>` backend。`SpeechService` / Channel / `speak` 工具都不用改。

### 7.2 输出目录与文件命名

- 默认 `output_dir: "generated/audio"`（workspace 相对路径，校验逻辑复用 `image_generation` 的 `_resolve_output_dir`）。
- 文件名：`voice-{YYYYMMDD-HHMMSS}-{sha256_short}.{ext}`。
- 落地后 `Attachment.path` 指向绝对路径，返回值里给 workspace 相对路径。

### 7.3 配额与并发

- 复用 `image_generation` 的 24h 滚动配额模式（`_image_quota_events` deque + `_reserve_image_quota`），改为 `max_seconds_per_24h` 或 `max_calls_per_24h`（语音合成按「秒数」计费更合理，但实现复杂；首版按「调用次数」即可）。
- 每 backend 一个 `asyncio.Semaphore`（并发上限由 `speak` 插件层配置，GPT-SoVITS 单实例通常串行；adapter 自身不做并发控制）。

### 7.4 方案 C 的会话级开关（保留设计，首版可不实现）

- 新增 `/voice on|off|status` 命令，写入 session metadata。
- router 在 `_send_response` 前检查 `session.voice_mode`，若为 `always` 则：
  1. 调用 GPT-SoVITS 合成 `send_text`。
  2. 发送语音附件。
  3. 根据 `voice_mode_keep_text` 决定是否同时发送文字。
- 这条路径**不经过 `speak` 工具**，是 router 层的后置 hook，需要在 `router.py` 增加一个 `_maybe_voice_reply(send_text, inbound, session_id)` 钩子。
- 与方案 A 共存：即使开了 `voice_mode=always`，模型调用 `speak` 仍走工具路径；router 只对「未被工具处理的文字回复」做自动 TTS。

## 8. 通道层改动

### 8.1 Telegram

`nahida_bot/channels/telegram/plugin.py:493` 的 `_send_attachment` 需要新增分支：

- `attachment.type == "voice"`：调用 `bot.send_voice`，发送 opus/ogg。Telegram 的「语音消息」（带波形、可后台播放）**只接受 ogg/opus**。GPT-SoVITS 默认输出 wav，需要转码。
- `attachment.type == "audio"`：调用 `bot.send_audio`，发送任意音频文件（显示为音频播放器，可标标题/ performer）。

**转码策略**：

- 引入 `pydub` 或 `ffmpeg-python` 作为可选依赖（`uv sync --extra voice-encode`）。
- 当 `attachment.type == "voice"` 且 mime 非 `audio/ogg` 时，自动 wav → ogg/opus。
- 无转码依赖时降级为 `send_audio`（音频文件而非语音消息），并 log warning。

`Attachment.type` 的选择策略：

- `speak` 工具默认产出 `type="voice"`（语义是「说话」）。
- 可配置 `tts.attachment_type: "voice" | "audio"`，覆盖默认。

### 8.2 OneBot

`nahida_bot/channels/onebot/segment_models.py:87` 已有 `VoiceSegment`（入站），出站需新增构造：

- 在 OneBot 插件的 outbound 发送路径里，遇到 `Attachment.type in ("voice", "audio")` 时构造 `[CQ:record,file=file:///...]` 段（v11）或 array segment `{"type":"record","data":{"file":...}}`（v12）。
- OneBot 协议端（NapCat/Lagrange）通常接受本地路径或 base64；silk 编码由协议端处理，无需 bot 侧转码。

### 8.3 Milky

**已实现，无需额外工作。** `nahida_bot/channels/milky/segment_converter.py:75-76` 已把 `Attachment.type in {"audio","voice","record"}` 映射为 `OutgoingRecordSegment(uri)`（`segments.py:240`），`plugin.send_message` 经 `to_payload` → `_send_segments` → `send_group/private_message` 发出，record 段发送失败有文字降级（`plugin.py:748-763`）。测试覆盖见 `test_milky_segment_converter.py:34,45`。

GPT-SoVITS 的 `speak` 工具产出的 wav 文件，构造 `Attachment(type="voice", path="/abs/path.wav")` 即可被 Milky 通道直接发送；本地路径经 `_attachment_uri`（`segment_converter.py:206`）转成 `file://` URI，Lagrange.Milky 协议端接受 `file://` 并自行处理编码。`OutgoingRecordSegment` 无 `summary` 字段，voice 附件的 `caption` 会被忽略（QQ 语音消息本身不支持附带文字，符合平台语义）。

> 注：`type="audio"` 与 `type="voice"` 在 Milky 都映射到 `record` 段（QQ 不区分语音/音频文件）。

### 8.4 Desktop

Desktop 当前用浏览器 `SpeechSynthesis`（`desktop/src/services/ttsSettingsStorage.ts`）做本地 TTS，与服务端 GPT-SoVITS 是两套独立链路：

- **服务端 TTS（GPT-SoVITS）**：语音作为 `Attachment` 通过 Gateway 推到 Desktop，Desktop 直接播放音频流。
- **本地 TTS（SpeechSynthesis）**：Desktop 自行合成，不依赖服务端。

首版建议：**两者并存，Desktop 优先播放收到的音频附件，无附件时回落到本地 SpeechSynthesis。** 即：

```
收到消息 → 有 audio attachment？ → 播放附件
                              └→ 无 → 本地 TTS 兜底（保留现有行为）
```

这样 Nahida 角色音色由 GPT-SoVITS 提供（高质量、可克隆），其他场景仍由本地兜底。

## 9. 与 Desktop TTS 的关系（再强调）

不重写、不替换 Desktop 本地 TTS。GPT-SoVITS 是「角色音色」的来源，本地 TTS 是「无附件时的兜底」。若未来想完全切换到服务端 TTS，只需让 Desktop 配置「无附件时不本地合成」即可，不影响服务端设计。

## 10. 安全边界

### 10.1 文本长度

GPT-SoVITS 单次合成对超长文本不稳定（易爆音、漂移）。`speak` 工具应在客户端做 `max_text_length`（默认 300 字符）截断或分段，超长时：

- 截断并 log warning，或
- 分段合成、拼接音频（实现复杂，首版截断即可）。

### 10.2 配额

防止模型滥用 `speak` 刷屏：复用 §7.3 的 24h 配额，超限时工具返回 `quota_exceeded` 错误（对照 `image_generation_quota_exceeded`）。

### 10.3 ref_audio_path 越界

`tts.voices` 配置里的 `ref_audio_path` 可能指向任意本地路径。这是**管理员配置**（非模型可控，模型连 `voice` 参数都没有），因此不构成模型诱导攻击面。但应在加载时校验路径存在、可读，避免运行时崩溃。

### 10.4 prompt 注入

语音内容本身可能被用于社会工程（克隆声音钓鱼）。`speak` 工具的 `text` 来自模型，模型可能被诱导合成敏感内容。首版不做内容过滤；如需，可在客户端加关键词黑名单（与 `image_generation` 的 prompt 审核对称）。

### 10.5 合成失败降级

**决定**：GPT-SoVITS 合成失败时，**自动降级为纯文字发送**，并 log warning。不把失败抛回给模型让它自己处理（那样模型可能再调一次、或回 `NO_REPLY` 导致用户什么都收不到）。

降级由 `speak` 工具内部完成：

1. 合成失败（HTTP 错误、超时、音频为空等）→ log `tts_synthesis_failed`（错误码见 `nahida_bot/speech/base.py:TtsError`，由 adapter 抛出、`SpeechService` 补 `backend` 归因）。
2. 若 `send=true`：工具自己把 `text` 作为纯文本 `OutboundMessage` 发送到当前会话（与 `image_generation._send_text_to_session` 同路径）。
3. 工具返回 `{status: "degraded", fallback: "text", error: "..."}`，不返回 `audio`/`media`。
4. 模型看到 degraded 后，**不需要再做任何事**；若它本来打算回 `NO_REPLY`，用户也已经收到了文字。

这样保证「speak 的内容一定能送达用户」，不会因为 TTS 后端挂了而吞消息。配额上，degraded 的调用**不计入**成功配额（已 `_release_image_quota` 模式释放）。

## 11. 工具产出话语的记忆持久化（统一设计）

> 本节起于 `speak` 的特殊需求，但审计现状后发现这是一个**普遍缺口**：所有「工具向当前会话发出角色话语」的场景都掉出了 memory。本节给出统一设计，`speak` 是首个调用方。

### 11.1 现状审计：工具发送的话语普遍不在 memory

当前「工具发消息给用户」的几个入口，memory 行为不一致：

| 工具 | 发送目标 | 是否进 memory | 审计（delivery ledger） |
| ---- | ---- | ---- | ---- |
| `_tool_message`（跨会话消息） | **其他**会话 | 仅当 `delivery="record"` 时，写 target session 为 `role="system"`（`commands.py:1506`） | ✅ 总是（`commands.py:1484`） |
| `send_local_attachment` | 当前会话 | ❌ 无 | ❌ 无 |
| `image_generate`（`send=true`） | 当前会话 | ❌ 无 | ❌ 无 |
| `speak`（本文档设计） | 当前会话 | **需要**（见下） | 待定 |

根因：`SessionRunner._assistant_visible_turns()`（`session_runner.py:2532`）只投影 `result.assistant_messages` + `result.final_response`；`role="tool"` 的 tool message 设计上不进 memory（`session_runner.py:2540` 注释："Tool-call metadata is intentionally not persisted"）。

这对 `web_fetch` 这类「给模型看的数据」是对的。但对「工具替角色向用户说/发了一句话」的场景，结果是：**角色做了对外表达，下一轮却失忆**。`speak` 把这个问题暴露得最彻底（voice-only 时连 `final_response` 都是 `NO_REPLY`，memory 完全空白），但 `image_generate`、`send_local_attachment` 同样存在（角色发了一张图/一个文件，下次不知道自己发过什么）。

### 11.2 三套现有机制梳理

理清现有的三条相关路径，才能定位统一接入点：

1. **`record_message_delivery`**（`api_bridge.py:361`）——纯审计账本，写 `message_delivery_store`，注释明确 "without touching memory turns"。用于 WebUI 投递记录、可观测性。**不进会话记忆。**
2. **`record_session_event`**（`api_bridge.py:287`）——往 memory 追加一条 `role="system"` 的 `ConversationTurn`。目前仅跨会话消息工具在 `delivery="record"` 时用它向 target session 登记「有消息从别处投递进来」。
3. **`_assistant_visible_turns`**（`session_runner.py:2532`）——把 assistant 自然语言回复投影为 `role="assistant"` turn 进 memory，参与 dreaming。这是「角色真正说了什么」的唯一入口，但目前只认 assistant 文字。

结论：缺一个「**工具替角色向当前会话送达的话语 → assistant turn**」的通道。下面统一补上。

### 11.3 统一设计：`delivered_text` 约定 + SessionRunner 投影

引入一个**返回值约定**（与 `tool-produced-image-media-design.md` 的 `media` 字段约定同构）：

> 当一个工具向**当前会话**送达了「角色对用户说的话」并希望被记住时，它在工具返回 JSON 里附带一个 `delivered_text` 字段（非空字符串）。

SessionRunner 的投影层（`_assistant_visible_turns` 或新增 `_tool_delivered_utterances`）扫描本轮 tool results：

1. 解析每个成功 tool result 的 JSON，提取 `delivered_text`。
2. 每条非空 `delivered_text` → 一条 `ConversationTurn(role="assistant", source="tool_utterance", metadata={...})`。
3. 与 assistant 文字 turn 合并进 `append_turn` + dreaming。

**为什么用约定字段而非硬编码工具名：**

- `speak` 是首个，但 `image_generate`（caption）、`send_local_attachment`（caption）、未来「发视频/发表情」的工具都是同一类问题，硬编码 `name=="speak"` 会重复造轮子。
- 约定字段让「是否进 memory」由**工具按调用语义决定**（同工具某次发 caption 想记、某次空 caption 不记），而非由工具身份固定。
- 与 `media` 字段对称：工具用 `media` 声明「我产出了媒体」，用 `delivered_text` 声明「我替角色说了话」。

**投影层职责边界不变**：tool message 是否进 provider conversation 不受影响（模型仍按原样看到工具结果 JSON），只是**额外**把 `delivered_text` 记进 memory。

### 11.4 `speak` 的接入

`speak` 工具成功合成并发送后，返回值里**始终**带 `delivered_text`：

```json
{
  "status": "ok",
  "delivered_text": "你好呀",
  "audio": { "path": "...", "duration": 3.2, ... },
  "media": [{ "kind": "audio", ... }]
}
```

SessionRunner 投影出一条 `source="tool_utterance"`、metadata `{"tool": "speak", "spoken": true, "audio_path": ..., "persona_voice": ...}` 的 assistant turn。`source` 统一为 `tool_utterance`（通用机制），`metadata.tool` 标明具体来源工具，`metadata.spoken=true` 标明这是语音话语。

- 进 memory 的是 **spoken text（文字）**，不是音频附件；音频落 workspace，与图片同理（只落盘 + 进 media registry，不进会话结构化字段）。
- 失败降级（§10.5）时**不带** `delivered_text`——降级文字已走 `final_response` 正常路径，不重复投影。
- 多条语音（未来）→ 多次 `speak` → 返回里多条 `delivered_text`（或工具把多次调用各自的 text 都投影），天然支持。

### 11.5 `image_generate` / `send_local_attachment` 的可选接入

这两个工具当前完全不掉进 memory（连审计都没有）。统一设计下它们可以**按需**附带 `delivered_text`：

- `image_generate`：当 `caption` 非空时，`delivered_text = caption`；空 caption 不附带（避免记空话）。这样角色「发了图并配了文」会被记住配文，纯发图则只靠 media registry 留痕。
- `send_local_attachment`：同理，`delivered_text = caption`。

这是**可选的后续整改**，不阻塞 `speak` 落地。首版只需把 SessionRunner 的投影机制做通用，`speak` 接入；这两个工具的接入可单独排期。但**机制只建一套**，不重复。

### 11.6 与跨会话 `_tool_message` 的边界

跨会话消息工具（`_tool_message`）**不走** `delivered_text` 投影，保持现状：

- 它发往**别的**会话，在 target session 那边是「外部投递事件」，用 `record_session_event`（`role="system"`）登记是正确的——那不是 target session 里角色的自然发言，而是系统层投递。
- `delivered_text` 投影专门服务于「**当前会话**内、工具替角色表达」的场景。
- 两者职责清晰：`record_session_event` = 跨会话/系统事件（system role）；`delivered_text` 投影 = 当前会话角色话语（assistant role）。

### 11.7 与 `record_message_delivery` 的关系

`record_message_delivery` 是**审计账本**，与本节的 **memory 持久化**是两个正交关注点：

- 一个 delivery 可以「记进 memory」（角色话语）而不进审计账本，反之亦然。
- 当前 `speak`/`image_generate`/`send_local_attachment` 都没调 `record_message_delivery`，WebUI 投递记录是缺的。**建议**作为另一项独立整改：让所有 `send_message` 路径统一打 delivery 审计（可能下沉到 `api_bridge.send_message` 内部自动记录）。
- 本文档只锁定 memory 这条线；delivery 审计的统一留作后续 cleanup，不在此 scope。

### 11.8 voice-only 四种情况对照表（`speak` 视角）

| 模型行为 | 用户收到 | memory 记录 |
| ---- | ---- | ---- |
| `speak("你好")` + 文字「补充说明」 | 语音 + 文字 | `delivered_text` 投影「你好」+ assistant 文字「补充说明」 |
| `speak("你好")` + `NO_REPLY` | 仅语音 | `delivered_text` 投影「你好」（不会因 NO_REPLY 丢失） |
| `speak("你好")` 合成失败 → 降级 | 仅文字「你好」 | assistant 文字「你好」（正常 `final_response` 路径，无 `delivered_text`） |
| 文字「你好」（没调 speak） | 仅文字 | assistant 文字「你好」 |

四种情况 memory 都正确记录了「角色对用户说了你好」。

### 11.9 开放细节

- `delivered_text` 投影出的 turn 是否需要在 content 里加标记（如 `[语音] 你好呀`）方便人读 transcript？倾向：content 保持纯净原文，标记进 metadata（`{"spoken": true}`），WebUI 按 metadata 渲染语音气泡。
- dreaming consolidator 见到 `source="tool_utterance"` 时是否区别对待？首版不需要，统一当 assistant 发言处理（`metadata.tool` 仅作溯源）。
- 历史回放（恢复 session 上下文）时，spoken turn 作为普通 assistant 文本还原给 provider，不附带音频（音频在 workspace，需要时另行引用）。
- `delivered_text` 的字段名是否与 `media` 并列成一组「工具产出声明字段」？倾向是，未来可文档化为「tool return protocol」。


## 12. 实现阶段

### 阶段 1：文档冻结（本文档）

确认：方案 A（`speak` 工具）为主、C（`/voice` 自动 TTS）保留、B 不做；`speak` + `NO_REPLY` 作为 voice-only 协议；音色人设绑定、模型不可选；合成失败降级纯文字；spoken text 进 memory（§11）。

### 阶段 2：统一 TTS 层 + GPT-SoVITS adapter ✅ 已完成

新增 `nahida_bot/speech/`（核心模块）：

- `base.py`：`SpeechRequest` / `SpeechArtifact` / `TtsError` / `TtsProvider`(ABC)。
- `config.py`：`TtsConfig`（`backends`/`voices` raw dict，按 `type` 判别）+ `resolve_voice` / `backend_raw`。
- `service.py`：`SpeechService`（`_BUILTIN_PROVIDERS` registry + 按 `type` dispatch + 懒加载 client 缓存 + voice 解析 + 错误归因）。
- `providers/gpt_sovits.py`：`GPTSoVITSProvider(TtsProvider)` + `GPTSoVITSBackendConfig` + `GPTSoVITSVoice`。
  - `synthesize(request: SpeechRequest, voice_config: GPTSoVITSVoice) -> SpeechArtifact`，POST `{base_url}{tts_path}`，传 api_v2 参数（`text`/`text_lang`/`ref_audio_path`/`prompt_text`/`prompt_lang`/`media_type`/`text_split_method` 等，`streaming_mode=false` 写死）。
  - 响应分流：200→音频字节，400→JSON `{message}`，5xx/429/timeout→retryable。
  - 错误码：`tts_synthesis_failed`(400) / `tts_server_error`(5xx) / `tts_rate_limited`(429) / `tts_timeout` / `tts_transport_error` / `tts_bad_response` / `tts_empty_text` / `tts_missing_ref_audio` / `tts_bad_config` / `tts_unsupported_provider`（**无 `auth_failed`**，api_v2 不鉴权）。
  - GPT-SoVITS 不支持流式合成，调用为阻塞式。
- 测试：`tests/test_speech.py`（21 个，覆盖 config/adapter/service dispatch/错误归因）。
- pyright 0 errors / ruff clean / pytest 21 passed。

### 阶段 3：`speak` 工具与命令插件

新增 `nahida_bot/plugins/<speech>/`（薄插件，对照 `image_generation` 形态），调核心 `SpeechService`：

- `/speak` 命令（异步任务，合成后发送）
- `speak` 工具（schema 见 §6.1，无 `voice` 参数）
- persona → voice 名的解析（从 session/persona 上下文取 persona，查映射得到 voice 名，交 `SpeechService.synthesize(text, voice=...)`）
- `_resolve_output_dir` / `_build_filename`（落地 wav 到 workspace）
- 配额（`max_calls_per_24h`）、并发（`Semaphore`）、background task 管理
- 合成失败降级（§10.5）：失败时改发纯文字 + log
- 成功时返回值带 `delivered_text`（§6.2 / §11.3），供 SessionRunner 投影进 memory

注册到 `nahida_bot/core/app.py` 的内置插件列表与 `config_schema.py`。

### 阶段 4：通道层 audio 发送

- Telegram：`_send_attachment` 增加 `voice` / `audio` 分支；引入可选 `voice-encode` extra 做 wav→opus 转码。
- OneBot：outbound 构造 `record` 段。
- Milky：**已实现**（§8.3），本阶段无需工作。

### 阶段 5：memory 投影（§11，关键）

修改 `nahida_bot/core/session_runner.py`，新增对**统一话语约定**的投影（不硬编码 `speak`）：

- 在 `_assistant_visible_turns`（或新增 `_tool_delivered_utterances`）里，扫描本轮成功 tool results 的 JSON，提取 `delivered_text` 字段。
- 每条非空 `delivered_text` → `ConversationTurn(role="assistant", source="tool_utterance", metadata={"spoken": ..., "audio_path": ..., "persona_voice": ...})`。
- 失败降级的 speak 不带 `delivered_text`，不重复投影。

机制建成即对所有工具通用；`speak` 是首个调用方。`image_generate`/`send_local_attachment` 的接入可后续单独排期（§11.5），无需再改 SessionRunner。

测试矩阵覆盖 §11.8 四种情况。

### 阶段 6：media artifact 注册

待 `tool-produced-image-media-design.md` 的 `extract_media_artifacts()` 落地后，`speak` 工具返回的 `media: [{kind:"audio",...}]` 即被自动注册；本阶段只需确保返回格式符合协议，无需额外代码。

### 阶段 7：（可选）方案 C 会话级开关

实现 `/voice on|off`、`session.voice_mode`、router 层 `_maybe_voice_reply` 钩子。

### 阶段 8：测试

- 客户端：mock GPT-SoVITS 响应，覆盖成功/超时/各类错误。
- 插件：`speak` 工具的 `send=true/false`、配额超限、persona 音色解析、失败降级。
- 通道：Telegram `voice`/`audio` 分支、转码降级、OneBot/Milky 段构造。
- memory：§11.8 四种情况的 spoken turn 持久化与 dreaming。
- 集成：`speak` + `NO_REPLY` 的 voice-only 时序（验证 router 不抑制工具已发的附件，且 spoken text 进了 memory）。

## 13. 开放问题

已决定（关闭）：

- ~~分段流式合成~~ → **跳过**。GPT-SoVITS 不支持流式合成，且实现复杂；语音一定是文字完整后才合成发送。
- ~~合成失败处理~~ → **降级纯文字 + log**（§10.5）。
- ~~多条语音~~ → **后续支持**。本质是模型多次调用 `speak`，非首版阻塞项。
- ~~音色切换~~ → **不做运行时切换**。音色人设绑定（§6.4）。
- ~~语音附件进 memory~~ → **不进**；但 spoken text 进 memory（§11）。
- ~~方案 C 与流式文字~~ → GPT-SoVITS 本就不支持流式，方案 C 开启时该会话文字流式自然关闭。
- ~~`speak` 专属 memory 机制~~ → **统一为 `delivered_text` 约定**（§11.3），覆盖所有「工具替角色向当前会话送达话语」的场景，`image_generate`/`send_local_attachment` 后续可复用同一机制。

仍开放：

1. spoken turn 的 metadata 是否需要更丰富的回放信息（如音频 URL、时长），供 WebUI 历史展示语音气泡？倾向 metadata 里存 `audio_path`，WebUI 自行决定是否暴露播放器。
2. 同一人设是否需要「多情绪参考音频」（模型传 `emotion`，persona 配置内部映射到不同 ref_audio）？首版单参考音频，后续按需扩展。
3. `speak` 的 `max_text_length` 截断后，是返回截断警告还是静默截断？倾向返回警告让模型知情。
4. 多 `speak` 调用（未来）的发送顺序与合并转发：是否打包成一条多附件消息？倾向保持每条独立发送，保留时序。
5. `image_generate`/`send_local_attachment` 接入 `delivered_text` 与 `record_message_delivery` 审计的排期（§11.5 / §11.7）——独立于 `speak`，另行整改。

## 14. 结论

语音输出以 **`speak` 工具为主路径**，底层是**统一 TTS 层**（`nahida_bot/speech/`：`SpeechService` + `TtsProvider` registry），GPT-SoVITS api_v2 是首个 adapter，复用 media artifact 协议（`kind="audio"`）。「只发语音」通过 `speak` + 现有 `NO_REPLY` 哨兵达成，**不新增哨兵**。**音色由人设绑定（`tts.voices`），模型不可选**。合成失败自动降级纯文字。

**记忆持久化采用统一设计**（§11）：引入 `delivered_text` 返回值约定，SessionRunner 投影层据此把工具替角色送达当前会话的话语记为 assistant turn。`speak` 是首个调用方；该机制同样适用于 `image_generate`/`send_local_attachment`（后续整改），**不重复造轮子**。跨会话 `_tool_message` 维持 `record_session_event`（`role="system"`）路径，职责不混淆。

方案 C（后置自动 TTS）作为会话级可选开关保留设计，首版可不实现。通道层：**Milky 已支持 voice 出站**（§8.3）；Telegram `voice`/`audio` 分支（含 wav→opus 转码）与 OneBot `record` 段待补。Desktop 本地 TTS 与服务端 TTS 并存，附件优先。

分层职责：

- **模型**：决定何时说话、说什么；用 `NO_REPLY` 表达「无需文字」。（不决定音色）
- **persona → voice 映射**（`speak` 插件层）：从 session persona 解析出 voice 名。
- **`tts.voices` 配置**：voice → backend + 参考音频三元组（GPT-SoVITS）或其他 provider 的音色身份，部署时固定。
- **`speak` 工具（插件）**：落地、发送、配额、media payload、失败降级；成功时返回 `delivered_text`。
- **`SpeechService`（核心）**：按 `tts.backends.<name>.type` dispatch 到 provider adapter，懒加载 client，错误归因。
- **`GPTSoVITSProvider`（adapter）**：HTTP 调 api_v2、错误分类、阻塞合成。
- **SessionRunner（memory 投影）**：扫描 tool results 的 `delivered_text`，记为 assistant turn 进 memory（通用机制，非 `speak` 专属）。
- **通道**：把 `Attachment(type="audio"|"voice")` 翻译为平台原生语音消息。
- **router**：仅在方案 C 开启时介入，做后置 TTS。
- **media artifact registry**：统一登记工具产出的音频，供未来 audio-input 模型读取。
