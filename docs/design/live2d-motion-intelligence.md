# Live2D 动作智能层设计

> 记录时间：2026-07-09
> 状态：训练前基础设施已落地，进入本地数据积累阶段
> 相关议题：GitHub Issue #9 Live2D 桌宠与桌面端软件
> 相关文档：
>
> - [desktop-app.md](desktop-app.md) — Desktop App 与 Live2D 桌宠产品形态
> - [gpt-sovits-voice.md](gpt-sovits-voice.md) — 语音输出与 TTS 管线

## 1. 背景

当前 Desktop / Live2D 桌宠已经有基础运行时状态、DisplayPlan、Live2D 模型映射、TTS 播放、表情和动作预览能力。接下来一个关键问题是：如何让桌宠在回复文本时自然表演，而不是依赖脆弱的字符串模式匹配或让后端大语言模型直接输出低层动画参数。

现有模式匹配的问题很明确：

- 后端 LLM 的文本稍微变化，就可能匹配不到正确动作。
- 模板只覆盖已知短语，无法泛化到新的表达方式。
- 动作选择和动作曲线生成混在一起，难以缓存、评估和训练。
- 如果未来接入不同后端模型，每个模型的输出风格都可能破坏匹配规则。

我们希望保留“AI 驱动”的方向：后端 LLM 只负责自然回复内容，本地桌宠根据文本、语境和当前状态自行决定如何表演。这样既能降低对后端结构化输出的依赖，也能让桌宠行为更像一个有连续状态的角色。

这份文档把近期关于 HY-Motion、LPM、强化学习小模型、缓存、奖励信号和数据采集的讨论收敛成一套可落地设计。

## 2. 核心结论

近期不建议追求 HY-Motion / LPM 级别的大模型复现，也不建议一开始用强化学习直接训练端到端动画生成器。

更适合当前项目的路线是：

```text
assistant text + runtime state
  -> local semantic motion planner
  -> MotionIntent / timing / intensity
  -> motion cache
  -> primitive-based synthesizer
  -> validator / ranker
  -> normalized pose timeline
  -> Live2D model retargeting
  -> renderer
```

其中：

- **后端 LLM** 只负责回复文本，不承担低层动画控制。
- **本地 MotionPlanner** 负责把文本和状态转成结构化动作意图。
- **MotionSynthesizer** 用参数化 motion primitives 生成曲线。
- **MotionCache** 让常见表达稳定复用，减少本地推理成本。
- **MotionValidator** 负责硬约束、安全边界和平滑性。
- **MotionTelemetry / PreferenceStore** 从第一版开始收集未来训练所需数据。
- **小模型 / RL** 是后续增强项，不是首版阻塞项。

一句话：先把动作系统做成“会产生日后可训练数据的可控 pipeline”，再逐步把规则、检索、ranker、小 LLM、curve refiner 和 RL skill policy 接进去。

## 3. 研究参考的定位

### 3.1 HY-Motion

HY-Motion 1.0 是 text-to-3D human motion 方向。它生成的是 3D 人体动作序列，不是 Live2D 参数曲线。其公开流程大致包括大规模动作数据清洗、统一骨架表示、VLM/LLM caption、动作 taxonomy、DiT / Flow Matching 动作生成器、高质量数据微调和偏好/奖励优化。

对我们短期直接接入价值有限：

- 目标表示不同：SMPL-H / 3D skeleton 与 Live2D 参数空间不同。
- 训练和推理成本偏大，不适合本地桌宠实时依赖。
- 解决的是“文本生成全身动作”，不是“对话角色的低成本实时表演”。

但它值得借鉴：

- canonical motion representation
- 动作 taxonomy
- prompt rewrite + duration prediction
- scale-then-refine 的数据路线
- validator / metrics / preference optimization

### 3.2 LPM

LPM 更接近角色表演系统：speaking、listening、idle、长时间稳定、角色一致性、backbone + refiner、偏好优化和 benchmark。

对我们有启发，但不应照搬：

- 桌宠不一定做双工语音交互。
- 本地桌宠不适合依赖大参数量模型。
- 如果目标只是“LLM 输出文本，bot 自己表演”，LPM 式视频/角色大模型过重。

可借鉴的系统思想：

- 不只做 speaking，也要有 idle / transition / optional attention state。
- 长时间桌宠需要动作记忆，不能每句话重新随机。
- backbone + refiner 可以映射为 MotionPlanner + MotionSynthesizer + MotionValidator。
- A/B preference 数据比绝对打分更适合优化“自然感”。

