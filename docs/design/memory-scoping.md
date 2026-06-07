# 记忆作用域隔离设计

> 记录时间：2026-05-24
> 状态：规划中
> 相关文档：
>
> - [memory-system.md](memory-system.md#9-配置建议) — 记忆系统总体设计，§9.0 审计中标注了 scope 缺口
> - [chat-address-and-session-id.md](chat-address-and-session-id.md) — ChatAddress / SessionKey 类型系统
> - [person-identity-system.md](person-identity-system.md) — Person / Account 身份映射设计
> - [cross-session-messaging.md](cross-session-messaging.md) — 跨会话消息（依赖记忆隔离）
> - [cron-and-webapi-optimization.md](cron-and-webapi-optimization.md) — Cron 系统（主要串线来源之一）

## 1. 背景

`memory_items` 表已在 schema 中预留了 `scope_type` / `scope_id` 列和索引，但**全部应用层代码**硬编码写入 `scope_type="global"`、`scope_id="__global__"`。这导致：

1. **正常对话串线**：`_load_relevant_memory` 搜索所有全局记忆项，用户 A 的偏好会出现在用户 B 的上下文中。
2. **Cron 触发串线**：用户 A 的 cron job 执行时，agent 看到用户 B 的偏好和事实。
3. **后台 dreaming 串线**：scheduler `_dream_session` 将任何 session 的整理结果写入全局，并可能归档其他 session 的记忆项。

对话历史（`memory_turns`）已按 `session_id` 正确隔离，不存在串线。**问题仅限于长期记忆项（durable memory items）**。

## 2. 目标与非目标

### 2.1 目标

- **V1 两级模型**：`chat`（按 ChatAddress 隔离）+ `global`（共享知识），不引入更多 scope 级别。
- **零泄露**：不同 chat 的用户偏好、个人事实互不可见。
- **共享知识保留**：通用决策、操作规范、项目约定等全局记忆继续对所有 session 可见。
- **最小改动**：数据库 schema 已就绪，改动完全在应用层。

### 2.2 非目标

- **不做 workspace / channel / person / account / session / agent scope** — V1 只做 `chat` + `global`。workspace 需要与 WorkspaceManager 集成；person/account scope 见 [person-identity-system.md](person-identity-system.md)。
- **不改 `memory_turns` / `memory_keywords`** — 对话历史已按 session_id 隔离。
- **不改 FTS schema** — `memory_item_fts` 的 `scope_type` / `scope_id` 已是 UNINDEXED 列，scope 过滤在 repo 层的 JOIN 中完成。
- **不做 LLM 驱动的 scope 分类** — V1 用基于 `kind` 的简单规则分类。
- **不做向量索引 scope 预过滤** — 向量索引只存 `(embedding_id, item_id, vector)`，scope 过滤在向量搜索后通过 DB 查询完成，当前规模可接受。

## 3. V1 Scope 模型

### 3.1 Scope 类型

| scope_type | scope_id 格式 | 说明 |
|------------|--------------|------|
| `global` | `__global__` | 共享知识：通用决策、操作规范、项目约定 |
| `chat` | `{chat_key}` (如 `milky:private:10001`) | 用户/聊天私有记忆：偏好、个人事实 |

### 3.2 Scope 与 kind 的对应关系

| kind | 默认 scope | 理由 |
|------|-----------|------|
| `preference` | `chat` | 用户个人偏好 |
| `fact` | `chat` | 用户个人事实 |
| `task` | `chat` | 用户待办任务 |
| `decision` | `global` | 项目/系统级决策 |
| `procedure` | `global` | 操作规范 |
| `warning` | `global` | 通用警告 |
| `summary` | `global` | 摘要 |

### 3.3 搜索策略（Scope Cascade）

当为 typed session 加载记忆时：

```
1. 搜索 chat scope  → 用户自己的记忆（优先）
2. 搜索 global scope → 补充共享知识
3. 合并去重，chat scope 优先占用 budget
```

未 typed 的 legacy session 只搜索 global scope（行为不变）。

### 3.4 Scope 推导

scope 始终从 `session_id` 推导：

```python
def resolve_scope_from_session(session_id: str) -> tuple[str, str]:
    key = SessionKey.parse(session_id)
    if key.address.is_typed:
        return "chat", key.address.chat_key  # e.g. "milky:private:10001"
    return "global", "__global__"
```

legacy session（如 `milky:10001`）的 `target_type="unknown"`，`is_typed=False`，退回 global scope。

## 4. 需修改的代码路径

### 4.1 写入路径（全部硬编码 `"global"/"__global__"`）

| 文件 | 行号 | 当前行为 |
|------|------|---------|
| `agent/memory/consolidation.py` | 421-422 | `append_item` 写入 `scope_type="global"` |
| `agent/memory/consolidation.py` | 512-513 | `_apply_archives` 写入 candidate |
| `agent/memory/consolidation.py` | 538-539 | `_append_candidate` 写入 candidate |
| `plugins/api_bridge.py` | 335-336 | `memory_store` 默认 global |

### 4.2 读取路径（不传 scope 参数，使用默认 global）

| 文件 | 行号 | 当前行为 |
|------|------|---------|
| `core/session_runner.py` | 1189-1196 | `_load_relevant_memory` 搜索全局 |
| `agent/memory/consolidation.py` | 458 | `project_workspace_memory` 搜索全局 |
| `agent/memory/consolidation.py` | 483 | `_load_existing_items` 搜索全局 |
| `agent/memory/consolidation.py` | 560 | `_has_duplicate` 搜索全局 |
| `plugins/api_bridge.py` | 297 | `memory_search` 搜索全局 |

### 4.3 Dreaming 路径

| 文件 | 行号 | 当前行为 |
|------|------|---------|
| `scheduler/service.py` | 523 | 创建 consolidator 无 scope |
| `scheduler/service.py` | 564 | `_refresh_memory_embeddings` 仅刷新 global scope |

### 4.4 已就绪的基础设施（无需改动）

| 文件 | 说明 |
|------|------|
| `db/engine.py` Migration 008 | `memory_items` 表已有 `scope_type`/`scope_id` 列 + 索引 |
| `agent/memory/models.py` | `MemoryItem`/`MemoryCandidate` 已有 scope 字段 |
| `agent/memory/sqlite.py` | 所有方法已接受 `scope_type`/`scope_id` 参数并正确过滤 |
| `db/repositories/sqlite_memory_repo.py` | SQL 查询已包含 `WHERE scope_type = ? AND scope_id = ?` |
| `core/chat_address.py` | `ChatAddress.chat_key` 提供自然 scope_id |
| `core/context.py` | `SessionContext` 已携带 `chat_address` |

## 5. 实施路线

### Phase 0：Scope 常量与推导工具

**目标**：在单一位置定义 scope 值，提供从 session_id 推导 scope 的工具函数。

**新增文件**：

| 文件 | 内容 |
|------|------|
| `nahida_bot/agent/memory/scope.py` | scope 常量 + `resolve_scope_from_session()` + kind→scope 映射 |

```python
SCOPE_TYPE_GLOBAL = "global"
SCOPE_TYPE_CHAT = "chat"
SCOPE_ID_GLOBAL = "__global__"

CHAT_SCOPED_KINDS = frozenset({"preference", "fact", "task"})
GLOBAL_SCOPED_KINDS = frozenset({"decision", "procedure", "warning", "summary"})

def resolve_scope_from_session(session_id: str) -> tuple[str, str]: ...
def scope_for_kind(kind: str) -> str: ...
def chat_scope_id(address: ChatAddress) -> str: ...
```

**测试**：
- `resolve_scope_from_session("milky:private:10001")` → `("chat", "milky:private:10001")`
- `resolve_scope_from_session("milky:10001")` → `("global", "__global__")`
- `resolve_scope_from_session("milky:private:10001:cron:abc")` → `("chat", "milky:private:10001")`
- `scope_for_kind("preference")` → `"chat"`
- `scope_for_kind("decision")` → `"global"`

**验收标准**：
- [ ] `scope.py` 新增，常量与 `SQLiteMemoryStore` 默认值一致
- [ ] `resolve_scope_from_session` 对 typed / legacy / derived session 正确返回
- [ ] 单元测试通过

---

### Phase 1：Consolidation 写入路径接入 scope

**目标**：所有写入 `memory_items` / `memory_candidates` 的代码路径传递正确的 scope。

**修改的文件**：

| 文件 | 改动 |
|------|------|
| `agent/memory/consolidation.py` | `__init__` 新增 `scope_type`/`scope_id` 参数（默认 `"global"`/`"__global__"`）；`consolidate_turn` 新增可选 `scope_type`/`scope_id` 覆盖参数；将 421-422、512-513、538-539 的硬编码替换为实例属性或参数 |
| `core/session_runner.py` | `_consolidate_memory_after_turn` 从 `session_id` 推导 scope 并传给 `consolidate_turn` |
| `scheduler/service.py` | `_dream_session` 从 `session_id` 推导 scope，传给 `MemoryConsolidator` 构造 |
| `plugins/api_bridge.py` | `memory_store` 当 metadata 未指定 scope 时，从 `current_session` 推导默认值 |

**关键设计决策**：

1. **构造函数默认值 + 每次调用覆盖**：`MemoryConsolidator.__init__` 接受默认 scope（scheduler dreaming 用），`consolidate_turn` 接受可选覆盖（SessionRunner 按 turn 传入）。两者向后兼容。
2. **`_load_existing_items` 和 `_has_duplicate` 也必须按 scope 过滤**：否则 consolidator 会看到其他 chat 的项目，错误地将共享内容识别为"重复"而跳过，导致数据静默丢失。
3. **kind→scope 映射在写入时决定**：根据 `memory.kind` 使用 `scope_for_kind()` 确定 `scope_type`，而非将整个 session 的所有提取都写入同一 scope。一个 session 的 dreaming 可能同时产生 `preference`（chat scope）和 `decision`（global scope）。

**测试**：
- consolidator 写入的 preference 项具有 `scope_type="chat"`
- consolidator 写入的 decision 项具有 `scope_type="global"`
- 不同 chat scope 之间的 `_has_duplicate` 不交叉检测
- 现有测试通过（构造函数默认值保持向后兼容）

**验收标准**：
- [ ] 来自 typed session 的用户偏好写入 `scope_type="chat"`、`scope_id="milky:private:10001"`
- [ ] 来自 legacy session 的内容仍写入 `scope_type="global"`
- [ ] `api_bridge.memory_store` 未指定 scope 时自动推导
- [ ] 现有测试未修改通过

---

### Phase 2：记忆加载读取路径接入 scope

**目标**：为 typed session 加载记忆时，先搜 chat scope，再搜 global scope，合并结果。

**修改的文件**：

| 文件 | 改动 |
|------|------|
| `core/session_runner.py` | `_load_relevant_memory` 增加 scope cascade：从 `current_session` 获取 scope，依次搜索 chat scope 和 global scope，合并去重 |
| `plugins/api_bridge.py` | `memory_search` 增加 scope cascade，与 `_load_relevant_memory` 同策略 |

**关键设计决策**：

1. **调用者端 cascade**：scope cascade 是 session 级别的关注点，不放在 `SQLiteMemoryStore`。Store 继续接受明确的 scope 参数，保持可测试性。
2. **Chat scope 优先占用 budget**：先搜 chat scope，剩余 budget 再搜 global scope，确保用户自己的记忆总是优先展示。
3. **仅 typed session 走 cascade**：legacy session 继续只搜 global（行为不变）。
4. **三种搜索模式（FTS / vector / hybrid）均走 cascade**：对每次搜索调用分别传入 scope 参数。

**搜索伪代码**：

```python
scope_type, scope_id = resolve_scope_from_session(session_id)

if scope_type == "chat":
    # 先搜 chat scope
    chat_items = await search(query, scope_type="chat", scope_id=scope_id, limit=limit)
    # 剩余 budget 搜 global
    remaining = limit - len(chat_items)
    if remaining > 0:
        global_items = await search(query, scope_type="global", scope_id="__global__", limit=remaining)
        seen = {item.item_id for item in chat_items}
        chat_items += [i for i in global_items if i.item_id not in seen]
    items = chat_items
else:
    items = await search(query, limit=limit)  # 默认 global
```

**测试**：
- 用户 A 的 chat 只看到自己的 preference + global decision，看不到用户 B 的 preference
- 用户 B 的 chat 只看到自己的 preference + global decision，看不到用户 A 的 preference
- legacy session 行为不变

**验收标准**：
- [ ] typed session 的记忆搜索结果不包含其他 chat 的私有记忆
- [ ] global 共享知识对所有人可见
- [ ] legacy session 搜索行为不变
- [ ] 三种搜索模式（FTS / vector / hybrid）均正确隔离

---

### Phase 3：Dreaming scope 隔离与 Embedding 刷新

**目标**：后台 dreaming 按 session scope 整理记忆，embedding 刷新覆盖所有 scope。

**修改的文件**：

| 文件 | 改动 |
|------|------|
| `scheduler/service.py` | `_dream_session` 从 `session_id` 推导 scope 传给 consolidator（Phase 1 已覆盖） |
| `scheduler/service.py` | `_refresh_memory_embeddings` 改为刷新所有 scope 的 items，不限于 global |
| `agent/memory/sqlite.py` | 新增 `embed_items_all_scopes()` 方法 |
| `db/repositories/sqlite_memory_repo.py` | 新增 `list_memory_items_all_scopes()` 查询 |

**关键设计决策**：

1. **Embedding 是 scope 无关的**：向量为文本内容生成，scope 变更不需要重算 embedding。`embed_items_all_scopes` 仅确保新增的 chat-scoped items 也被嵌入。
2. **Dreaming 只归档自己 scope 内的 items**：session A 的 dreaming 不会归档 session B 的 chat-scoped items。这通过 Phase 1 的 `_load_existing_items` 按 scope 过滤实现——LLM dreamer 看不到其他 chat 的 items。

**测试**：
- session A 的 dreaming 写入 chat-scoped items
- session B 的 dreaming 不会归档 session A 的 items
- `embed_items_all_scopes` 覆盖 global 和 chat scope

**验收标准**：
- [ ] 后台 dreaming 的整理结果写入正确的 chat scope
- [ ] embedding 刷新覆盖所有 scope 的 items
- [ ] 不同 session 的 dreaming 不互相干扰

---

### Phase 4：现有全局数据迁移

**目标**：将用户特定的全局记忆项迁移到正确的 chat scope。

**新增文件**：

| 文件 | 内容 |
|------|------|
| `scripts/migrate_memory_scope.py` | 一次性迁移脚本：`inspect`（分析）/ `apply`（执行） |

**修改的文件**：

| 文件 | 改动 |
|------|------|
| `agent/memory/sqlite.py` | 新增 `update_item_scope()` 方法 |
| `db/repositories/sqlite_memory_repo.py` | 新增 `update_memory_item_scope()` SQL |

**迁移策略**：

1. 查询所有 `scope_type="global"`、`status="active"` 的 items。
2. 对每个 item，从 `evidence_json` / `metadata_json` 中提取 `session_id`。
3. 如果 `session_id` 可解析为 typed ChatAddress，且 `kind` 属于 `CHAT_SCOPED_KINDS`（preference / fact / task），则迁移到 chat scope。
4. `kind` 属于 `GLOBAL_SCOPED_KINDS`（decision / procedure / warning / summary）的保留在 global。
5. 无法推断 `session_id` 或 legacy session 的 items 保留在 global（安全默认）。
6. `inspect` 先只读分析，输出迁移计划。`apply` 需显式执行，先自动备份 DB。

**迁移后**：
- 调用 `embed_items_all_scopes()` 刷新 embedding。
- 验证 chat-scoped items 不出现在 global 搜索中。

**测试**：
- 创建 global scope 的 preference item + 已知 session_id → inspect 建议迁移 → apply 执行迁移
- 创建 global scope 的 decision item → inspect 不建议迁移
- 创建无 session_id 的 item → inspect 不建议迁移
- 迁移后搜索结果正确隔离

**验收标准**：
- [ ] `inspect` 输出完整迁移计划，标注每条 item 的建议动作
- [ ] `apply --dry-run` 不修改数据库
- [ ] `apply` 前自动备份 SQLite
- [ ] 迁移后 chat-scoped items 仅出现在对应 chat 的搜索中
- [ ] global 共享知识保持全局可见

---

### Phase 5：验证与文档更新

**目标**：端到端验证，更新相关文档。

**任务**：
- [ ] 端到端隔离测试：两个 session 完整走 consolidation → search → dreaming 流程，验证零泄露
- [ ] 更新 `memory-system.md` §9.0 审计状态，将 scope 缺口标记为已完成
- [ ] 更新 `memory-system.md` §3.1 多作用域隔离状态
- [ ] 全局审计：grep 确认无残留的硬编码 `"global"` / `"__global__"` 不在使用常量或有意默认值的位置
- [ ] `uv run pyright` → 0 errors
- [ ] `uv run ruff check` → all passed
- [ ] `uv run pytest` → all passed

---

## 6. 与身份系统的边界

V1 的 `chat` + `global` 两级 scope 只解决一个问题：不同聊天入口之间的长期记忆不再互相泄露。它不能表达“这个 QQ 账号、这个 Telegram 账号、这个群成员身份是同一个人”。

这部分应由独立的身份系统处理，完整设计见 [person-identity-system.md](person-identity-system.md)。该设计引入：

- `AccountKey`：一个平台账号，例如 `milky:user:10001`。
- `Person`：bot 本地认识的一个真实聊天对象。
- `ParticipantObservation`：某个账号在某个群/频道中的显示名、角色和出现记录。
- `person` / `account` memory scope：个人事实和偏好不再依赖聊天入口。

关键修正：身份系统不应只是在搜索时“把同一个人的所有 chat scope 都搜一遍”。这对私聊勉强可行，但在群聊中会把某个群成员的个人事实错误地当作整个群的共享记忆。正确方向是：

```text
ChatAddress  -> 消息在哪里
SessionKey   -> 对话历史是哪条 lane
AccountKey   -> 这条消息由哪个平台账号发出
Person       -> 哪些账号属于同一个真实聊天对象
```

长期个人记忆应写入 `person:{person_id}` 或 `account:{account_key}`；群/频道规则才写入 `chat:{chat_key}`；系统知识写入 `global:__global__`。

LLM 仍然不负责决定具体 scope key。它最多输出受控 subject，例如 `current_sender`、`current_chat`、`global`；系统根据当前 `IdentityResolution` 把 subject 映射到确定的 scope。

因此，本文档的 V1 `chat` scope 是过渡隔离层，不是最终用户身份模型。实现 person/account scope 后，`preference` / `fact` / `task` 的默认写入规则需要从“按 chat”升级为“按当前发送者 person/account”，并保留群聊隐私策略。

---

## 7. 依赖关系

```
Phase 0 (scope 常量)          ⬜
  └─→ Phase 1 (写入路径)      ⬜
        └─→ Phase 2 (读取路径) ⬜
        └─→ Phase 3 (dreaming) ⬜  (可与 Phase 2 并行)
              └─→ Phase 4 (迁移) ⬜
                    └─→ Phase 5 (验证) ⬜
```

Phase 0 是所有后续的前置。Phase 1 完成后，Phase 2 和 Phase 3 可以并行。Phase 4 需要等 Phase 1-3 完成，确保新数据和旧数据的 scope 一致。Phase 5 是最终验证。

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 迁移错误 scope 化 items | 用户记忆放入错误的 chat 或丢失 | 先 dry-run；基于 kind 的保守分类；global items 永远不会被错误 scope 化 |
| Legacy session 不获隔离 | 两段式 session 的用户数据仍可能泄露 | 可接受：legacy session 是已知局限；ChatAddress 重构 Phase 4b 完成后 legacy session 将被清理 |
| Scope cascade 双重搜索增加延迟 | 每次 `_load_relevant_memory` 两次 DB 查询 | limit 通常 5-10 项，两次 FTS 查询对小 DB 很快；后续可 profiling 优化 |
| Dreaming 将应属 global 的知识写入 chat scope | 共享知识被困在一个 chat 中 | kind 分类保守；decision/procedure/warning/summary 保持 global；可后续添加管理员命令手动迁移 |
| 现有测试因 scope 变更失败 | consolidator 构造函数默认值保持 `"global"`/`"__global__"` | 所有现有测试不传 scope 参数，使用默认值，行为不变 |

## 9. 关键文件索引

| 文件 | 角色 |
|------|------|
| `nahida_bot/agent/memory/consolidation.py` | 整合器写入路径（泄露源头） |
| `nahida_bot/core/session_runner.py` | 记忆加载读取路径 + 整合调用点 |
| `nahida_bot/scheduler/service.py` | 后台 dreaming + embedding 刷新 |
| `nahida_bot/plugins/api_bridge.py` | 插件 memory_search / memory_store |
| `nahida_bot/agent/memory/sqlite.py` | Store 实现（scope 参数已就绪） |
| `nahida_bot/agent/memory/models.py` | MemoryItem / MemoryCandidate 数据模型 |
| `nahida_bot/db/repositories/sqlite_memory_repo.py` | SQL 查询（scope 过滤已就绪） |
| `nahida_bot/core/chat_address.py` | ChatAddress / SessionKey 类型系统 |
| `nahida_bot/core/context.py` | SessionContext（携带 chat_address） |
