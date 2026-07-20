# 仓库减法清单（subtraction backlog）

> 状态：草案 / 待重构期落地
> 记录时间：2026-07-20
> 目标：把"默认配置下根本没被点亮"的代码、重复的配置项和重叠的鉴权概念整理成一份
> 可逐项执行的减法清单，供功能堆叠告一段落后回头重构时使用。
> 关联文档：
>
> - [memory-simplification-proposal.md](memory-simplification-proposal.md)（已落地的 Markdown ↔ memory_items 合并）
> - [memory-soft-scope-and-authz.md](memory-soft-scope-and-authz.md)
> - [person-identity-system.md](person-identity-system.md)
> - [authorization-tickets.md](authorization-tickets.md)
> - [agent-loop-repair-plan.md](agent-loop-repair-plan.md)

---

## 0. 背景：为什么需要这份清单

代码体量约 **226 个 Python 文件 / 5.6 万行**（不含 `webui/`、`desktop/`、`nahida-bot-sdk/`、`docs/`）。
按 `config.yaml` 默认值审计后发现，**默认运行下根本没被点亮的代码在 4000–5000 行左右**，
集中在三块：

1. **验权 / Identity 体系** —— 6 套相互重叠的鉴权概念，绝大多数默认关闭。
2. **记忆 / 上下文体系** —— 多套 scope 维度、sensitivity provenance、portability；rule-based + LLM 双 dreamer。
3. **未到来的"未来"功能** —— node 分布式协议（Phase 5）、canonical ledger 读取侧（Phase 5）、
   conversation_joiner 状态机（MVP 默认关）、authorization tickets（默认关）。

下面的项目都按 **ROI（删的行数 ÷ 风险）** 排序，前 4 项即可把体感重量砍掉一半，
且都不会破坏默认运行。

---

## 1. 现状地图：哪些地方"重"

| 模块 | 行数 | 默认状态 | 主要问题 |
|------|-----:|---------|---------|
| `identity/` | 1036 | `enabled=false` | 6 套鉴权概念中权重最大的；与 memory/loop/commands/gateway 多处耦合 |
| `agent/memory/` | 2979 | 部分启用 | 7 种 scope、sensitivity × source × portable 三维正交、rule+LLM 双 dreamer |
| `agent/context.py` | 1020 | 启用 | 5 条预算路径、二分截断时反复 `json.dumps` metadata |
| `gateway/services/node_*.py` + `node_protocol/` + `node/` | ~1500 | 无节点接入 | Phase 5 分布式协议，未使用 |
| `gateway/routes/` | 2908 | 部分启用 | node/identity/memory/cron 等大量 route 对默认部署无意义 |
| `conversation_joiner/plugin.py` | 1689 | `enabled=false` | 含 engagement 状态机、continue_gate/exit_gate，"in-memory MVP" |
| `agent/runtime/` + canonical ledger | ~900 | 写入侧 `enabled=true` | 读取 / 重放侧未实现，写入数据无人消费 |
| `core/config.py` | 457 | — | 13 个 Pydantic model 全部 `extra="allow"`；3 处 legacy 字段未清 |
| `scheduler/service.py` 的 memory_dreaming | ~200 | 启用 | 12 个配置字段、双 dreamer 调用路径 |

---

## 2. 减法项目（按 ROI 排序）

每项给出：**位置 / 影响 / 默认状态 / 删除步骤 / 风险**。

### ▢ 2.1 删除 Authorization Tickets（最高 ROI）

- **位置**：
  - `nahida_bot/identity/authorization.py:167-298`（`request_ticket` / `approve_ticket` / `revoke_ticket` / `ticket_status` / `_consume_matching_grant` 等，约 130 行）
  - `nahida_bot/plugins/builtin/commands.py:2401-2484`（`_cmd_auth` + `_auth_usage`，约 85 行）
  - `nahida_bot/gateway/routes/` 无对应路由（已经省了）
  - 配置：`IdentityAuthorizationTicketsConfig`（4 个字段 + `IdentityConfig.authorization_tickets`）
  - 文档：[authorization-tickets.md](authorization-tickets.md)