### 3.3 游戏 AI 的高层 planner + 低层 skill policy

米哈游内部提到的方向可以概括为 hierarchical control：

```text
大模型 / planner:
  做高层决策，例如“开车过去”“先打 A 再打 B”

强化学习小模型 / skill policy:
  做低层闭环控制，例如方向盘、油门、闪避、技能释放
```

迁移到 Live2D 桌宠：

```text
LLM / local planner:
  这句话应该表现为“思考后解释”

skill controller / synthesizer:
  具体 headX/headY/bodyZ/eyeX/mouthOpen 如何连续变化
```

但 Live2D 与游戏不同：游戏有天然 reward，例如到达目标、击杀敌人、少掉血；Live2D 的“自然、可爱、不过度、语义匹配”更主观。因此我们应先用监督学习、偏好学习和硬规则指标，后期再考虑 RL。

## 4. 目标与非目标

### 4.1 目标

- 用本地动作智能层替代脆弱的文本模式匹配。
- 让后端 LLM 可以只输出自然文本，桌宠本地决定表演。
- 定义稳定的 `MotionIntent`、`MotionPlan`、`NormalizedPoseFrame` 等中间表示。
- 用参数化 motion primitives 解决非动画师缺少手 K 数据的问题。
- 从第一版开始采集可训练数据，包括决策、执行、偏好和失败样本。
- 为未来接入 embedding/ranker、小 LLM、ONNX curve refiner、RL skill policy 预留接口。
- 保持运行时安全可控：所有低层曲线都必须经过 validator、priority mixer 和 model profile 限制。

### 4.2 非目标

- 不在首版训练端到端动作生成大模型。
- 不让 LLM 直接输出逐帧 Live2D 参数曲线。
- 不要求双工语音交互作为前置条件。
- 不用在线 VLM/LLM judge 直接作为实时 RL reward。
- 不把 Live2D renderer 变成动作决策层。renderer 只执行已经验证过的 motion timeline。
- 不承诺任意 Live2D 模型自动有高质量复杂动作。能力仍受模型参数、表情文件和 motion 文件限制。

## 5. 概念与边界

### 5.1 MotionIntent

`MotionIntent` 是语义层动作意图。它不包含具体 Live2D 参数名。

```ts
export interface MotionIntent {
  id: string;
  source: "rule" | "embedding" | "llm" | "cache" | "manual";
  intent:
    | "idle"
    | "greet"
    | "thinking"
    | "explain"
    | "agree"
    | "deny"
    | "surprised"
    | "concerned"
    | "celebrate"
    | "apology"
    | "error"
    | "retreat"
    | "emerge";
  emotion:
    | "neutral"
    | "happy"
    | "thinking"
    | "worried"
    | "surprised"
    | "error"
    | "offline";
  communicativeAct?:
    | "answer"
    | "ask"
    | "confirm"
    | "reject"
    | "search"
    | "warn"
    | "comfort";
  durationMs: number;
  intensity: number; // 0..1
  gaze?: "user" | "down-left" | "down-right" | "side" | "none";
  loopable: boolean;
  interruptible: boolean;
  priority: "idle" | "background" | "speech" | "state-transition" | "critical";
  tags?: string[];
}
```

### 5.2 MotionPlan

`MotionPlan` 是可执行但仍模型无关的 timeline。它可以引用 primitives，也可以包含 normalized pose keyframes。

```ts
export interface MotionPlan {
  id: string;
  intent: MotionIntent;
  createdAt: string;
  durationMs: number;
  segments: MotionPlanSegment[];
  validationWarnings: MotionValidationWarning[];
  telemetryId?: string;
}

export type MotionPlanSegment =
  | {
      type: "primitive";
      name: string;
      atMs: number;
      durationMs: number;
      params: Record<string, number | string | boolean>;
    }
  | {
      type: "pose-keyframes";
      atMs: number;
      durationMs: number;
      keyframes: NormalizedPoseKeyframe[];
    }
  | {
      type: "expression";
      atMs: number;
      expressionKey: string;
      blendMs?: number;
    };
```

### 5.3 NormalizedPoseFrame

`NormalizedPoseFrame` 是跨 Live2D 模型的 canonical pose。它不直接使用 `ParamAngleX` 等模型私有参数。

```ts
export interface NormalizedPoseFrame {
  atMs: number;
  headYaw: number; // -1..1
  headPitch: number;
  headRoll: number;
  bodyYaw: number;
  bodyPitch: number;
  bodyRoll: number;
  gazeX: number;
  gazeY: number;
  browUpLeft: number;
  browUpRight: number;
  eyeOpenLeft: number;
  eyeOpenRight: number;
  mouthOpen: number;
  mouthSmile: number;
  breath: number;
  energy: number;
}
```

