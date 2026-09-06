# Code Quality Review

实施进度见 [修复记录](D:/Projects/nahida-bot/docs/code-quality-fixes-2026-09-05.md)。以下问题及行号保留审查时 `6b089cc` 的基线；已移动的文件以修复记录的新路径为准。

## Summary

当前主要质量问题是**同一条规则缺少明确的维护归属：接口已经存在，调用链却绕开它、复制它，或者在跨层转换时丢掉它承载的信息**。这同时表现为抽象不足与多余包装，并已造成可复现的行为不一致。

审查基线：`v2` 工作区 HEAD `6b089cc`，开始时工作区 clean。方法为主审人工追踪调用链、Luna 独立审查插件子系统、Python AST 统计、局部运行探针及现有测试。扫描了源码 TODO/FIXME；深读重点是 core、retrieval、插件生命周期与桥接、相关渠道和 SDK 地址模型。前端仅检索标记及关联入口，未做全面审查。未改业务代码、未访问生产服务或数据库。

## Overall Assessment

| Metric | Score |
|---|---|
| Overall | 工具参考分 83.26/100；不能代表架构质量 |
| Files Analyzed | 工具分析 297 个 Python 文件；人工为重点抽查，并非逐文件通读 |
| Critical Issues | 未确认 P0/P1；下列 1–5 为可复现问题，6–10 为设计债务或风险 |
| Existing checks | Ruff 通过；5 个相关测试文件共 85 个测试通过 |

量化工具为 `eff-u-code 2.2.2`。Tree-sitter 的 Python WASM 缺失，工具退回正则解析，**函数长度和参数数量明显失真**：它把 SessionRunner 最大参数数量报为 3，而 Python AST 得到构造函数有 27 个非 self 参数。因此不依据分数开重构清单，也不据此与 8 月 23 日的分数比较趋势。

参考热点：`core/app.py` 52.59，`channels/milky/plugin.py` 59.80，`plugins/knowledge_base/plugin.py` 64.14，`core/session_runner.py` 67.69。AST 确认 SessionRunner 有 77 个直接定义的方法、文件 3440 行；RealBotAPI 构造函数 28 个非 self 参数、98 个直接定义的方法。这些是定位线索，下面的判断依赖具体职责和行为。

## Key Issues (sorted by severity)

### 1. 插件导入与重载没有分开，错误归一化也不完整〔P2，已复现〕

位置：[loader.py:97](D:/Projects/nahida-bot/nahida_bot/plugins/loader.py:97)、[loader.py:107](D:/Projects/nahida-bot/nahida_bot/plugins/loader.py:107)、[manager.py:317](D:/Projects/nahida-bot/nahida_bot/plugins/manager.py:317)。对应两个 loader FIXME。

普通外部插件加载先 `import_module()`，再检查 `module_path in sys.modules` 并 `reload()`；检查时条件已经必然成立。临时插件在模块顶层递增文件计数器，首次 `load()` 后计数为 **2**。有顶层注册或资源初始化的插件会重复执行副作用。

同一导入边界只包装 `ImportError`。探针顶层抛 `RuntimeError` 时原样穿透，而 manager 仅在捕获 `PluginLoadError` 时记录 ERROR，导致失败状态与真实结果不一致。reload 也在该异常包装范围之外。

建议：将普通 load 和显式 reload 分开；在插件导入/重载边界统一包装普通异常并记录失败状态。保留异常链，不吞掉进程取消或退出信号。`sys.path.insert(0)` 的污染另需明确插件模块命名和导入路径所有权，不能只靠卸载时随手删除共享路径解决。

### 2. 依赖注入的平行参数表已经漏传服务〔P2，已复现〕

位置：[manager.py:134](D:/Projects/nahida-bot/nahida_bot/plugins/manager.py:134)、[manager.py:179](D:/Projects/nahida-bot/nahida_bot/plugins/manager.py:179)、[api_bridge.py:2146](D:/Projects/nahida-bot/nahida_bot/plugins/api_bridge.py:2146)。对应 manager:326 的注入 TODO。

manager 的更新入口接受 `model_router`、`speech_service`，但转交已加载 bridge 时两者都遗漏。bridge 自己有 `model_router=None` 参数并无条件赋值，结果是 **manager 更新成功，已有 bridge 的 router 被清成 None**；speech 则始终保留旧值。用真实两个 setter、内存中的服务替身复现，两个 bridge 字段均为 None。

启动流程先加载 pre-agent 插件，再在 [app.py:235](D:/Projects/nahida-bot/nahida_bot/core/app.py:235) 等处更新服务，因此这个契约与真实启动顺序有关。内置 TTS 的 post-agent 加载顺序通常避开 speech 问题，不能据此说默认 TTS 已坏。