- **默认状态**：`identity.authorization_tickets.enabled=false`，整套流程从未运行。
- **删除步骤**：
  1. 删 `IdentityAuthorizationTicketsConfig` 与 `IdentityConfig.authorization_tickets` 字段。
  2. 删 `AuthorizationGate` 的 `request_ticket` / `approve_ticket` / `revoke_ticket` / `ticket_status` /
     `_consume_matching_grant` / `_prune` / `_new_id` / `_challenges` / `_grants` 等成员。
  3. 删 `commands.py` 的 `_cmd_auth` 与 `_auth_usage`，以及 `_register_commands` 里对应的 `register_command`。
  4. 删 `api_bridge.py` 里 `authorization_ticket` 这个 API（若存在）。
  5. 删 [authorization-tickets.md](authorization-tickets.md)。
- **风险**：极低。功能默认关、无生产数据、无外部 API 依赖。非管理员要执行一次特权工具，
  让管理员代跑即可——聊天机器人不需要"一次性精确参数授权工单"。

### ▢ 2.2 简化 Memory sensitivity / portability 体系

- **位置**：
  - `nahida_bot/agent/memory/models.py:36-46`（`normalize_sensitivity_source`）+ `SensitivitySource` Literal
  - `nahida_bot/agent/memory/service.py:59-76`（`resolve_write_sensitivity` 的 explicit 优先级分支）
  - `nahida_bot/agent/memory/consolidation.py:96-119`（`classify_sensitivity` 中的 explicit vs dream 区分）
  - `nahida_bot/agent/memory/portability.py` 整个文件（50 行）
  - `nahida_bot/agent/memory/service.py:286-337`（`store_item` 中 portable 分支 + re-route 逻辑）
  - `nahida_bot/agent/memory/service.py:398-416`（`update_item_for_context` 中 portable 分支）
  - `nahida_bot/agent/memory/consolidation.py:557-592`（portable=false 走 chat scope 的分支）
  - 配置：`MemoryRetrievalConfig.soft_scope`（默认 false）
  - DB 列：`memory_items.sensitivity_source`（已有数据需 backfill 或丢弃）
- **默认状态**：`memory.retrieval.soft_scope=false`；portability 默认 true。
- **简化方向**：
  - 把 `Sensitivity` 收敛到二元 `public | private`，删除 `secret_like`（或反之，保留一个"严格不跨 scope"档）。
  - 删除 `SensitivitySource` 整列与 `sensitivity_source` DB 列（或保留为常量 `"default"`，不再分支）。
  - 删除 `portability.py` 与所有 `portable=false` 分支；非公开记忆天然不跨 scope，等价于 `portable=false`。
- **影响行数**：约 250–350 行 + 4 个配置字段 + 1 个 DB 列。
- **风险**：中。需要一次 schema 迁移或一次性 backfill 脚本；如果有用户已经写入 `private` 记忆，
  要决定是降级为 `public` 还是保留。

### ▢ 2.3 砍掉 Person / Account scope 层

- **位置**：
  - `nahida_bot/identity/policy.py` 整个文件（235 行）
  - `nahida_bot/agent/memory/scope.py:30-31`（`SCOPE_TYPE_PERSON` / `SCOPE_TYPE_ACCOUNT` 常量）
  - `nahida_bot/agent/memory/service.py:162-256`（`search_items_cascade` 中的 person → account → chat → global 级联）
  - `nahida_bot/agent/memory/service.py:496-509`（`_accessible_item` 中的级联判定）
  - `nahida_bot/agent/memory/consolidation.py:488-503`（`MemoryWriteRequest` 走 person/account 的分支）
  - 配置：`IdentityConfig.people` / `IdentityConfig.admins`
- **默认状态**：`identity.enabled=false`；person/account scope 在运行时永远是空集。
- **简化方向**：
  - 保留 `IdentityResolver` 与 `AuthorizationGate`（admin 集合），但**移除"记忆按 person 分桶"语义**。
  - 记忆 scope 收敛到 `global` + `chat` 两种。
  - 删除 `MemoryReadRequest` / `MemoryWriteRequest` 中 `person_id` / `sender_account_key` 字段。
- **影响行数**：约 400–600 行 + 2 个 scope 常量 + 2 个配置字段。
- **风险**：中。如果未来真的要做"跨账号同一个人"的记忆合并，需要重新引入；但当前完全没在跑，
  先收回承诺、留 issue 追踪更健康。

### ▢ 2.4 合并 `AgentConfig` 与 `AgentLoopConfig`

