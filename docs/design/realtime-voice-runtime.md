# 实时语音运行时设计（Desktop-first 级联方案）

> 状态：**基础契约落地中**。本设计固定平台无关的服务端 VoiceSessionCore、Desktop-first
> 验证、单参与者优先、实时媒体与消息事件分离等边界。首个产品实现采用深度流式级联；
> 原生全双工模型保留为对照实验和长期演进方向。

## 1. 背景与结论

现有语音能力面向“生成一条完整语音消息”：`SpeechService.synthesize()` 等待 Provider
返回完整 `SpeechArtifact`，Desktop 再经 `/api/speech/jobs` 下载完整音频播放。这条路径适合
QQ/Milky 语音消息、通知和非实时表现，但不适合可插话的语音会话。

实时交互的延迟不是单个 TTS 模型的问题，而是以下等待的总和：

1. 固定静音窗口判断用户说完；
2. ASR 等待整句定稿；
3. Agent Provider 把流式 delta 聚合成完整 response；
4. 工具调用和结果回填；
5. TTS 等待完整文本并生成完整文件；
6. Desktop 下载完整 blob 后才开始播放。

因此本设计作出四个决定：

- **实时主线采用深度流式级联**：流式 ASR → 增量 Agent → 稳定短语提交 → 长驻流式
  TTS。它保留现有 Agent、工具、权限、KB 和 memory。
- **GPT-SoVITS 不进入实时主链路**：继续作为高相似度音色基准、离线语音消息和降级
  Provider。
- **快速反射采用服务端决策、平台边缘执行**：VoiceSessionCore 统一决定 cue、延迟、冷却和
  turn 归属；Desktop、Discord 等输出适配器只播放预置/预加载的无事实音频并负责即时急停。
  反射不经过 Agent，也不写入正式对话 memory。
- **原生全双工模型是独立实验线**：PersonaPlex、MiniCPM-o 等不直接替换 Nahida Agent；
  若将来采用，优先作为实时交互外壳或受限快系统。

公开系统验证了两种路线的差异：OpenAI 将低延迟、barge-in 和自然 turn-taking 的主路径
定义为直接处理 live audio 的 speech-to-speech session；Moshi 使用用户/模型双音频流；
PersonaPlex 在此基础上加入文本角色提示和音频声音提示；MiniCPM-o 4.5 在统一会话中持续
写入音频状态，并由模型决定输出 listen/speak token。

参考：