建议：近期补齐转交并验证已有 bridge 的字段；随后统一 RuntimeServices 更新契约，明确“未提供”和“清空”的区别。服务对象只在装配边界使用，业务类仍接受自己所需的窄依赖，避免制造新的万能 service locator。

### 3. KB 抽象经过往返转换后丢失信息〔P2，已复现〕

位置：[plugin.py:1000](D:/Projects/nahida-bot/nahida_bot/plugins/knowledge_base/plugin.py:1000)、[plugin.py:1015](D:/Projects/nahida-bot/nahida_bot/plugins/knowledge_base/plugin.py:1015)、[session_runner.py:1865](D:/Projects/nahida-bot/nahida_bot/core/session_runner.py:1865)。

实际链路：store → RetrievalAdapter → RetrievalService → RetrievalResult → 插件取 `.raw` → runner 再调用 adapter 的私有转换函数。已经算好的实际 mode、邻居折扣和 `neighbor_of` 在 `.raw` 处丢失；runner 统一补成 `mode="hybrid"`，与插件是否开启向量或是否降级无关。

启用邻居扩展的内存假 store 探针：

| 结果 | adapter 输出 | 经插件再按 runner 路径重建 |
|---|---|---|
| 主命中 mode | fts | hybrid |
| 邻居 score | 4.0 | 5.0 |
| 邻居 neighbor_of | hit | 缺失 |

真实数据库邻居对象未必带这个非零分数，但 mode 和 metadata 的信息损失无条件存在；探针用非零分数展示契约损失。runner 后续还有跨集合排序和阈值过滤，不能把它只当显示字段问题。

建议：内部检索入口直接返回 `list[RetrievalResult]`，工具展示或外部兼容接口在最外层投影。保留 RetrievalService 已经在使用的跨来源融合能力；单 adapter 的局部调用不必每次新建 service 再绕一圈。无需再增一层统一检索框架。

### 4. 渠道能力、prompt 文案与配置是三份事实来源〔P2，已复现〕

位置：[outbound_mentions.py:23](D:/Projects/nahida-bot/nahida_bot/core/outbound_mentions.py:23)、[message_context.py:75](D:/Projects/nahida-bot/nahida_bot/core/message_context.py:75)、[session_runner.py:2820](D:/Projects/nahida-bot/nahida_bot/core/session_runner.py:2820)、[milky/config.py:112](D:/Projects/nahida-bot/nahida_bot/channels/milky/config.py:112)。直接对应两处用户关注的 FIXME。

core 写死支持渠道集合、QQ/飞书 ID 形状、两份 mention prompt。prompt 固定教“最多 3 个 token”，实际渠道按 `max_mentions_per_message` 限制不同目标数量；两者连计数语义都不同。runner 注入时只看平台名，不看 `outbound_mentions_enabled`。

注册一个关闭 mention、上限设为 1 的 milky 渠道后，生成的 prompt 仍包含 CQ token 教程及“最多 3 个”。出站会遵守开关，问题是模型仍被教着输出不能兑现的格式，用户可能看到字面 token。

建议：复用已有 [PromptSupplementRegistry](D:/Projects/nahida-bot/nahida_bot/plugins/registry.py:124)，由渠道按当前配置注册/更新能力提示；将限额文案从有效配置生成。已有渠道正在用这个入口注入 Markdown 能力提示。共享纯解析函数可以留在 messaging 公共模块，QQ/飞书策略归渠道；不要新增包揽所有 prompt 的大 manager。

### 5. SessionKey 已存在，却仍有多份字符串切分协议〔P2，已复现〕

位置：[authorization.py:60](D:/Projects/nahida-bot/nahida_bot/identity/authorization.py:60)、[adapters.py:472](D:/Projects/nahida-bot/nahida_bot/agent/retrieval/adapters.py:472)、[sessions.py:261](D:/Projects/nahida-bot/nahida_bot/gateway/routes/sessions.py:261)、[history.py:581](D:/Projects/nahida-bot/nahida_bot/plugins/builtin/tools/history.py:581)。

前三段切分散在授权、检索、历史工具；gateway 又自带一份 target type 集合。对于 SDK 明确支持的旧格式派生 ID `milky:12345:session-a`：SDK 和 gateway 解析出的基础旧地址为 `milky:12345`，authorization 和 retrieval 却保留 `:session-a`。旧格式会话的同聊天识别和检索来源标识因此不一致。未证明由此产生越权。

建议：在 SDK 的 SessionKey 旁定义明确的“会话归属聊天”转换，包含 typed/legacy 的规范化策略，各入口调用。不要盲目把所有代码换成 `ChatAddress.parse()`：第四段在 ChatAddress 和 SessionKey 里可能分别表示 thread 与 suffix，需要先固定语义。

### 6. SessionRunner 拆出了阶段方法，但职责仍聚集〔P2，维护风险〕