### 5.4 ModelPerformanceProfile

每个 Live2D 模型不只需要参数映射，还需要表演边界。

```ts
export interface ModelPerformanceProfile {
  modelId: string;
  poseParameterMap: Record<keyof NormalizedPoseFrame, string[]>;
  expressionMap: Record<string, string[]>;
  motionMap: Record<string, unknown>;
  intensityScale: number;
  maxVelocity: Partial<Record<keyof NormalizedPoseFrame, number>>;
  maxAcceleration: Partial<Record<keyof NormalizedPoseFrame, number>>;
  preferredIdleEnergy: number;
  forbiddenCombos: Array<{
    expression?: string;
    primitive?: string;
    reason: string;
  }>;
}
```

## 6. 运行时架构

建议新增以下边界：

```text
PresentationSegment / assistant text / runtime state
        │
        ▼
MotionPlanner
        │
        ├── RuleMotionPlanner
        ├── EmbeddingMotionPlanner
        └── LocalLlmMotionPlanner
        │
        ▼
MotionCache
        │
        ▼
MotionSynthesizer
        │
        ├── PrimitiveMotionSynthesizer
        └── LearnedResidualSynthesizer
        │
        ▼
MotionDriver
        │
        ├── RuleMotionDriver
        └── NativeOnnxMotionDriver
        │
        ▼
MotionValidator
        │
        ▼
MotionScheduler / PriorityMixer
        │
        ▼
Live2DRetargeter
        │
        ▼
Live2DRenderer
```

`MotionDriver` 是生成短时 `NormalizedMotionClip` 的运行时端口，不替代 planner、validator、scheduler 或 renderer。首版由规则驱动实现；未来 ONNX 驱动应保持同一输入输出协议，并继续经过后续硬校验与混合。

### 6.1 MotionPlanner

职责：

- 从文本、DisplayPlan、runtime state、最近动作历史中生成 `MotionIntent`。
- 首版可以是规则 + embedding retrieval。
- 后续可以接 Qwen3-0.6B / 1.7B LoRA 或其他本地小模型。

输入建议：

```ts
export interface MotionPlannerInput {
  assistantText: string;
  segmentIndex: number;
  totalSegments: number;
  displayEmotion?: string;
  runtimeStatus: string;
  currentPoseSummary: Record<string, number>;
  previousIntent?: MotionIntent;
  recentIntents: MotionIntent[];
  speechDurationEstimateMs?: number;
}
```

### 6.2 MotionCache

缓存不是单纯性能优化，而是行为稳定器。常见表达应该有稳定表演，不应每次随机成完全不同动作。

缓存 key 可以包含：

- 文本 embedding 或 canonicalized text
- runtime status
- display emotion
- recent intent context
- model id / model profile version
- planner version

示例：

```text
sha256(
  textEmbeddingBucket
  + runtimeStatus
  + emotion
  + recentIntentSummary
  + modelProfileVersion
  + plannerVersion
)
```

缓存 value 保存 `MotionIntent` 或已验证的 `MotionPlan`。如果模型能力、primitive 版本、validator 版本变化，应主动失效。

### 6.1.1 已实现：服务端 LLM motion planner（Python core）

除桌面本地 planner（Rule / Embedding / LocalLlm）外，仓库已实现**服务端
motion planner**：Python core 在 Agent 回复生成后，调用一个低成本 LLM 分析
回复文本，为每一句生成情绪 / 动作 / 语音风格，产出与桌面端
`displayPlan.ts` 兼容的 wire 格式，随 `agent.message.completed` 事件转发给
Desktop Node。任何失败（超时、解析错误、模型不可用）都返回中性计划，**不会
阻塞回复**。

- 代码：`nahida_bot/agent/motion_planner.py`（`LLMMotionPlanner`，按
  ModelRouter 解析 `cheap` 标签）、`nahida_bot/agent/motion_plan.py`。
- 枚举：情绪 `neutral | happy | thinking | worried | error | offline`；
  动作 `idle | nod | point | wave | notify | speaking | emerge | retreat`；
  语音风格 `neutral | bright | calm | soft`。上限：≤ 12 段、单段 pause ≤
  3000ms、单段文本 800 字符、总 4000 字符。
