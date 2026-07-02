# 记忆系统去冗余提案:Markdown ↔ memory_items 的合并

> 记录时间：2026-07-01
> 状态：**提案 / 待评估，不阻塞日常开发**。本文记录一次关于"记忆系统是否冗余、能否简化"的讨论与
> 初步结论，**尚未决策**。重开此议题前，先观察 soft-scope（A2）上线后的实际召回质量与打标可靠性。
> 关联：[memory-architecture-exploration.md](memory-architecture-exploration.md)、
> [memory-soft-scope-and-authz.md](memory-soft-scope-and-authz.md)。关联 issue：#22（tracking）。

## 1. 起因

soft-scope + sensitivity 改造（Piece A：`2fcea96` + 评审修复 `96860d7`、`6909773`）落地后，回看整张
记忆地图时浮现一个疑问：记忆有多层存储，其中 **workspace Markdown 记忆与 `memory_items` 是同一
目的的两套可写存储**，语义不兼容，且评审中已产出一个真实的泄漏 bug（敏感写入经 Markdown 每轮
无差别注入，绕过 `memory_items` 的 sensitivity 过滤）。本文评估能否把二者合并、简化记忆系统。

## 2. 现状地图：哪些是真冗余，哪些不是

记忆实际是多层并存，容易混看。按"底→上"：

| 层 | 存储 | 角色 | webui |
|----|------|------|-------|
| A | `memory_turns` | 原话流水（精确回放、dreaming 输入、`search_chat_history`） | sessions 页（按 session） |
| **B** | **`memory_items`** | **持久结构化事实（主记忆：kind/scope/sensitivity/层级/FTS+向量）** | **无** |
| B' | `memory_candidates` | dreaming 提议留痕（pending/auto_applied） | 无 |
| C | workspace Markdown（`MEMORY.md` / daily） | 又一套"长期记忆"：`memory_write` 直接写，每轮整包注入 | 无（仅文件） |
| G | KB（documents/chunks/collections） | 外部文档，与 B 共用检索底座 | 有（`kb.py`） |

横切（非独立存储）：D 作用域（chat/global/person/account）、E 检索底座（`RetrievalService`）、
F 敏感度层（public/private/secret_like + default/dream/explicit）。

**真冗余 = C ↔ B**：同一目的（nahida 长期记住的东西）、两套可写存储、不兼容的读语义（C 整包注入无
排序/无 scope/无 sensitivity；B 按 query 检索有排序/有 scope/有过滤），且 consolidation 还会 B→C
单向投影，导致内容两边都有、语义不一致。

**非冗余（别动）**：

- **A vs B**：A 是"说过什么"、B 是"记住了什么"。合了就丢掉一边——要么没法精确回放，要么没法高效
  结构化检索。A 同时是 B 的 dreaming 输入源。
- **G vs B**：KB 是外部文档、B 是对话提炼，生命周期/写入路径完全不同，只共用检索底座 E。
- **D/E/F**：横切基础设施，不是独立记忆系统。

**证据**：`96860d7` 修的泄漏 bug 根因就是两套存储语义不组合——敏感项进 C 后，C 的整包注入绕过了
B 的 sensitivity 过滤。这是设计层面的缝，不是单点 bug。

## 3. 一个真实矛盾：Markdown 的 grep 兜底召回

合并 C→B 看似干净，但有一个不平凡的代价，**它正是本文要回答的核心问题**：

> Markdown 是文件 → agent 已有 `workspace_read` / `exec(grep)` 等**通用文件工具**，召回 miss 时她
> 自己就能 fallback 去文件里翻；`memory_items` 是 SQLite → agent **没有任何通用工具能直接查**，
> 只能看检索自动注入的、或 `memory_search` 返回的。检索 miss 且工具没命中，这条记忆对她就不可见。

而且 grep 相对 FTS 有**独立价值**：子串/正则、**rank-无关**。FTS 靠分词命中，中文尤其易漏（半个词、
错位表述）；grep 找"我依稀记得的那句原话"反而更灵。所以 grep 不是检索的重复，是**互补的兜底召回**。