位置：[session_runner.py:252](D:/Projects/nahida-bot/nahida_bot/core/session_runner.py:252)、[:1064](D:/Projects/nahida-bot/nahida_bot/core/session_runner.py:1064)、[:1802](D:/Projects/nahida-bot/nahida_bot/core/session_runner.py:1802)、[:2318](D:/Projects/nahida-bot/nahida_bot/core/session_runner.py:2318)、[:3084](D:/Projects/nahida-bot/nahida_bot/core/session_runner.py:3084)。

它同时维护模型路由、历史窗口、KB 查询策略、媒体下载与视觉降级、工具 schema、prompt 组装、转录持久化。内部 request/runtime 对象改善了阶段组织，但没有隔离这些规则的变化；KB 私有转换函数穿透正是具体后果。image_understand schema 还在 app:795 和 runner:2288 双写。

建议：先抽离依赖相对独立的媒体准备，再收口检索与上下文组装、转录持久化；runner 保留调度顺序。不要为了降低单文件行数机械搬成多个 mixin，也不要把 27 个参数装进一个不透明袋子当作解决问题。

### 7. Skill 目录能力属于工作区，却寄居在 context 模块〔P2，维护风险〕

位置：[commands.py:932](D:/Projects/nahida-bot/nahida_bot/plugins/builtin/commands.py:932)、[commands.py:958](D:/Projects/nahida-bot/nahida_bot/plugins/builtin/commands.py:958)、[router.py:1553](D:/Projects/nahida-bot/nahida_bot/core/router.py:1553)。对应 commands:938 的 TODO。

工具 `skill`、`/help`、slash 路由都直接导入 `agent.context.SkillCatalog`，自己取 workspace 路径再访问磁盘。skill 查找规则修改时，调用方都需要了解 agent 的上下文实现；这与工作区资产的所有权不符。

建议：把 SkillCatalog 放到 workspace 专属模块，WorkspaceManager 暴露 `list/read skill` 窄接口，agent 仅消费目录快照。不会因此把 token 预算或整个 ContextBuilder 一并搬过去。

### 8. TTS 与图片生成重复实现配额和任务生命周期〔P2，维护风险〕

位置：[tts/plugin.py:385](D:/Projects/nahida-bot/nahida_bot/plugins/tts/plugin.py:385)、[image_generation/plugin.py:387](D:/Projects/nahida-bot/nahida_bot/plugins/image_generation/plugin.py:387)、[tts/plugin.py:433](D:/Projects/nahida-bot/nahida_bot/plugins/tts/plugin.py:433)、[image_generation/plugin.py:206](D:/Projects/nahida-bot/nahida_bot/plugins/image_generation/plugin.py:206)。

两边平行维护滚动 24h 账本、锁、预留/释放、过期清理、重试时间和后台任务集合；连“配额重启后丢失”的 TODO 也各有一份。框架已有 `api.spawn_task()` 和按插件归属取消任务的能力。

建议：共享具备 reserve/release 语义的 RollingQuotaLimiter，显式传入额度单位，任务归入框架生命周期。是否持久化额度是需求选择；重复账本本身已是维护债务。不要把 TTS 和图像请求流程一起塞进 GenericGenerationPlugin。

### 9. 待处理消息队列既无界，又用八元组代替请求模型〔P2，容量与维护风险〕

位置：[router.py:154](D:/Projects/nahida-bot/nahida_bot/core/router.py:154)、[:929](D:/Projects/nahida-bot/nahida_bot/core/router.py:929)、[:1197](D:/Projects/nahida-bot/nahida_bot/core/router.py:1197)。对应 backpressure TODO。

agent 忙时无条件追加队列；消息数量没有上限或过期策略。长运行遇到持续消息会积累附件引用、旧指令和延迟。正常执行使用 `_AgentDispatchRequest`，排队却退回八元组，消费端靠位置解包，还用 list.pop(0)。

建议：提取具名 PendingMessage 和有界 deque，由 router 明确接纳、合并、拒绝与过期规则。仅换 deque 不能解决积压；上限与拒绝行为要一起定义。本轮没有压测，不能声称线上已因它故障。

### 10. 临时诊断代码仍让 agent loop 理解 provider 原生格式〔P3，维护风险〕

位置：[loop.py:1119](D:/Projects/nahida-bot/nahida_bot/agent/loop.py:1119)、[:1245](D:/Projects/nahida-bot/nahida_bot/agent/loop.py:1245)。

“将来删除”的 TODO 下，主循环仍靠一串中英文关键词猜模型是否承诺调用工具，再识别 choices/output/content 等 provider 原始响应形状来打日志。新增 provider 格式会把适配器细节继续带入执行循环；关键词误报也容易制造 warning 噪声。

建议：provider 输出统一诊断摘要，loop 只记录摘要；自然语言承诺检测若保留，应作为可关闭诊断策略，并明确删除条件。把关键词搬到 constants.py 仍不能解决边界问题。