- 配置（顶层 `motion_planner:`）：

  | 键 | 类型 | 默认值 | 说明 |
  |----|------|--------|------|
  | `enabled` | `bool` | `false` | 是否启用 |
  | `model_tag` | `str` | `"cheap"` | ModelRouter 解析的模型标签 |
  | `timeout_seconds` | `float` | `15.0` | 单次规划超时（1–60） |

- 作用域：仅在回复路由为 `node:...`（Desktop node 路径）时生效；
  `outbound.extra["display_plan"]` 由 `node_event_bridge.py` 挂到
  `agent.message.completed` 事件的 `display_plan` 字段。普通聊天频道不受影响。

### 6.3 MotionSynthesizer

职责：

- 把 `MotionIntent` 变成 `MotionPlan`。
- 首版基于参数化 motion primitives。
- 后续 learned model 只做 residual / refiner，而不是完全替代模板。

建议首批 primitives：

- `idle-breathe`
- `blink`
- `small-nod`
- `shake`
- `glance-left`
- `glance-right`
- `think-loop`
- `explain-small`
- `happy-bounce`
- `surprised-pop`
- `sad-drop`
- `celebrate`
- `emerge-follow-through`
- `retreat-follow-through`

每个 primitive 是函数，而不是固定 clip：

```ts
generateNod({
  durationMs,
  intensity,
  repeat,
  startPose,
  phaseOffset,
});
```

### 6.4 MotionValidator

职责：

- 检查参数范围、速度、加速度、jerk。
- 检查口型和 speech/lip-sync 是否冲突。
- 检查状态机阶段是否允许该动作。
- 检查动作是否过强、过频、不可中断。
- 产生 warnings，并在必要时降级到 safe plan。

首版 validator 应先实现硬规则，不依赖 AI judge。

### 6.5 PriorityMixer

不同动作源会同时控制同一维度：

- lip-sync 控制 `mouthOpen`
- expression 控制表情文件
- idle breathing 控制 `breath/bodyPitch`
- speech gesture 控制 `head/body/gaze`
- state transition 控制 emerge/retreat
- debug override 控制手动参数

需要明确优先级：

```text
debug override
  > state-transition
  > safety/error
  > lip-sync mouthOpen
  > speech gesture
  > expression pose hints
  > idle/background motion
```

renderer 不应自行判断这些语义，应该只执行混合后的结果。

## 7. 数据采集设计

如果未来要训练小模型，现在就必须开始采集可训练数据。重点不是马上训练，而是避免以后只有不可用的零散日志。

所有采集默认本地保存，提供开关、清空、导出。文本可能包含隐私，不应默认上传。

建议路径：

```text
desktop/data/motion-dataset/
  decisions.jsonl
  executions.jsonl
  preferences.jsonl
  invalid.jsonl
```

### 7.1 决策样本

用于训练 `text/context -> MotionIntent`。

```json
{
  "schemaVersion": 1,
  "type": "motion_decision",
  "timestamp": "2026-07-09T00:00:00.000Z",
  "assistantText": "我先查一下这个问题，可能要花几秒。",
  "conversationState": "speaking",
  "petRuntimeStatus": "chat",
  "activeEmotion": "thinking",
  "selectedIntent": {
    "intent": "thinking",
    "emotion": "thinking",
    "durationMs": 1800,
    "intensity": 0.42,
    "gaze": "down-left"
  },
  "source": "rule",
  "modelId": "nahida-default",
  "plannerVersion": "rule-v1",
  "cacheHit": false
}
```

### 7.2 执行样本

用于调试 synthesizer、validator 和未来 curve/refiner 训练。

```json
{
  "schemaVersion": 1,
  "type": "motion_execution",
  "timestamp": "2026-07-09T00:00:01.000Z",
  "decisionId": "decision-uuid",
  "motionPlanId": "plan-uuid",
  "intent": {},
  "timelineSummary": {
    "durationMs": 1800,
    "segmentCount": 4,
    "primitiveNames": ["glance-down-left", "think-loop", "small-nod"]
  },
  "validationWarnings": [],
  "fallbacks": [],
  "runtimeMetrics": {
    "maxVelocity": 0.42,
    "maxAcceleration": 0.31,
    "lipSyncConflictCount": 0
  }
}
```

### 7.3 偏好样本

偏好数据最值钱，未来可训练 ranker / reward model。

```json
{
  "schemaVersion": 1,
  "type": "motion_preference",
  "timestamp": "2026-07-09T00:00:03.000Z",
  "assistantText": "我先查一下这个问题，可能要花几秒。",
  "candidateA": "plan-a",
  "candidateB": "plan-b",
  "winner": "plan-a",
  "labels": ["more_natural", "better_timing"],
  "notes": "B 的点头太频繁。"
}
```