- [OpenAI Voice agents](https://developers.openai.com/api/docs/guides/voice-agents)
- [Moshi](https://arxiv.org/abs/2410.00037)
- [PersonaPlex](https://arxiv.org/abs/2602.06053)
- [MiniCPM-o 4.5](https://arxiv.org/abs/2604.27393)
- [Full-Duplex-Bench](https://arxiv.org/abs/2503.04721)

## 2. 目标与非目标

### 2.1 首阶段目标

- Desktop 上完成单用户实时语音会话。
- 输入音频持续上传，ASR 提供 partial/final transcript。
- 普通无工具回答可以在 Agent 仍生成时开始合成和播放。
- 用户插话后立即停止本地播放，并取消旧 Agent/TTS turn。
- memory 只记录用户实际说完的文本和 Bot 实际播放完成的正式文本。
- persona 绑定固定 voice profile，支持声音复刻与版本化。
- 语音引擎、ASR、平台 transport 可替换。

### 2.2 暂不处理

- Discord/QQ 等平台 transport；Desktop 验收后再接 Discord。
- 多用户重叠语音和说话人仲裁。
- 让原生 speech-to-speech 模型直接拥有高风险工具权限。
- 训练新的基础全双工模型。
- 把 20ms PCM 帧发布为 `InboundMessage` 或普通 EventBus 事件。

## 3. 总体架构

```text
Desktop microphone
  │ WebAudio AEC / noise suppression / AGC
  │ 20 ms PCM frames
  ▼
Realtime Voice data plane
  ├── VAD / speech-start interruption
  ├── streaming ASR partial/final
  └── semantic endpointing
              │
              ▼
       VoiceTurnCoordinator
       ├── turn generation / cancellation
       ├── speculative run ownership
       └── session + episode correlation
              │ final or stable transcript
              ▼
         Nahida SessionRunner
       tools / memory / KB / auth
              │ provider deltas
              ▼
        SemanticCommitter
       stable phrase boundaries
              │ committed phrases
              ▼
       StreamingSpeechSession
       warm model + cached voice
              │ timestamped PCM chunks
              ▼
        Playback ledger
       short jitter/ring buffer
              │
              ▼
       VoiceOutputAdapter
        ├── Desktop AudioWorklet
        ├── Discord Opus queue
        └── future transports

Cross-platform reflex lane:
VoiceSessionCore ReflexCoordinator ──play/cancel command──> VoiceOutputAdapter
transport speech-start ──> stop locally first + notify VoiceSessionCore
```

## 4. 控制面与数据面

实时音频不能经过现有消息 EventBus。该总线适合低频、可等待的业务事件，不适合每 20ms
一个 frame 的有期限媒体流。

### 4.1 数据面

- 二进制音频帧；每帧携带 `voice_session_id`、`turn_id`、sequence、capture/playback PTS、
  sample rate 和 channels。
- 队列必须有界；过期音频应丢弃，不能通过无限缓存制造越来越大的延迟。
- 输入标准形态首版固定为 16kHz mono PCM16；输出内部形态优先 24kHz mono PCM16，再由
  transport 做平台需要的重采样/Opus 编码。
- Desktop 使用专用 AudioWorklet ring buffer；现有完整 blob `HTMLAudioElement` 路径继续
  服务非实时 TTS。

### 4.2 控制面

- JSON 事件：session open/close、speech started/stopped、partial/final transcript、Agent 状态、
  phrase committed、TTS started/completed、interrupt、reflex feedback、error。
- 所有异步结果必须携带 `turn_id`。Coordinator 只接受当前 turn 的结果；迟到结果静默丢弃。
- ReflexCoordinator 发出带 `command_id`、`turn_id` 和绝对 `expires_at_ms` 的语义化
  `play/cancel` 命令。适配器不得接收服务端文件路径；过期、重复或已取消 turn 的 play
  命令必须丢弃。
- Desktop 首版使用专用 WebSocket，例如：

```text
POST   /api/voice/sessions
WS     /api/voice/sessions/{voice_session_id}/stream
DELETE /api/voice/sessions/{voice_session_id}
```

现有 Gateway Node WebSocket 继续传控制和 artifact 引用，不承载高频音频二进制。

## 5. 两套正交状态机

Conversation Joiner 的状态描述“Bot 是否参与当前话题”：

```text
observing -> joining -> engaged -> cooling
```

实时媒体状态描述“当前一轮在做什么”：

```text
idle -> listening -> endpointing -> thinking -> speaking -> idle
                    \______________________/\
                         interrupted
```

二者不能合并。首阶段只共享：

- `session_id`：现有对话记忆/路由身份；
- `episode_id`：一次连续参与的话题范围；
- `turn_id`：一次用户发言到一次 Bot 正式输出的可取消运行代次。

Desktop 单用户语音会话开启后可直接进入 engaged；空闲超时进入 cooling，关闭会话回到
observing。未来 Discord 多用户实现可把 final ASR transcript 投影为观察消息，再复用 Joiner
的参与度、batch、Bot 占比和退出策略。`speech_started` 的媒体打断始终走快路径，不等待
Joiner/LLM 判断。

## 6. 深度流式级联

### 6.1 ASR 与端点

- 流式 ASR 持续输出可修订 partial；只有 final transcript 进入正式 memory。
- VAD 的 speech-start 负责抢话；speech-stop 只表示候选端点。
- 语义端点器结合 partial transcript、停顿和句法完整度确认 turn；固定静音超时仅作兜底。
- 可在高置信候选端点启动 speculative Agent run，但播放前必须确认 turn；用户继续讲话时
  取消旧 run。

### 6.2 Agent delta

现有 Provider 虽使用流式 HTTP，却在适配器内聚合完整 response。实时路径需要增加 provider
delta 契约，并让 AgentLoop 转发 `text_delta`、`tool_call_delta`、`response_done`。

工具安全规则：

- 尚未确认是否出现 tool call 的文本不得立即播放；
- 如果发生 tool call，丢弃未提交的普通文本；
- 工具执行期间只允许服务端授权的无事实反射音频；
- 工具结果回填后的正式回答才能进入 SemanticCommitter/TTS。

### 6.3 SemanticCommitter

不能逐 token 喂给 TTS。提交器应在自然标点、稳定短语长度、短时间无 delta 等条件下提交，
并保留尚未提交的尾部用于修订。每个提交生成单调递增 `segment_id`。

### 6.4 Streaming TTS

实时 Provider 必须支持长驻 session：

- session 开启时加载/缓存 voice profile；
- `push_text()` 接受已提交短语；
- 异步产生 `SpeechChunk`；
- `finish_input()` 正常收尾；
- `cancel()` 立即停止并使后续 chunk 失效；
- 实际输出长期必须维持 RTF < 1，不能积累无限 lag。

完整 artifact Provider 不自动冒充 streaming Provider；不支持实时接口时明确报错或走非实时
降级。

## 7. 跨平台快速反射

快速反射是表现优化，不是第二个会回答问题的 Agent。为保证 Desktop、Discord 和未来语音
平台行为一致，它拆成服务端决策与平台边缘执行两层。

### 7.1 服务端 ReflexCoordinator

VoiceSessionCore 是权威决策者：

- 根据当前 `turn_id`、Agent/tool 状态决定是否允许 cue；
- 只允许无事实承诺的 cue，例如 `acknowledge`、`thinking`、`checking`；
- 统一管理 thinking delay、跨 turn cooldown、命令 ID、过期时间和取消原因；
- 用户重新说话、正式 TTS 开始、turn 完成或 session 关闭时撤销 pending/active cue；
- 忽略不属于当前 turn 的迟到 terminal/feedback 事件；
- 反射不写入 conversation memory，但服务端记录统一 telemetry。

命令只表达语义，不包含文件路径或音频 URL：

```json
{
  "type": "play",
  "command_id": "reflex-42",
  "session_id": "voice-session-1",
  "turn_id": "turn-7",
  "cue": "thinking",
  "expires_at_ms": 1788000000500,
  "interruptible": true
}
```

如果命令在 deadline 前没有到达或本地音频没有预加载，适配器直接丢弃，不能临时发起慢
TTS 后补播。

### 7.2 VoiceOutputAdapter 本地执行

每个平台只负责媒体执行：

- Desktop 把 cue 映射到安装包或会话预热缓存里的 PCM/OGG，通过 AudioWorklet 播放；
- Discord voice gateway 把同一 cue 映射到预编码的 48kHz Opus；
- 每个执行器绑定一个 `voice_session_id`，拒绝来自旧会话或错误路由的命令；
- 适配器去重 `command_id`，拒绝过期命令和已取消 `turn_id`；
- 收到服务端 cancel 时停止对应 cue；
- 向服务端反馈 started/completed/dropped/cancelled，供 Coordinator 清理和 telemetry 使用。

打断是唯一允许“平台先斩后奏”的动作。平台侧 VAD 检测到 speech-start 后必须立即清空本地
播放队列、tombstone 当前 turn，再通知 VoiceSessionCore 取消 Agent/TTS 和修正播放账本；
不能为了统一决策而增加一次网络往返。

当前代码落点是服务端 `ReflexCoordinator` 和 Desktop `VoiceReflexExecutor`。后者不再持有
delay、cooldown 或 cue 选择策略，只消费服务端命令并提供本地急停。实际音频资产、预加载
缓存和 UI 在 Desktop voice session 接线阶段补齐。

## 8. 声音复刻与 VoiceProfile

VoiceProfile 是部署者绑定到 persona 的受信配置，模型不得指定任意参考音频路径：

```yaml
voices:
  nahida:
    backend: cosyvoice3
    profile_version: 1
    checkpoint: nahida-v1
    references:
      neutral: voice/nahida/neutral.wav
      bright: voice/nahida/bright.wav
      soft: voice/nahida/soft.wav
      serious: voice/nahida/serious.wav
```

实时主线优先评估 CosyVoice 3 或后续能满足流式契约的中文 voice-conditioned 模型。
GPT-SoVITS/IndexTTS 保留为离线 A/B 质量基线。PersonaPlex 的“text role + audio voice”是
长期架构参考，但当前公开模型仅英文且硬件要求高。MiniCPM-o 4.5 更接近中文目标，但不应在
没有专用 GPU、工具隔离和场景评测前替换现有 Agent。

声音素材必须有明确授权和来源记录；checkpoint、参考音频、清洗流程和评测集均需版本化。

## 9. 播放账本与 memory

每个正式语音 segment 记录：

- `turn_id` / `segment_id`；
- 文本 span；
- 音频起止 sample/PTS；
- queued、started、played、cancelled 状态。

发生 barge-in 时：

1. 当前 VoiceOutputAdapter 立即清空 ring buffer/Opus queue；
2. Gateway 取消当前 Agent/TTS turn；
3. 只提交完整播放的正式 segment 为 assistant spoken turn；
4. 未播放和只播放一部分的 segment 标记 truncated，不进入普通 assistant memory；
5. 任何平台的反射 cue 永不进入正式 memory。

## 10. 延迟预算和验收指标

首个工程目标，不作为模型能力承诺：

- 用户 speech-start 到 Bot 停止播放：p95 ≤ 200ms；
- 无工具短回答，用户端点确认到首个正式音频：p95 ≤ 1s；
- 播放预缓冲保持在数百毫秒级，禁止持续增长；
- cancel 后旧 `turn_id` 音频不得重新出现；
- final transcript、实际播放文本与 memory 一致；
- 连续五分钟测试无累计音画/音频 lag；
- 记录 VAD、endpoint、ASR final、Agent TTFT、首 phrase、TTS first chunk、first playback、
  interrupt-stop 等分段指标。

## 11. 分阶段实施

### Phase 0：基础契约（当前）

- [x] 固化本设计与架构边界。
- [x] 定义流式 ASR frame/event/session/provider 契约。
- [x] 定义流式 TTS request/chunk/session/provider 契约。
- [x] 为 SpeechService 增加可选 streaming provider dispatch。
- [x] 定义可丢弃迟到结果的 VoiceTurnCoordinator。
- [x] 定义服务端跨平台 ReflexCoordinator 与 play/cancel 命令。
- [x] 用 RealtimeVoiceSession 原子编排 turn 与 reflex 状态。
- [x] 定义 Desktop VoiceReflexExecutor（预加载执行、过期丢弃、本地急停）。

### Phase 1：Desktop Voice Lab

- [ ] 麦克风权限、WebAudio capture、AEC/NS/AGC。
- [ ] AudioWorklet 输入重采样和输出 ring buffer。
- [ ] 专用 voice session WebSocket。
- [ ] mock ASR/TTS 回环与延迟面板。
- [ ] 本地反射音频资产、预加载、服务端命令接线和设置项。

### Phase 2：首个真实级联

- [ ] FunASR/sherpa-onnx 流式 ASR Provider。
- [ ] 语义端点器。
- [ ] Agent provider delta 和 SemanticCommitter。
- [ ] CosyVoice 或等价 StreamingTtsProvider。
- [ ] 播放账本与 spoken-memory 截断。

### Phase 3：平台与研究线

- [ ] Discord DAVE/Opus voice gateway。
- [ ] Discord VoiceReflexExecutor（预编码 Opus + 本地 speech-start 急停）。
- [ ] conversation joiner 的单/多人参与策略接入。
- [ ] MiniCPM-o 4.5 / PersonaPlex 对照评测。
- [ ] 评估“原生 duplex 快系统 + Nahida Agent 慢系统”的双系统方案。