其余 TODO/FIXME 的分级：

| 标记 | 本轮判断 |
|---|---|
| context.py:1042 重复序列化预算 | 明确的局部效率债务；适合一次构建内缓存不可变投影，未测量性能，不直接给可变消息加长期缓存 |
| sqlite_memory_repo.py:890 逐关键词 INSERT | 可换 executemany 的局部改进，通常不需要新增抽象层 |
| tokenization.py:15/17 | 警告兼容措施与 import 副作用；词表在模块加载时 add_word，懒加载应连初始化一起收口 |
| db/engine.py:535 生命周期 | context manager 能改善临时调用者清理；不能据此说长寿命应用必须每次 async with |
| router.py:150 恢复会话数量 | 容量风险，需结合规模衡量；与 pending 消息上限不是同一个问题 |
| scheduler/service.py:979 所有权 | 自动插话场景的任务 owner 策略待定；未验证越权，不当作已确认漏洞 |
| speech/config、gateway speech、motion_planner、desktop voice | 同一 persona voice 路由需求尚未实现；应统一入口，不应分头加更多 fallback |
| orchestration 的三个 observability TODO | 同一待补可观测能力，不能按三处重复算三个质量缺陷 |
| reasoning_max_chars、milky sender fallback、Telegram Markdown parser | 分别是调参、缺失信息回退和解析器实现选择；本轮未证明行为缺陷 |
| SDK scaffold 的 TODO 字符串 | 生成模板的占位内容，不是产品代码待办 |

与 [8 月 23 日报告](D:/Projects/nahida-bot/docs/code-review-2026-08-23.md) 对照：memory hybrid 已共用候选池和融合 helper；HTTP transport 已抽共享层，Codex 有 async auth hook，这些不再沿用旧结论。也不采纳旧报告中“一律删除外部 API”“mock 用统一 no-op”“全局把 RuntimeError 映射成 503”等过宽建议：仓库内没有调用不等于 SDK 无用户，静默 mock 和泛化异常处理还会掩盖契约错误。

## Refactoring Plan

1. 修 loader 首次导入和异常状态，补顶层副作用计数、失败状态测试。
2. 修 manager→bridge 的 router/speech 转交，补 pre-agent 后更新契约测试。
3. KB 内部保留 RetrievalResult，验证 mode、neighbor_of、score 跨层不变。
4. 渠道通过既有 supplement 接口提供 mention 规则，测试开关与非默认限额。
5. 收口 SessionKey 的聊天归属转换，覆盖 typed、legacy、derived 的一致性。
6. 给 pending 队列具名模型与容量策略，再处理 runner 的职责拆分。
7. 将 SkillCatalog 归入 workspace；共享配额组件并接入框架任务管理。

从用户指出的问题推导出的后续审查标准（是本轮理解，尚非用户逐条确认的仓库规则）：

- **抽象不足**：同一规则改动要同步修改多个模块，且这些模块已经出现语义漂移。
- **过度抽象**：包装、注册表、转换对象没有保存额外语义，却增加调用层数或制造信息往返损失。
- **常量质量**：关注常量的归属与唯一来源；算法局部常量可以留在函数旁，配置/协议/prompt 不能各自重复维护同一限制。
- **边界清晰**：上层消费能力与结果，不导入下层私有转换器、不猜 provider 原始形状、不自行解码另一模块拥有的标识。
- **重构有收益**：能消除一处规则副本、缩小变更范围或保住契约，才值得引入新组件；文件变短不是充分理由。
- **测试保护语义**：检查首次只执行一次、字段转交完整、结果信息不丢、配置能改变行为；避免只断言实现当前生成的字符串。

## Security Concerns

本轮没有确认可利用的越权或新的安全漏洞，也不是完整安全审计。插件 sys.path 污染与无界 pending 队列有隔离/资源风险；session key 漂移触及授权语义，但目前探针仅证明解析不一致，未证明能跨域读取。未连接生产验证。

验证命令：

```text
npx --yes --package eff-u-code fuck-u-code analyze nahida_bot -f json -o <TEMP>/nahida-quality-20260905.json
.venv/Scripts/ruff.exe check nahida_bot nahida-bot-sdk/nahida_bot_sdk --output-format concise
.venv/Scripts/python.exe -m pytest tests/test_session_runner.py tests/test_retrieval_adapters.py tests/test_chat_address.py tests/test_outbound_mentions.py tests/test_plugin_loader.py -q
```

另外运行了 5 组临时本地探针：加载次数/导入异常、已有 bridge 更新、mention 配置、legacy session key、KB 结果往返。全部使用临时插件或内存替身，不调用真实模型、渠道或数据库。探针结果用于确认当前问题，并不代表问题已修复。