反馈 UI 首版不需要复杂：

- good
- bad
- too much
- too little
- wrong emotion
- repetitive
- A/B 选择

### 7.4 失败样本

记录 planner 输出非法、validator 降级、模型不支持参数等问题。

```json
{
  "schemaVersion": 1,
  "type": "motion_invalid",
  "timestamp": "2026-07-09T00:00:04.000Z",
  "assistantText": "...",
  "reason": "validator_rejected",
  "details": {
    "violations": ["mouthOpen_conflict", "maxVelocity_exceeded"]
  },
  "fallbackPlan": "safe-idle"
}
```

### 7.5 日常使用与反馈闭环

用户不需要查看或手写 JSONL。长期采集按下面的交互完成：

1. 在 `Settings -> Motion Data` 开启本地采集。
2. 正常使用桌宠；真实 pet window 是 Tauri 模式下唯一自动写 decision/execution 的 surface，主窗口镜像和 Workbench preview 不重复入库。
3. 在 Runtime 的 `Rate the last motion` 或交互态桌宠窗口点击 Good、Bad、Too much、Too little、Wrong emotion、Repetitive。
4. 对 Bad/Wrong emotion 可补充正确 intent、emotion 和 intensity；反馈会直接关联原始 `motionPlanId`。
5. 误操作使用 Undo；底层追加 retraction 记录，不破坏 JSONL 的 append-only 特性，审计和训练准备器只统计仍有效的反馈。
6. 在 Workbench 的 `Recent Motion Replay` 选择最近 20 条真实记录，使用 Older/Newer 或下拉框重播，再进行反馈。

Workbench 手动 primitive 预览和 replay 本身不会产生新的训练执行记录，避免一次真实回复被多个 Live2D stage 重复计数。Web-only 开发模式没有独立 pet window，因此 Runtime stage 会作为采集 surface。

当前闭环采集的是可追溯、可重播的 normalized procedural motion；模型自带 motion 只能按 group/index 播放，渲染器尚不能提取其原始参数曲线，因此暂不进入曲线训练集或 Recent Motion Replay。

## 8. 小模型路线

### 8.1 Embedding retrieval + ranker

第一阶段最推荐。

做法：

- 为每个 intent/primitive 准备文本描述和示例句。
- 用 embedding 找语义相似候选。
- 用轻量 ranker 结合 runtime state、emotion、最近动作选择最终 intent。

优点：

- 比字符串模式匹配稳。
- 数据需求低。
- 容易解释和调试。
- 失败时可以回退到 rule planner。

### 8.2 小 LLM planner

第二阶段可考虑 Qwen3-0.6B / 1.7B LoRA 或同类小模型。

适合任务：

- 输出结构化 `MotionIntent`
- 判断语气、动作意图、时长、强度
- 对不同后端 LLM 的自然文本做泛化

不适合任务：

- 直接输出 30fps 曲线
- 直接控制模型私有 Live2D 参数
- 直接承担实时低层闭环控制

训练数据来自 `decisions.jsonl` 和人工修正样本。可以先 SFT，再用偏好数据做 DPO 或训练 ranker。

### 8.3 Curve residual / refiner

第三阶段再做。

输入：

- `MotionIntent`
- 当前 pose
- primitive 生成的 baseline curve
- speech energy / duration / phase

输出：

- normalized pose residual
- timing offset
- intensity correction
- primitive variant selection

这比从零生成整段曲线安全，因为模板保证基本可用，模型只做润色。

### 8.4 RL skill policy

RL 放到后期。它适合闭环低层控制，不适合首版语义动作选择。

如果未来做 RL，policy 大致是：

```text
state:
  currentPose
  targetIntent
  phase
  speechEnergy
  previousVelocity
  modelProfile

action:
  headYawDelta
  headPitchDelta
  bodyRollDelta
  gazeDelta
  mouthFormDelta
```

但它需要稳定环境、reward、simulator/replay harness 和偏好模型。没有这些前置条件，直接 RL 很容易学出抖动、夸张或 reward hacking 的动作。

## 9. 奖励信号与 RLHF

### 9.1 不建议直接用在线 VLM/LLM reward

VLM/LLM 可以辅助标注，但不应在首版直接作为在线 RL reward：

- 成本高，无法本地低延迟运行。
- 判断噪声大，同一动作可能多次评分不一致。
- 容易偏好更显眼、更大幅度的动作，导致桌宠变吵。
- 容易 reward hacking，例如为了“看起来动作明显”而过度摆动。