- **位置**：
  - `nahida_bot/core/config.py:52-74`（`AgentConfig` pydantic）
  - `nahida_bot/agent/loop.py:105-128`（`AgentLoopConfig` dataclass，含一个自承认的 TODO）
  - `nahida_bot/core/app.py:453-465`（逐字段搬运）
- **简化方向**：让 `AgentLoop` 直接接收 `AgentConfig`，或反过来把 pydantic 模型做成
  `AgentLoopConfig` 的工厂。当前两份字段几乎一一对应，新增字段时容易只改一处。
- **影响行数**：净减约 30 行，但解掉一个长期 TODO，并降低未来漂移风险。
- **风险**：极低。

### ▢ 2.5 清理 Markdown 记忆遗留读路径

- **位置**：
  - `nahida_bot/agent/memory/markdown.py:84-113`（`append_daily_memory` /
    `append_long_term_memory`，**全仓只有定义、无调用方**——纯死代码）
  - `nahida_bot/agent/memory/markdown.py:46-61`（`daily_memory_path` / `recent_daily_memory_paths`）
  - `nahida_bot/agent/memory/markdown.py:13-15`（`DAILY_MEMORY_DIR` / `DAILY_MEMORY_GLOB` / `DEFAULT_DAILY_DAYS`）
  - `nahida_bot/agent/memory/markdown.py:264-292`（`load_workspace_markdown_memory` 里 daily 段）
  - `nahida_bot/plugins/builtin/commands.py:1037-1052`（`_tool_memory_read` 里 daily 段）
- **背景**：当前代码无任何地方写 `memory/YYYY-MM-DD.md`；这套读取路径纯粹是给老工作区留的兼容层。
  Markdown 已是纯投影（见 [memory-simplification-proposal.md](memory-simplification-proposal.md)）。
- **删除步骤**：
  1. 删 `append_daily_memory` / `append_long_term_memory` 两个死函数。
  2. 决定是否保留 daily notes 的**读**路径。如果不需要兼容老工作区：删 `recent_daily_memory_paths`、
     `DAILY_MEMORY_*` 常量、`load_workspace_markdown_memory` 与 `_tool_memory_read` 中 daily 段。
- **影响行数**：50–80 行。
- **风险**：极低（死代码部分）/ 低（兼容路径部分，需确认无人有老 daily 笔记）。

### ▢ 2.6 清理 legacy 配置字段

- **位置**：
  - `nahida_bot/core/config.py`：`MemoryEmbeddingConfig.provider_id`（注释已写 "Legacy"）
  - `nahida_bot/core/config.py`：`MultimodalConfig.image_fallback_provider`（注释已写 "Legacy"）
  - `nahida_bot/core/config.py`：`SchedulerConfigModel.memory_dreaming_provider_id`（注释已写 "Legacy"）
  - `nahida_bot/core/config.py`：`Settings.model_routing`（注释已写 "Legacy, ignored"）
  - `nahida_bot/gateway/services/webui_auth.py:99-103`：`sha256:` 这种 legacy 密码哈希格式
- **删除步骤**：逐字段删除 + 在 `app.py` 里的回退处理去掉；`webui_auth` 的 `sha256:` 分支直接删。
- **影响行数**：约 50 行 + 4 个配置字段。
- **风险**：低。需要提醒用户迁移 `webui.auth.admin_password_hash` 到 `pbkdf2_sha256$...` 格式
  （`hash_password_pbkdf2` 函数已经提供生成入口）。

### ▢ 2.7 收敛 conversation_joiner

- **位置**：
  - `nahida_bot/plugins/conversation_joiner/plugin.py`（1689 行）
  - `config.yaml` 的 `conversation_joiner` 块（约 60 行 + 5 层嵌套）
- **简化方向**：
  - 砍掉 `engagement` 状态机（`batching` / `continue_gate` / `exit_gate` 全部子配置 + 实现约 800 行）
  - 保留 threshold + cooldown + sample_rate + persona_context 这套 MVP（约 400 行）
  - 状态机推到独立分支或 `experimental/` 目录，等真要打磨时再合回来
- **影响行数**：约 800 行实现 + 约 40 行配置注释。
- **风险**：中。需确认依赖状态机的 hook（事件、SSE 推送）有 graceful degradation。

### ▢ 2.8 收敛记忆 dreamer 路径