**关键洞察**：矛盾不在"格式"，在**工具层非对称**。当前 grep 之所以猛，部分正因为它不受控——
能搜到别 chat 的内容 = 泄漏。"猛"与"危险"同源。把能力与存储分开后，矛盾可解。

## 4. 三条解路

把"召回 miss 时 agent 能自服务搜记忆"这个**能力**，与"用什么**存储**"分开后，有三条路保留它：

- **路 A**：给 `memory_items` 配一个够强的工具（子串/正则/list/按 scope·kind·sensitivity 过滤），
  等于把 webui 面板的 API 也开成 agent 工具 → "数据库版 grep"。
- **路 B**：保留一个 Markdown 文件，但降级为 B 的**只读派生投影**（consolidation 已有 B→C 生成
  逻辑），**不再被 `memory_write` 直接写**。agent 照样能 grep，但它不再是独立可写源。
- **路 C**：A + B —— 投影给"免费 grep 路径"，工具给结构化查询。

## 5. 推荐：路 B，且严格优于现状

派生投影把"猛"与"危险"拆开：

- **rank-无关的子串召回**（治"检索排序没排上来"的 miss）→ 投影是扁平文件，grep 照样按子串命中，
  **保留**；
- **scope/sensitivity 过滤**（治泄漏）→ 投影时只投影当前 scope 可见项，**加上**。

结果：**召回覆盖不降反升**（排序 miss 能被 grep 接住），**泄漏消失**（敏感项根本不进投影文件）。
两个轴都比今天好——这是派生投影相对于"保留现状"和"纯 DB"的甜点。

> 注：上轮（讨论本提案前）曾建议"退役 Markdown"。**本节修正该建议**——因 grep 兜底能力值得保留，
> Markdown 不应退役，而应降级为只读派生投影。

## 6. 代价 / 非平凡部分

- **投影必须按 scope/sensitivity 过滤**，否则又回到泄漏。过滤规则应复用 `identity.policy` 的 read
  scope 级联 + sensitivity 过滤（与检索同一套边界）。
- **workspace ↔ scope 映射**：若 workspace per-chat，投影简单；若跨 chat 共享，投影文件需按"当前
  scope 可见集"动态生成——这是本方案唯一需要想清楚的非平凡部分。
- **失去人肉直接编辑 Markdown**：现在 owner 能打开 `MEMORY.md` 手改，B 没有这个能力。→ 必须先有
  webui 面板（带编辑/重新打标）再退役可写 C。

## 7. 建议的实施顺序（增量、可回滚）

1. **webui 面板**（先补人类编辑/可视化面）—— [webui-design.md](webui-design.md) Phase 5 已有
   `- [ ] memory 页面` 占位（约 L1216），镜像 KB 面板（`kb.py`）。
2. **`memory_write` 切到只写 B**（public 也走结构化库，C 不再被直接写）。
3. **C 降级为只读投影**（consolidation 的 B→C 投影逻辑已存在，加 scope/sensitivity 过滤即可）。
4. （可选）若觉得 grep 路径不够、需要结构化查询 → 补路 A 的 memory 工具。

每步独立可回滚。**不 big-bang**——与项目"先观察再大改"的既有风格一致（见
[memory-architecture-exploration.md](memory-architecture-exploration.md) §10 的推迟决策）。

## 8. 明确不做（本文范围外）

- **不合并 A（turns）与 B（items）** —— 角色 different，合则两伤。
- **不动 KB** —— 独立系统。
- 图引擎 / 独立库拆分 / 意图触发 scope 扩张 —— 继续 deferred。
- A2.1（当事人到场放松 `private`）—— 继续 deferred（需 identity 接入）。

## 9. 待回答的开放问题

- **standing context 怎么从 B 查**：现在整包注入 `MEMORY.md` 充当"常驻上下文"。改成 turn-build 时
  从 B 查"最近重要项"的话，查询长什么样（按 importance/recency？预算多少字符？与 retrieval 注入
  会不会重复）？
- **投影过滤的 scope 解析**：直接复用 `identity.policy.resolve_memory_read_scopes` 的级联？
- **路 A 是否值得做**：路 B 够用，还是 grep-on-memory 工具的召回增益值得多维护一个工具？
- **B'（candidates）去留**：可折叠进 items 的 metadata，或留作审计无妨——低优先。