更稳的方式：

```text
硬规则指标
+ 程序化质量指标
+ 人类偏好数据
+ VLM/LLM 弱标注
-> 训练本地 reward model / ranker
-> 用 ranker 选择候选
-> 后期再用于 RL 或 DPO
```

### 9.2 reward 类型

#### 硬约束

必须直接计算：

- 参数不越界
- 速度不过大
- 加速度 / jerk 不过大
- 起止 pose 不跳变
- 不和 lip-sync 抢 `mouthOpen`
- emerge/retreat 阶段不播放大动作
- debug override 不被低层动作覆盖

#### 程序化质量指标

可以直接计算：

- smoothness
- energy 是否接近 intent intensity
- duration 是否匹配目标时长
- loopable idle 是否可无缝衔接
- 重复度是否过高
- motion diversity 是否合理

#### 语义匹配

可以来自 classifier、ranker、人类偏好或 VLM 弱标注：

- thinking 文本是否使用 thinking 动作
- apology 是否避免 celebrate
- error 是否避免 happy
- explanation 是否不过度兴奋

#### 人类偏好

最可靠，但数量少。适合 A/B 数据：

- A 比 B 更自然
- A 动作太多
- B 表情不对
- C 可爱但太吵

### 9.3 示例 reward 组合

如果未来真的训练 RL policy，可从如下 reward 开始：

```text
reward =
  1.0 * semantic_match
+ 0.8 * preference_model_score
+ 0.5 * smoothness
+ 0.3 * timing_match
+ 0.3 * controlled_diversity
- 1.0 * constraint_violation
- 0.8 * lip_sync_conflict
- 0.5 * excessive_motion
- 0.4 * repetition
- 0.4 * state_machine_conflict
```

约束类惩罚应强于美学类奖励。否则模型会为了显眼动作破坏运行时安全边界。

### 9.4 RLHF 的现实路径

标准 RLHF 路线：

```text
生成多个候选动作
-> 人类偏好选择
-> 训练 reward model
-> PPO / GRPO / 其他 RL 优化 policy
```

对我们近期更现实的是：

```text
生成多个候选 MotionPlan
-> 人类 A/B 选择
-> 训练 ranker
-> 运行时用 ranker 选最优候选
```

这已经能改善自然感，而且比真正 RL 稳定得多。

## 10. 评估与 Benchmark

需要建立一个小型 Live2D Motion Bench，不等训练模型时才做。

### 10.1 场景集

建议固定 50-100 条测试文本：

- greeting
- thinking/searching
- explanation
- agreement/disagreement
- error/apology
- surprised
- long answer
- short reply
- idle for 10 minutes
- repeated similar replies

### 10.2 指标

硬指标：

- 参数越界率
- 最大速度 / 加速度 / jerk
- lip-sync conflict count
- plan generation latency
- renderer frame stability
- cache hit rate
- fallback rate

主观指标：

- semantic match
- naturalness
- not annoying
- character consistency
- transition smoothness
- long idle non-repetition

### 10.3 回放工具

需要一个 motion replay harness：

- 输入 `MotionPlan` 或 JSONL 样本。
- 在固定模型上回放。
- 导出参数曲线和短视频/GIF。
- 支持 A/B 对比。
- 支持给样本打标签并写回 `preferences.jsonl`。

没有 replay harness，偏好数据很难稳定积累。

当前进度（2026-08-12）：

- [x] 固定了 50 条中英文语义场景，覆盖 greet、apology、deny、agree、surprised、celebrate、concerned、thinking、explain、idle 与强 emotion override。
- [x] `MotionPlan` 可以编译到 normalized clip，并按固定采样率确定性回放。
- [x] 回放会计算越界数、最大速度、最大加速度和最大 jerk。
- [x] 本地数据审计会检查 schema、decision/execution/preference 关联、intent 覆盖和最低样本门槛。
- [ ] 固定 Live2D 模型的视频/GIF 导出和双候选并排 A/B 回放仍待有候选生成器后实现。

## 11. MVP 落地路线

### Phase 0：接口与 schema

- 定义 `MotionIntent`、`MotionPlan`、`NormalizedPoseFrame`。
- 定义 `ModelPerformanceProfile`。
- 定义 `MotionPlanner`、`MotionSynthesizer`、`MotionValidator`、`MotionCache`、`MotionTelemetry` 接口。

当前进度（2026-08-12）：