- **位置**：
  - `nahida_bot/agent/memory/consolidation.py:204-335`（`RuleBasedMemoryExtractor`，约 130 行 + 8 个正则常量）
  - 同文件 `_dream_session` 调用 `_extractor.extract` 后又调 LLM `dream`，最后 `_dedupe_extractions` 合并
  - `nahida_bot/scheduler/service.py:563-655`（`_dream_session` 调用 consolidator 的入口）
- **简化方向**：**二选一**。
  - 选 LLM dreamer：删 `RuleBasedMemoryExtractor` 与 8 个正则；consolidator 直接走 LLM。
  - 选 rule-based：删 `LlmMemoryDreamer`，省一次 LLM 调用，但召回质量下降。
  - **推荐 LLM 路径**：regex 抓中文"我喜欢/决定/TODO"很容易误抓，且与 LLM 重复劳动。
- **影响行数**：约 130–200 行。
- **风险**：低。前提是 LLM dreamer 模型可配置（已经是）。

### ▢ 2.9 关闭并移除 canonical ledger 写入侧

- **位置**：
  - `nahida_bot/agent/runtime/`（887 行）
  - `nahida_bot/db/repositories/sqlite_agent_run_repo.py`
  - `nahida_bot/core/config.py:77-93`（`AgentRuntimeConfig`）
  - `nahida_bot/core/app.py:283-287, 447-451`（run_store 注入）
  - `config.yaml`：`agent_runtime.canonical_ledger_enabled` / `transcript_replay_enabled`
- **背景**：写入侧默认 `enabled=true` 在采集数据，但 [agent-loop-repair-plan.md](agent-loop-repair-plan.md)
  自己写"读取 / 重放侧属 Phase 5"。**没人读的数据不用采集**。
- **删除步骤**：
  1. 配置默认置 `false`（最低代价）。
  2. 或完全移除 `AgentRunStore` / `RunRecorder` 与 `AgentLoop.run_store` 参数。
  3. 等 Phase 5 真要做时，从 git 历史恢复。
- **影响行数**：保留代码仅关配置 = 0 行；完全移除 = 约 900 行。
- **风险**：低。已有数据保留在 DB 里，恢复路径完整。

### ▢ 2.10 移除 Node 分布式协议

- **位置**：
  - `nahida_bot/gateway/services/node_*.py`（5 个文件，约 1000 行）
  - `nahida_bot/gateway/node_protocol/`（7 个文件，约 1110 行）
  - `nahida_bot/gateway/routes/nodes.py`（156 行）
  - `nahida_bot/node/`（client + capabilities，约 456 行）
  - `nahida_bot/core/config.py`：`NodeProtocolConfigModel` / `WebAPIConfigModel.nodes`
  - `config.yaml` 的 `webapi.nodes.*` 注释
- **背景**：README 自己说 Phase 5"规划中"。无任何外部 node 客户端实际在连。
- **删除步骤**：整组移到 `experimental/node-protocol/` 目录（保留 git 历史）或独立分支；
  从 `app.py` / `routes/__init__.py` / `WebAPIConfigModel` 中摘除引用。
- **影响行数**：约 2700 行。
- **风险**：中。需要清理 `WebAPIApp` 里注册 node WebSocket 端点的代码；如果有用户在试水 node 协议，
  需要提前公告。

### ▢ 2.11 重构 ContextBuilder 预算路径

- **位置**：`nahida_bot/agent/context.py:521-781`（`build_context` + 4 条 fallback 路径）
- **问题**：当前是"先 fit，fit 不下 try summary，summary 不下再 compact，再 fit"的多层 if-else，
  且 `_estimate_tokens` 在二分截断里反复 `json.dumps(metadata)`（`context.py:1064` 有自承认 TODO）。
- **重构方向**：收敛为一条主路径，显式四步：
  1. 按 `tool_transcript_groups` 分组。
  2. 装填 prefix（system / instructions / catalog / memory）。
  3. 倒序装填 history 到 soft budget。
  4. 装 protected（必要时按 tool→assistant→user 顺序截断 tool result 内容）。
  仅在 protected 仍超预算时退到 summary；compact_summary 作为最后一道兜底。
  `_estimate_tokens` 加 metadata 序列化缓存（按 `id(message)` + content hash）。
- **影响行数**：净减约 150 行 + 1 个 TODO 关闭。
- **风险**：中。涉及核心路径，需要完整的 context 组装测试覆盖。