- [x] 已定义 `MotionIntent`、`NormalizedPoseFrame`、`NormalizedMotionClip` 和 canonical channel range。
- [x] 已定义异步 `MotionDriver` 边界，输入包含 phase、previous pose、audio energy、lookahead 和 runtime context。
- [x] 已增加 `DisplayPlan` 兼容适配器，同时保留 legacy `DisplayMotion` 与新的语义 intent。
- [x] 已定义 `MotionPlan`、`ModelPerformanceProfile` 以及 planner/synthesizer/validator/mixer/cache/telemetry/preference 接口。
- [x] `PrimitiveMotionSynthesizer` 会生成带版本信息的模型无关 plan；plan 可重新编译为 normalized clip。
- [x] 模型 performance profile 支持参数映射、强度、速度、加速度、idle energy、禁止组合、本地持久化与 Workbench 校准。
- [x] 现有 procedural motion 已迁移到 `RuleMotionDriver`；renderer 只消费 normalized clip 并负责参数重定向。

验收：

- 现有 procedural motion 能通过新接口播放。
- renderer 不负责语义决策。

### Phase 1：Rule planner + primitive synthesizer

- 把当前 base motions 改造成 primitives。
- 支持 duration/intensity/repeat/startPose 参数化。
- 加 validator 硬规则。

当前进度（2026-08-12）：

- [x] 原有 nod/point/wave/notify/speaking/emerge/retreat 已迁移为参数化 primitives。
- [x] 已扩充为 18 个 primitives，包括 idle-breathe、blink、左右 glance、shake、think-loop、explain-small、surprised-pop、sad-drop、happy-bounce 和 celebrate。
- [x] 已支持 duration、intensity、repeat、startPose，并可从中断中的当前 pose 平滑接续。
- [x] Debug 面板可直接枚举和播放 procedural primitives。
- [x] `RuleMotionPlanner` 已覆盖当前 13 类语义 intent，并通过固定 50 场景基准。
- [x] validator 已实现范围修正、时间线补帧、模型强度缩放、速度/加速度限制、禁止组合与 safe-idle 降级。
- [x] priority mixer 已实现 debug > state-transition > safety > lip-sync > speech > expression > idle 的逐通道优先级。
- [x] Gateway TTS 已接 Web Audio RMS envelope；无法获取真实音频时保留 pulse lip-sync 降级。

验收：

- 当前 13 个 canonical intent 在固定 50 场景基准中都能选择并播放；新增 taxonomy 必须先补 benchmark case。
- 参数不过界、无明显跳变。

### Phase 2：Telemetry 与 preference

- 写本地 JSONL。
- Debug/Workbench 增加 good/bad/too much/wrong emotion/repetitive。
- 支持 A/B candidate 记录。

当前进度（2026-08-12）：

- [x] Tauri app-data 下按 decisions/executions/preferences/invalid 分文件追加 JSONL；Web 模式使用有上限的 localStorage fallback。
- [x] 每条执行记录包含 planner/profile/synthesizer/driver/validator/mixer 版本、完整 `MotionPlan`、normalized clip、warnings 和 fallback 状态。
- [x] 支持 good/bad/too much/too little/wrong emotion/repetitive 单候选反馈，记录能关联到真实 `motionPlanId`。
- [x] 正常 Runtime 和交互态 pet window 均提供最近动作反馈、保存状态、纠正标签和 append-only Undo；不再要求进入 Debug。
- [x] Workbench 可载入最近 20 条持久化动作，选择 Older/Newer、重播 normalized clip 并对原始 plan 反馈。
- [x] Tauri 只由真实 pet surface 自动采集；Runtime 镜像与 Workbench preview 禁止重复写入。Web 开发模式则由 Runtime 代替真实 surface。
- [x] 设置页提供采集开关、分类计数、训练门槛、导出和清空；关闭后 telemetry 与 preference 均不会写入。
- [x] 数据审计门槛当前设为 500 decisions、100 preferences、8 类 intent、99% 合法记录及 95% execution/preference 关联率。
- [x] `python -m scripts.prepare_motion_training_data` 会严格校验导出文件，联结 decision/plan/preference，按稳定哈希生成 80/10/10 planner 与 curve 数据集和 readiness manifest。
- [x] 数据准备器会应用人工 intent/emotion/intensity 修正、排除未纠正的 Bad/Wrong emotion 监督目标、排除负反馈曲线，并忽略已撤销反馈及 Workbench/debug surface。
- [ ] schema 已支持 candidate A/B，但运行时暂时只有一个 rule candidate；双候选 UI 放到 embedding/ranker 能生成候选之后。

验收：

- 每次动作决策都能追溯 input、intent、plan、warnings。
- 用户可导出和清空数据。

### Phase 3：Embedding planner + semantic cache

- 用 embedding 替代硬字符串模式匹配。
- semantic cache 保存稳定 MotionPlan。
- planner source 标记为 `embedding` 或 `cache`。

当前进度（2026-08-12）：

- [x] 已实现版本化本地 semantic cache，key 包含规范化文本、runtime、emotion、近期 intent、model/profile/planner/primitive 版本。
- [x] cache/planner/driver 任一失败都能回退到 DisplayPlan adapter、rule driver 和 safe idle。
- [ ] 真正的 embedding retrieval 尚未接入；在没有可比较数据和确定的本地 embedding runtime 前，不为了“去掉正则”引入新的模型依赖。

验收：

- 同义表达能命中相近动作。
- 常见表达 cache hit 后行为稳定。

### Phase 4：Ranker / 小 LLM planner

- 用 500-2000 条决策样本训练 intent planner 或 ranker。
- 小 LLM 只输出 `MotionIntent` JSON。
- 增加 validator 对 LLM 输出的 schema 检查。

验收：

- 非模板文本的动作选择优于 rule baseline。
- 失败时能回退到 rule/cache。

启动条件：设置页数据审计达到 ready，并冻结当前 50 场景 rule baseline。达到此前不开始神经网络训练，避免用少量、未关联或单一 intent 数据得到虚假提升。

训练前最后一步：导出 Desktop JSONL 后运行数据准备器；只有生成的 `manifest.json` 中 `readyForPlannerTraining` 为 true 才启动 planner SFT。偏好 ranker 另要求 `readyForPreferenceTraining` 为 true。

### Phase 5：Residual refiner

- 用 primitive baseline 生成曲线。
- 小模型输出 residual 或 variant 选择。
- 继续由 validator 做硬约束。

验收：

- 同一 intent 的动作变化更自然，但不破坏安全边界。

### Phase 6：RL 可行性验证

仅当满足以下条件再做：

- 有 replay harness。
- 有足够偏好样本。
- 有本地 reward/ranker。
- 有可重复训练环境。
- 有明确 baseline。

验收：

- RL policy 在 benchmark 上优于 supervised/refiner baseline。
- 不增加明显抖动、过度动作或状态机冲突。

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 小模型输出不稳定 | 动作怪、破坏体验 | schema validator、fallback、cache、feature flag |
| 动作过度 | 桌宠吵、打扰用户 | intensity limit、repetition penalty、preference 标签 |
| 奖励模型偏差 | 学出夸张动作 | 人类偏好校准、硬约束强惩罚、A/B 而非绝对分 |
| 隐私泄漏 | 文本日志包含敏感内容 | 默认本地、可关闭、可清空、导出前提示 |
| 参数映射不准 | 换模型后动作难看 | ModelPerformanceProfile、手动校准、safe fallback |
| 数据不可训练 | 日志缺上下文或版本 | schemaVersion、plannerVersion、modelProfileVersion、telemetryId |
| 过早 RL | 训练成本高且效果差 | 先 rule/retrieval/ranker/refiner，RL 后置 |

## 13. 当前决策

1. 首版做本地 Motion Intelligence Layer，不复现 HY-Motion / LPM 大模型。
2. 小模型优先用于语义规划、ranker 和 residual refiner，不直接输出 Live2D 私有参数。
3. 所有曲线必须经过 validator 和 priority mixer。
4. 从第一版开始采集 JSONL 数据，为未来训练做准备。
5. VLM/LLM 可用于弱标注和 A/B judge，但不作为在线实时 reward。
6. RL 只作为后期可选优化，用于低层 skill policy，不用于首版动作选择。
7. 已落地一条服务端 cheap-LLM 路径：`motion_planner`（见 6.1.1）负责在
   Desktop 回复路径上生成 DisplayPlan，桌面本地 planner 继续负责表现层精修；
   训练数据准备脚本见 `scripts/prepare_motion_training_data.py`。

## 14. 参考

- HY-Motion 1.0: https://github.com/Tencent-Hunyuan/HY-Motion-1.0
- HY-Motion paper: https://arxiv.org/abs/2512.23464
- LPM project: https://large-performance-model.github.io/
- LPM paper: https://arxiv.org/pdf/2604.07823
- PFNN: https://theorangeduck.com/page/phase-functioned-neural-networks-character-control
- Learned Motion Matching: https://www.ubisoft.com/en-us/studio/laforge/news/6xXL85Q3bF2vEj76xmnmIu/introducing-learned-motion-matching