### ▢ 2.12 收敛配置 `extra="allow"`

- **位置**：`nahida_bot/core/config.py` 所有 13 个 Pydantic model。
- **问题**：全部 `extra="allow"`，拼错的顶层 key 会被静默吞掉；插件配置走顶层 dict 的隐式约定。
- **简化方向**：
  - `Settings` 改为 `extra="forbid"`，新增显式 `plugin_configs: dict[str, dict]` 字段。
  - 子模型按需放开（如 `ProviderEntryConfig` 因为各家 provider 字段不同，保留 allow）。
  - `app.py` 里 `_inject_plugin_configs` 改为读 `plugin_configs`。
- **影响行数**：净变化小，但能尽早抓到配置拼写错误。
- **风险**：中。属于 breaking change：现有用户的 `config.yaml` 顶层插件配置块需要迁移到
  `plugin_configs:` 下。需要一次校验脚本或一次性兼容层。

---

## 3. 推荐执行顺序

按"风险 ÷ 收益"由低到高排，**前 4 步即可让体感重量砍掉一半**，且都不破坏默认运行：

1. **2.5 Markdown 死代码 + daily 读路径**（80 行，零风险）
2. **2.6 清理 legacy 字段 + sha256 密码**（50 行，零风险）
3. **2.4 合并 AgentConfig / AgentLoopConfig**（30 行 + 关闭 TODO，极低风险）
4. **2.1 删除 Authorization Tickets**（~300 行 + 4 个配置字段，极低风险）
5. **2.9 关闭 canonical ledger 写入**（配置改 false 即可；完全移除留到 Phase 5 真启动前）
6. **2.8 收敛 dreamer 到 LLM 一条路径**（~150 行，低风险）
7. **2.2 简化 sensitivity / portability**（~300 行 + 1 个 DB 列，需要迁移脚本）
8. **2.3 砍 person/account scope 层**（~500 行，需要确认 identity 路径不破）
9. **2.7 收敛 conversation_joiner**（~800 行，需要降级测试）
10. **2.11 重构 ContextBuilder**（核心路径，需要测试覆盖）
11. **2.10 移除 node 协议**（最大单块，建议放最后；移到 `experimental/` 而非删除）
12. **2.12 收敛配置 extra**（breaking change，留到大版本号升级时做）

---

## 4. 不建议动的地方

下列虽然在默认配置下也没跑，但**删除或合并的代价高于收益**：

- **`memory_candidates` 候选表**：虽然 consolidator 拿到结果直接 `auto_applied` 写库，
  candidate 表实际是审计镜像；但这是 dreaming 唯一的可观测窗口，WebUI 也接了。删掉收益小、
  丢失诊断能力大。
- **`memory_turns`（原话流水）**： dreaming 的输入、回放的基础，不能合到 `memory_items`。
- **KB / DocumentStore**：与 memory 共用检索底座但生命周期完全独立。
- **`workspace MEMORY.md` 投影本身**：[memory-simplification-proposal.md](memory-simplification-proposal.md)
  已确认 Markdown 作为 grep 兜底召回的真实价值；保留投影，只清死代码（见 2.5）。
- **多 Channel（Telegram / Milky / OneBot）**：是产品定位，不是冗余。
- **WebUI Bearer + Session 双轨**：Bearer 给脚本、Session 给浏览器，职责清晰，不算重复。

---

## 5. 行动追踪

每完成一项打勾并填写 PR / commit：

- [ ] 2.1 Authorization Tickets —— PR: ___
- [ ] 2.2 Sensitivity / portability 简化 —— PR: ___
- [ ] 2.3 Person/account scope 移除 —— PR: ___
- [ ] 2.4 AgentConfig / AgentLoopConfig 合并 —— PR: ___
- [ ] 2.5 Markdown 死代码 / daily 读路径 —— PR: ___
- [ ] 2.6 Legacy 字段 + sha256 密码 —— PR: ___
- [ ] 2.7 conversation_joiner 收敛 —— PR: ___
- [ ] 2.8 Dreamer 二选一 —— PR: ___
- [ ] 2.9 canonical ledger 关闭/移除 —— PR: ___
- [ ] 2.10 Node 协议移除 —— PR: ___
- [ ] 2.11 ContextBuilder 重构 —— PR: ___
- [ ] 2.12 配置 extra 收敛 —— PR: ___
