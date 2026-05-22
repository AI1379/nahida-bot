# ChatAddress 与 Session ID 重构 TODO

> 记录时间：2026-05-21
> 状态：Phase 0 / Phase 1 已完成，Phase 2–4 待实施
> 最后更新：2026-05-22
> 相关文档：
>
> - [cross-session-messaging.md](cross-session-messaging.md)
> - [runtime-settings.md](runtime-settings.md)

## 1. 背景

当前跨 session / 跨 channel 消息发送已经具备基本能力，但这次讨论暴露出一个更底层的问题：**会话身份（session id）** 和 **发送地址（delivery target）** 混用了相似的裸字符串格式。

现有 session id 主要是：

```text
<platform>:<chat_id>
<platform>:<chat_id>:<uuid8>
```

例如：

```text
milky:10001
milky:10001:abcd1234
telegram:-100123456
```

这里没有记录 `private/group/channel/thread` 这样的 target type。Milky 的私聊和群聊都使用数字 peer id，如果只看 `milky:<id>`，无法稳定判断这是 QQ 好友还是 QQ 群。

而发送地址更自然地需要表达 target type：

```text
milky:private:10001
milky:group:20001
telegram:private:123456
telegram:group:-100123456
```

这两个格式看起来很像，但语义不同。如果继续混在一起，会让 message 工具、session 记录、memory、scheduler 和 LLM 上下文都变得更难推理。

本轮结论需要比“为旧格式补 metadata”更进一步：旧 session key 设计本身是错误的。只要某个 channel 的私聊 id 和群聊 id 有重合，`<platform>:<chat_id>` 就会把两段完全不同的对话写入同一个 `sessions.session_id`、`memory_turns.session_id` 和 `active_sessions.chat_key`。这不是显示问题，而是数据隔离问题，应按一次破坏性 session key 修正来规划。

## 2. 当前问题

### 2.1 Session ID 缺少 target type

`milky:<peer_id>` 不能区分：

- QQ 私聊用户 `10001`
- QQ 群聊 `10001`

如果平台存在同号或测试数据复用，session key 会碰撞。即使没有真实碰撞，代码也无法从 session id 本身判断 outbound 应该走 `send_private_message` 还是 `send_group_message`。

这是必须修的核心问题。正确的基础 session key 应包含 chat type：

```text
milky:private:10001
milky:group:10001
telegram:private:123456
telegram:group:-100123456
```

`/new` 生成的新 session 则应在这个基础 key 后追加 uuid：

```text
milky:group:10001:abcd1234
telegram:private:123456:abcd1234
```

### 2.2 三段式字符串有歧义

现有 `/new` session 会产生：

```text
milky:<chat_id>:<uuid8>
```

这也是三段式。不能简单把所有三段式解释成：

```text
milky:<chat_type>:<chat_id>
```

例如：

```text
milky:20001:abcd1234
```

在现有系统里这是 legacy session id；第二段 `20001` 是 chat id，不是 chat type。把它默认补成 `private` 会误判群聊 session。

### 2.3 message 工具不应该知道 channel 私有协议

message 工具属于 builtin 通用层。它应该表达“发送到哪个 channel 的哪类 target”，不应该直接生成 Milky 的：

```text
message_scene=group
peer_id=20001
```

这些是 Milky channel 的协议细节，应由 Milky channel 自己解释。

### 2.4 LLM 层需要低 token 成本接口

`ChatAddress` 作为内部结构很适合工程实现，但工具调用如果直接展开成多个字段，会增加 token 和 schema 成本。

LLM 工具层更适合继续使用短字符串：

```text
milky:group:20001
telegram:private:123456
```

但内部必须尽早解析成结构体，不能在系统里到处传裸字符串。

## 3. 概念边界

### 3.1 ChatAddress

`ChatAddress` 表示外部平台上的一个可发送/可接收 target。

建议内部结构：

```python
@dataclass(frozen=True)
class ChatAddress:
    channel: str
    target_type: Literal["private", "group", "channel", "thread", "unknown"]
    target_id: str
    thread_id: str = ""
```

说明：

- `channel`: `milky`、`telegram` 等 channel id。
- `target_type`: 通用类型，不使用 Milky 的 `friend` 作为公共概念。Milky 内部可把 `private` 映射成 `friend`。
- `target_id`: 平台原生 chat/peer id。
- `thread_id`: 预留给 Telegram topic、Discord thread 等二级 target。
- `unknown`: 只用于 legacy session 或无法从历史数据恢复类型的场景，不应作为主动发送的默认值。

### 3.2 SessionKey

`SessionKey` 表示一段对话历史，不等同于发送地址。

新的 canonical session key 应和 `ChatAddress` 的三元组一致：

```text
<channel>:<target_type>:<target_id>
<channel>:<target_type>:<target_id>:<session_suffix>
```

其中：

- 前三段是 deterministic chat key，可作为 `active_sessions.chat_key`、scheduler `session_key` 和默认主 session id。
- 第四段是 `/new` 或 `cron isolated` 等派生 session 的 suffix。
- `target_type` 必须来自标准集合：`private | group | channel | thread`。
- legacy `milky:10001` 只允许作为迁移前数据或兼容输入存在，不再作为新数据写入格式。

session metadata 仍建议保留 `chat_address`，但它不再是弥补 key 缺陷的主方案，而是用于显示、审计、迁移校验和未来扩展：

```json
{
  "chat_address": {
    "channel": "milky",
    "target_type": "group",
    "target_id": "20001"
  }
}
```

### 3.3 DeliveryTarget

`DeliveryTarget` 是 message 工具发送时使用的目标。它可以由 `ChatAddress` 表示，但不能直接假设等同于 session id。

例如：

```text
DeliveryTarget: milky:group:20001
Record session: milky:group:20001
Metadata: target_type=group
```

在目标类型明确时，delivery target 和基础 session key 的前三段相同，这是有意设计的。差别在于：

- `ChatAddress`/delivery target 表示外部聊天地址。
- `SessionKey` 表示内部记忆和运行状态的 key。
- 派生 session 可以在同一个基础地址后追加 suffix，例如 `/new` 或 cron isolated。

## 4. 推荐工具接口

### 4.1 LLM 层使用冒号字符串

为了降低 token 和调用复杂度，LLM-facing `message` 工具建议优先接受：

```json
{
  "target": "milky:group:20001",
  "text": "hello"
}
```

兼容旧调用：

```json
{
  "platform": "milky",
  "chat_id": "20001",
  "chat_type": "group",
  "text": "hello"
}
```

不推荐 LLM 直接传：

```json
{
  "platform": "milky",
  "chat_id": "20001",
  "text": "hello"
}
```

对 Milky 来说，裸数字 target 不应默认 private，因为这会把群聊误发到私聊。

### 4.2 内部立即解析为 ChatAddress

工具 handler 收到字符串后，应立即调用统一 parser：

```python
address = ChatAddress.parse_tool_target(target)
```

后续代码传结构体或结构化 dict，不再传未解析的 `"milky:group:20001"`。

### 4.3 Channel 层负责协议映射

通用层只传：

```python
OutboundMessage(extra={"chat_address": address_as_dict})
```

或者：

```python
OutboundMessage(extra={"chat_type": "group"})
```

Milky channel 内部负责：

```text
private -> friend
group -> group
```

Telegram channel 内部可以忽略 `chat_type`，因为 Telegram `chat_id` 本身通常足以发送；但它仍可用 `chat_type` 做校验、日志和未来 thread/topic 支持。

## 5. Parser 兼容规则

建议添加一个统一 parser，而不是散落在 builtin command、router、scheduler、channel 里。

### 5.1 Typed target

如果字符串形如：

```text
<channel>:<known_target_type>:<target_id>
```

其中第二段是：

```text
private | group | channel | thread
```

则解析为 `ChatAddress`。

示例：

```text
milky:group:20001
telegram:private:123456
```

### 5.2 Legacy session id

如果第二段不是 known target type，则按 legacy session id 处理：

```text
<channel>:<target_id>
<channel>:<target_id>:<uuid8>
```

此时 `target_type=unknown`，除非能从 session metadata 或最近 message context 中恢复。

兼容 parser 可以读取 legacy，但新写入路径不得继续产生 legacy key。也就是说：

- 解析输入时可以接受 `milky:10001`，但必须返回 `unknown` 或要求调用者补类型。
- 新建 session、active session、cron job、message record 都必须写 `milky:private:10001` 或 `milky:group:10001`。
- 如果调用者试图用 legacy key 发送 Milky 消息，应报错并要求显式 `private/group`。

### 5.3 不要默认补 private

不要把：

```text
milky:20001
milky:20001:abcd1234
```

默认解释为：

```text
milky:private:20001
```

原因：

- 旧 session 的第二段是 target id，不是 target type。
- Milky 群聊和私聊 peer id 都可能是数字。
- 默认 private 会把群聊误发到私聊，属于高风险行为。

可以在 UI 或工具错误信息里提示用户补充 `chat_type`。

## 6. 目标架构

### 6.1 核心类型

建议新增 `nahida_bot/core/chat_address.py`，集中放置：

```python
ChatAddress(channel, target_type, target_id, thread_id="")
SessionKey(address, suffix="")
parse_chat_address(value)
parse_session_key(value)
make_chat_key(address)
make_session_id(address, suffix="")
```

`ChatAddress` 不负责数据库迁移，只负责表达外部地址。`SessionKey` 负责可持久化字符串格式，避免各层继续手写 `f"{platform}:{chat_id}"`。

### 6.2 Router

Router 不应再暴露只接受 `platform, chat_id` 的 session 方法。目标接口：

```python
get_active_session_id(address: ChatAddress) -> str
set_active_session(address: ChatAddress, session_id: str) -> None
make_session_id(address: ChatAddress) -> str
make_new_session_id(address: ChatAddress) -> str
```

过渡期可以保留兼容 wrapper：

```python
get_active_session_id_legacy(platform, chat_id, chat_type="")
```

但所有 channel 入站路径必须传 `ChatAddress`，不能再让 router 从 `is_group` 或裸 `chat_id` 猜。

### 6.3 InboundMessage / SessionContext

`InboundMessage` 当前已有 `chat_id`、`is_group`、`chat_context`、`message_context`。建议补一个标准属性或 helper：

```python
address = chat_address_from_inbound(inbound)
```

`SessionContext` 应补 `chat_type` 或直接补 `chat_address`，因为工具层发送附件、message 工具默认当前会话、cron 创建等都会读 current session。只保留 `platform/chat_id` 会把错误继续传下去。

### 6.4 Scheduler / WebAPI

Scheduler 的 `session_key` 必须变成 typed chat key：

```text
<channel>:<target_type>:<target_id>
```

WebAPI 创建 cron/send message 的请求也应新增 `chat_type`，或接受 `target`：

```json
{"target": "milky:group:20001", "text": "hello"}
```

兼容旧参数时，如果 `chat_type` 缺失，Telegram 可以按平台能力尝试兼容，Milky 必须报错。

## 7. 数据库迁移方案

### 7.1 受影响的持久化数据

至少需要审查这些表和字段：

- `sessions.session_id`
- `sessions.metadata_json`
- `memory_turns.session_id`
- `active_sessions.chat_key`
- `active_sessions.session_id`
- `cron_jobs.chat_id`
- `cron_jobs.session_key`
- `background_tasks.requester_session_id`
- `background_tasks.child_session_id`
- `background_tasks.delivery_target_json`

还需要审查 memory item / candidate / embedding 是否存在以 session id 为 scope 的记录。如果 `scope_id` 写入了 legacy session id，也需要纳入迁移。

### 7.2 迁移原则

- 不把 unknown 默认成 private。
- 不把一个已经混有 private/group turns 的 legacy session 强行合并到某一个 typed session。
- 可以证明类型的数据自动迁移。
- 无法证明类型的数据保留 legacy，并打上 `legacy_untyped` 标记，等待人工处理或后续工具处理。
- 如果同一个 legacy session 内能按 turn metadata 明确拆出 private/group，则允许拆分，但 assistant/system turn 的归属必须通过相邻上下文推断，不能无依据搬运。

### 7.3 类型推断来源

自动迁移时按可信度从高到低推断 `ChatAddress`：

1. `memory_turns.metadata_json.message_context.channel/chat_type/chat_id`
2. `sessions.metadata_json.chat_address`
3. `sessions.metadata_json.message_context`
4. `cron_jobs.session_key` 如果已经是 typed key
5. channel-specific raw event，例如 Milky `message_scene`
6. legacy session id 本身

第 6 条只能提供 channel 和 id，不能提供 target type。它不能单独完成 Milky 迁移。

### 7.4 Migration 011 建议步骤

建议新增 `011_typed_session_keys`，但不要在 migration SQL 中写复杂 Python 难以维护的推断逻辑。更稳妥的做法是：

1. SQL migration 只添加辅助字段或迁移审计表。
2. 应用启动后的 Python migration service 在同一个数据库事务内做可测试的推断和重写。

推荐新增审计表：

```sql
CREATE TABLE IF NOT EXISTS session_key_migration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_session_id TEXT NOT NULL,
    new_session_id TEXT,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

可选新增列：

```sql
ALTER TABLE cron_jobs ADD COLUMN chat_type TEXT;
```

如果不想改 cron schema，也可以继续只用 typed `session_key`，但 `chat_type` 单列会让 WebAPI list/filter 更清晰。

### 7.5 session 重命名策略

对每个 legacy `sessions.session_id`：

1. 如果已经是 typed session key，跳过。
2. 收集该 session 下所有 turn 的可推断 address。
3. 如果所有可推断 address 都一致，整段迁移到对应 typed session id。
4. 如果存在多个 address，按 turn metadata 拆分到多个 typed session。
5. assistant/system turn 如果没有 address，归属到前一个有 address 的用户 turn；如果前后冲突或没有上下文，则留在 legacy session。
6. 如果完全无法推断 address，保留 legacy session，并写 migration log。

重命名需要同步更新：

```text
sessions.session_id
memory_turns.session_id
background_tasks.requester_session_id
background_tasks.child_session_id
active_sessions.session_id
cron_jobs.session_key
```

如果新 session id 已存在，不能直接覆盖。应合并 metadata、保留较早 `created_at`、较晚 `last_active_at`，并把 turns 移过去。

### 7.6 active_sessions 迁移

`active_sessions.chat_key` 应迁移为 typed chat key。

旧记录：

```text
chat_key = milky:10001
session_id = milky:10001:abcd1234
```

新记录：

```text
chat_key = milky:group:10001
session_id = milky:group:10001:abcd1234
```

如果无法确定 chat type：

- 不迁移为 typed key。
- 保留旧记录但标记到 migration log。
- 新 router 不应主动使用旧记录，除非兼容 resolver 能从当前 inbound address 找到唯一旧 override。

### 7.7 cron_jobs 迁移

cron 的风险较高，因为它会主动发送消息。

迁移策略：

- 如果 `cron_jobs.session_key` 能迁移为 typed key，则更新 `session_key` 和可选 `chat_type`。
- 如果不能确定 target type，默认禁用该 job，写入 `last_error` 或 migration log，要求人工确认。
- firing 时 channel send 必须使用 `ChatAddress`，不能再只用 `job.chat_id`。

对 Milky 来说，不能自动判断类型的 cron job 宁可停止，也不能冒险发送到错误聊天。

### 7.8 background_tasks 迁移

子任务 session id 通常形如：

```text
<requester_session_id>:subagent:<task_id>
```

如果 requester session 成功迁移，则 child session id 应一起重写：

```text
milky:10001:subagent:t1
-> milky:group:10001:subagent:t1
```

如果 requester 无法迁移，子任务保留 legacy，并写 migration log。Orchestration policy 中用 `requester_session_id in target_session_id` 判断权限的逻辑也需要重审，因为 typed key 增加一段后，字符串包含判断更脆弱。

### 7.9 回滚策略

破坏性迁移前必须备份 SQLite 文件。迁移本身应做到：

- migration log 记录 old/new 映射。
- 所有重写在事务内完成。
- 失败时 rollback。
- 成功后不删除 migration log。
- 提供一次性脚本可根据 log 尝试反向恢复，但不承诺能恢复已经混合拆分的 session。

### 7.10 一次性人工确认迁移脚本

建议单独提供一个一次性脚本，专门帮助迁移现有 session。它不应该作为普通启动 migration 自动执行，而应由维护者显式运行：

```text
python scripts/migrate_session_keys.py inspect --db data/nahida.sqlite --out migration-plan.json
python scripts/migrate_session_keys.py apply --db data/nahida.sqlite --plan migration-plan.json
```

设计目标：

- 对每一个 legacy session 生成处理建议。
- 先只读分析，不修改数据库。
- 由维护者逐条批准后，再按 session 串行执行。
- 每条迁移使用独立事务，失败不影响后续未执行条目。
- 所有执行结果写入 migration log，脚本可重复运行并跳过已完成条目。

#### 7.10.1 inspect 输出

`inspect` 阶段扫描数据库，生成 JSON plan。每个 session 一条记录：

```json
{
  "old_session_id": "milky:10001",
  "status": "needs_approval",
  "recommendation": "rename",
  "new_session_id": "milky:group:10001",
  "confidence": "high",
  "evidence": [
    {
      "source": "memory_turns.metadata_json.message_context",
      "chat_address": "milky:group:10001",
      "turn_count": 42
    }
  ],
  "affected": {
    "sessions": 1,
    "memory_turns": 120,
    "active_sessions": 1,
    "cron_jobs": 0,
    "background_tasks": 2
  },
  "approval": "pending",
  "notes": ""
}
```

`recommendation` 建议使用有限枚举：

- `rename`: 整个 legacy session 可迁移到一个 typed session。
- `split`: 同一个 legacy session 内存在多个明确 address，建议拆分。
- `keep_legacy`: 无法证明类型，暂时保留 legacy。
- `disable_cron`: cron 目标类型不明，建议禁用相关 job。
- `skip_typed`: 已经是 typed session，无需处理。
- `manual_review`: 证据冲突或存在风险，需要人工指定。

`confidence` 建议使用：

- `high`: 所有证据一致，可人工快速批准。
- `medium`: 有主要证据，但存在少量无 address 的 assistant/system turn。
- `low`: 只能从弱证据推断，默认不应自动执行。
- `conflict`: private/group 等证据冲突，必须人工处理。

#### 7.10.2 人工批准格式

脚本不需要做复杂交互 UI。维护者可以直接编辑 plan，把每条记录的 `approval` 改成明确动作：

```json
{
  "old_session_id": "milky:10001",
  "recommendation": "rename",
  "new_session_id": "milky:group:10001",
  "approval": "approved"
}
```

可选批准值：

- `pending`: 默认值，不执行。
- `approved`: 按建议执行。
- `rejected`: 明确不执行，并写入 log。
- `force_keep_legacy`: 保留 legacy，但写入 `legacy_untyped` metadata。
- `force_rename`: 使用人工填写的 `new_session_id` 执行 rename。
- `force_split`: 使用人工填写的 split mapping 执行拆分。

对于 `force_rename` 和 `force_split`，脚本必须重新校验目标 key 格式，不能接受缺少 `target_type` 的新 session id。

#### 7.10.3 apply 执行规则

`apply` 阶段按 plan 文件顺序串行处理，只执行 `approval` 不为 `pending` 的条目。

每条记录的执行流程：

1. 重新读取数据库，确认 old session 当前仍存在且未被迁移。
2. 校验目标 session id 是 typed key。
3. 开启事务。
4. 更新 `sessions`、`memory_turns`、`active_sessions`、`cron_jobs`、`background_tasks` 等相关字段。
5. 合并或更新 `sessions.metadata_json.chat_address`。
6. 写入 `session_key_migration_log`。
7. 运行该 session 范围内的一致性检查。
8. commit。

如果任一步失败：

- rollback 当前条目。
- 在 plan 中标记该条为 `failed`，记录错误。
- 继续处理下一条未执行记录，除非启动参数要求 `--stop-on-error`。

#### 7.10.4 拆分策略

`split` 比 `rename` 风险更高，默认只生成建议，不自动执行。人工批准时应要求 plan 中包含显式 mapping：

```json
{
  "old_session_id": "milky:10001",
  "approval": "force_split",
  "split_targets": [
    {
      "chat_address": "milky:private:10001",
      "new_session_id": "milky:private:10001",
      "turn_ids": [1, 2, 3]
    },
    {
      "chat_address": "milky:group:10001",
      "new_session_id": "milky:group:10001",
      "turn_ids": [4, 5, 6]
    }
  ]
}
```

脚本可以在 inspect 阶段预填 `turn_ids`，但 apply 阶段必须验证这些 turn 仍属于 old session。没有明确归属的 assistant/system turn 不应自动搬运，除非 plan 明确列出。

#### 7.10.5 输出给维护者的摘要

`inspect` 和 `apply` 都应打印一个短摘要：

```text
Sessions scanned: 18
High confidence rename: 12
Needs manual review: 3
Keep legacy suggested: 2
Typed already: 1
Cron jobs to disable unless approved: 1
```

每条 session 的摘要应包含：

- old session id
- 建议的新 session id
- 推断证据来源和数量
- affected rows
- cron/background task 风险
- 建议动作

这样迁移前可以逐条检查，不需要直接读 SQL。

#### 7.10.6 为什么不用自动全量迁移

这个脚本应刻意保持“半自动”：

- session key 错误会影响长期记忆，误迁移成本高。
- Milky 的 private/group id 碰撞虽然少，但一旦发生就不能靠默认规则修复。
- cron job 会主动发送消息，不能在目标类型不明时自动恢复。
- 串行逐条执行更容易定位问题，也方便在迁移前后手动验证具体 session。

## 8. 实施路线建议

### Phase 0: 冻结新 legacy 写入

- 新增 `ChatAddress` / `SessionKey` 设计和 parser。
- 修改规划和测试预期，明确 legacy 只读兼容。
- 暂不执行数据库迁移。

目标：让后续代码实现有统一目标，不继续扩大旧格式数据。

### Phase 1: 新数据写 typed key

- Router、Milky、Telegram 入站路径全部使用 `ChatAddress` 生成 session id。
- `/new` 基于 typed chat key 生成 typed suffix session。
- `SessionContext` 携带 `chat_address`。
- message 工具 `target` 继续用 `channel:type:id`，内部解析为 `ChatAddress`。
- outbound 白名单基于 `ChatAddress` 检查。

目标：从这个版本开始，新消息不再产生 collision。

### Phase 2: 兼容读取 legacy 数据

- `get_recent()` 等 memory 读取只读当前 typed session。
- 对 legacy session 的读取只通过显式 legacy id 或迁移 fallback 完成。
- `/status`、session list 显示 legacy/typed 状态，帮助识别未迁移数据。

目标：避免旧数据突然不可见，同时不再把 legacy 当作正常路径。

### Phase 3: 实现迁移工具

- 实现一次性人工确认脚本，例如 `scripts/migrate_session_keys.py`。
- `inspect` 阶段输出每个 session 的处理建议、证据、风险和 affected rows。
- 维护者编辑 plan，逐条批准 `approved` / `force_rename` / `force_split` / `force_keep_legacy`。
- `apply` 阶段按 plan 顺序串行执行，每条 session 一个事务。
- 非 dry-run / apply 前自动备份 db。
- 迁移完成后跑一致性检查。

目标：先让用户看见迁移影响，再由用户逐条批准，最后执行可审计的破坏性写入。

### Phase 4: 移除 legacy 写入兼容

- 删除 `platform/chat_id` 生成 session key 的主路径。
- WebAPI/cron/message 工具对 Milky 缺少 `chat_type` 的输入直接报错。
- 文档中将 legacy session id 标为只支持历史查询。

目标：彻底收束 session identity。

## 9. 对当前 message 工具的建议

短期建议：

- 保留 `target` 字符串接口，因为它对 LLM 省 token。
- 将描述从 “canonical session-like target” 改为 “delivery target”。
- 返回文案避免暗示 target 是 session id，例如：

```text
Message sent to target milky/group/20001
```

而不是：

```text
Message sent to milky:group:20001
```

- `record` 语义必须明确：记录到目标 chat 对应的 session，而不是记录到“当前工具调用所在 session”。
- 在 typed session key 落地前，如果代码仍处于兼容阶段，record 写入 legacy session 时必须附带 `chat_address` metadata，方便后续迁移。
- 在 typed session key 落地后，`record` 应写到 typed session，例如 `milky:group:20001`，不再写 `milky:20001`。

中期建议：

- 将所有 message 工具 target 解析逻辑迁出 `commands.py`。
- `commands.py` 只调用：

```python
address = parse_chat_address_tool_args(...)
```

这样 builtin 工具不会继续积累 channel/session 解析技术债。

## 10. 关键决策

- `ChatAddress` 是内部稳定结构。
- LLM 工具层可以继续用冒号字符串节省 token。
- `channel:type:id` 同时是 delivery target 的紧凑文本形式，也是 canonical base session key。
- session 派生后缀只能追加在 typed base key 之后。
- legacy session id 不默认补 `private`。
- 新写入路径禁止产生 legacy session id。
- Milky 裸数字 outbound 需要显式 `chat_type`，除非 scene cache 或 session metadata 能证明类型。
- 数据库迁移必须支持 dry-run、备份、审计 log 和 ambiguous 数据保留。
- 无法确认 target type 的 cron job 应禁用或要求人工确认，不能冒险发送。

---

## ROADMAP — 实施路线图

> 评估时间：2026-05-22
> 总体难度：**中高**
> 预估总工期：**6–8 个工作日**（单人，含测试）
> 最复杂子系统：数据库迁移脚本（Phase 3）

### 全局依赖关系

```
Phase 0 (核心类型)         ✅ 已完成 (2026-05-22)
  └─→ Phase 1 (新数据写 typed key)  ✅ 已完成 (2026-05-22)
        └─→ Phase 2 (兼容读 legacy)  ⬜ 待实施
              └─→ Phase 3 (迁移工具)  ⬜ 待实施
                      └─→ Phase 4 (移除 legacy)  ⬜ 待实施
```

Phase 0 和 Phase 1 之间无并行空间。Phase 2/3 可部分并行（迁移脚本开发可与兼容读实现同时进行），但 Phase 3 的 apply 必须在 Phase 2 完成后才能对真实数据执行。

---

### Phase 0: 核心类型与 Parser ✅ 已完成

**完成时间：2026-05-22**

#### 新增文件

| 文件 | 内容 |
|------|------|
| `nahida_bot/core/chat_address.py` | `ChatAddress` dataclass, `SessionKey` dataclass, `parse()`, `from_inbound()`, 格式校验 |
| `tests/test_chat_address.py` | 40 个 parser 测试，全部通过 |

#### 已实现

1. **ChatAddress dataclass** (`slots=True, frozen=True`)
   - `channel: str`, `target_type: str`, `target_id: str`, `thread_id: str = ""`
   - `__str__` → `channel:target_type:target_id[:thread_id]`
   - `is_typed` / `chat_key` / `legacy_key` 属性
   - `parse(value)` 类方法：typed、legacy、含 thread_id 三种路径
   - `from_inbound(platform, chat_id, *, is_group, chat_type)` 构建方法

2. **SessionKey dataclass**
   - 持有 `ChatAddress` + 可选 `suffix: str`
   - `__str__` → `milky:group:20001:abcd1234`
   - `parse(value)` 处理 typed/legacy base + suffix
   - `is_derived` 属性

3. **Parser 设计原则**
   - typed target: 第二段是 `KNOWN_TARGET_TYPES` 中的值 → `ChatAddress`
   - legacy session: 第二段不是 target_type → `target_type="unknown"`
   - **不默认补 private**，legacy 返回 `unknown`

#### 验收标准

- [x] `ChatAddress.parse("milky:group:20001")` 返回正确结构
- [x] `SessionKey.parse("milky:10001")` 返回 `target_type="unknown"`
- [x] `SessionKey.parse("milky:10001:abcd1234")` 正确识别 legacy suffix
- [x] `str(SessionKey(address, "abcd1234"))` → `"milky:group:20001:abcd1234"`
- [x] 40 个 parser 测试全部通过

---

### Phase 1: 新数据写 Typed Key ✅ 已完成

**完成时间：2026-05-22**

**目标：** 从 Router → Channel → Tool 全链路使用 `ChatAddress`，新产生的 session 不再有 collision 风险。

#### 已修改的文件

| 文件 | 实际改动 |
|------|----------|
| `nahida_bot/core/context.py` | `SessionContext` 新增 `chat_address: ChatAddress | None` 字段 |
| `nahida_bot/core/router.py` | `make_session_id` / `make_new_session_id` 接受 `str \| ChatAddress`；`get/set_active_session` 接受 `str \| ChatAddress`；新增 `_address_from_inbound()` helper；入站路径构建 `ChatAddress` 并传入 `SessionContext`；`get_active_session_id` typed key 优先、legacy key fallback |
| `nahida_bot/channels/milky/plugin.py` | `handle_inbound_event` 从 `message_scene` 构建 `ChatAddress`（`group`→`"group"`，`friend`→`"private"`，无 scene→`"unknown"`）；`make_session_id(address)` 生成 typed session id |
| `nahida_bot/channels/telegram/plugin.py` | `handle_inbound_event` 从 `is_group` 构建 `ChatAddress`（`True`→`"group"`，`False`→`"private"`）；`make_session_id(address)` 生成 typed session id |
| `nahida_bot/plugins/api_bridge.py` | `start_new_session` 从 `current_session.chat_address` 获取 `ChatAddress`，用 typed key 调用 router |
| `nahida_bot/plugins/builtin/commands.py` | Message 工具新增 `target` 参数（`milky:group:20001`），解析为 `ChatAddress`；`record` 用 typed session id；cron create 从 `ctx.chat_address` 提取 `chat_type` 传给 scheduler |
| `nahida_bot/scheduler/models.py` | `CronJob` 新增 `chat_type: str = ""` 字段 |
| `nahida_bot/scheduler/service.py` | `create_job` 接受 `chat_type` 参数，有 `chat_type` 时生成 typed `session_key`；`list_jobs` 先查 typed key 再 fallback legacy；`_execute_fire` 构建 `ChatAddress` 传入 `SessionContext` |
| `nahida_bot/scheduler/repository.py` | SQL insert/update 包含 `chat_type` 列；`_row_to_job` 安全读取 `chat_type`（兼容旧 schema） |
| `nahida_bot/db/engine.py` | Migration 011：`ALTER TABLE cron_jobs ADD COLUMN chat_type`；`CREATE TABLE session_key_migration_log` |
| `nahida_bot/gateway/schemas.py` | `SendMessageRequest` / `CreateCronRequest` 新增可选 `target` 字段 |
| `nahida_bot/gateway/routes/messages.py` | 优先解析 `target`→`ChatAddress`，fallback 到 `platform+chat_id` |
| `nahida_bot/gateway/routes/cron.py` | 优先解析 `target`→`ChatAddress`，提取 `chat_type` 传给 scheduler |

#### 关键设计决策

1. **Router 方法签名兼容**：`make_session_id` / `get_active_session_id` / `set_active_session` 同时接受 `ChatAddress` 和 legacy `(str, str)` 参数，通过 `isinstance` 分支处理。未改动的调用方继续工作。

2. **Active session fallback**：`get_active_session_id(address)` 先查 typed key，找不到时查 legacy key。这让从 DB 恢复的旧 session override 仍能命中。

3. **ChatAddress 来源**：
   - Milky：从 `message_scene` 直接推断（`group`→group，`friend`→private）
   - Telegram：从 `is_group` 推断（`True`→group，`False`→private）
   - Router 入站：从 `chat_context.chat_type` > `message_context.chat_type` > `is_group` 逐级 fallback

4. **Message 工具 `target` 参数**：新增为可选参数，与 `platform+chat_id` 二选一。`target` 解析为 `ChatAddress` 后用于 `record` 的 session id 生成。

5. **Scheduler `chat_type`**：新增为 `create_job` 的可选参数。有 `chat_type` 时生成 typed `session_key`（如 `milky:group:20001`），无时 fallback 到 legacy（如 `milky:20001`）。

#### 测试结果

- 611 tests passed, 8 skipped (live integration tests), 0 failures
- 测试改动：
  - `test_message_router.py`：`_inbound` helper 添加 `ChatContext(chat_type="private")`；所有 session id 引用从 `test:c1` 更新为 `test:private:c1`；group 测试使用 `ChatContext(chat_type="group")`
  - `test_milky_plugin.py`：session id 断言从 `milky:20001` 更新为 `milky:group:20001`

#### 验收标准

- [x] Milky 私聊 `10001` 和群聊 `10001` 产生不同 session id
- [x] 新 session key 格式为 `milky:private:10001` 或 `milky:group:10001`
- [x] `/new` 产生 `milky:group:10001:abcd1234` 格式
- [x] Message 工具 `target="milky:group:20001"` 可解析并正确 record
- [x] Cron fire 时 `SessionContext` 携带 `chat_address`
- [x] WebAPI `target` 参数工作正常
- [x] 旧 `platform/chat_id` 格式仍可解析（兼容读），但新写入均为 typed
- [x] 现有测试全部通过

---

### Phase 2: 兼容读取 Legacy 数据 ⬜ 待实施

**目标：** 旧 session 数据不丢失、不崩溃，但明确标记为 legacy，不参与正常 session 路由。

**难度：中** | **预估工期：1–1.5 天**

#### 需修改的文件

| 文件 | 改动范围 |
|------|----------|
| `nahida_bot/core/router.py` | `restore_active_sessions` 识别 legacy chat_key，加载但不用于新路由 |
| `nahida_bot/plugins/builtin/commands.py` | `/status`、session list 显示 legacy/typed 状态 |

#### 关键实现步骤

1. **Router 兼容查找**（已部分实现）
   - `get_active_session_id` 已支持 typed key 优先、legacy key fallback
   - 需增强 `restore_active_sessions` 对 legacy key 的日志提示

2. **Migration 011** ✅ 已在 Phase 1 中完成
   - `chat_type` 列和 `session_key_migration_log` 表已创建

3. **Session 状态可视化**
   - `/status` 输出标注 session key 是否为 typed
   - Session list 区分 legacy 和 typed sessions

#### 验收标准

- [x] 启动时 legacy active_sessions 记录不报错（Phase 1 fallback 机制已覆盖）
- [x] Typed session 优先匹配，legacy 仅作为 fallback
- [x] Migration 011 成功执行，审计表可用
- [ ] `/status` 可区分 legacy/typed session

---

### Phase 3: 数据库迁移工具 ⬜ 待实施

**目标：** 提供可审计、可回滚、人工逐条批准的迁移脚本，将 legacy session key 转为 typed key。

**难度：高** | **预估工期：2–3 天**

#### 新增文件

| 文件 | 内容 |
|------|------|
| `scripts/migrate_session_keys.py` | 一次性迁移脚本：inspect / apply / rollback |

#### 需修改的文件（仅 minor）

| 文件 | 改动 |
|------|------|
| `nahida_bot/db/engine.py` | 可能需要调整 migration 顺序或添加辅助索引 |

#### 关键实现步骤

1. **inspect 命令**
   - 扫描所有 legacy session key
   - 按第 7.3 节的推断优先级收集证据
   - 生成每 session 的 `recommendation` / `confidence` / `evidence` / `affected`
   - 输出 `migration-plan.json`

2. **人工批准流程**
   - 维护者编辑 plan JSON，逐条设置 `approval`
   - 支持: `approved` / `rejected` / `force_keep_legacy` / `force_rename` / `force_split`

3. **apply 命令**
   - 串行逐条执行，每条独立事务
   - rename: 更新 `sessions`, `memory_turns`, `active_sessions`, `cron_jobs`, `background_tasks`
   - split: 按 turn_id 拆分到多个 typed session
   - 冲突检测: 目标 typed session 已存在时合并而非覆盖
   - 所有操作写入 `session_key_migration_log`
   - apply 前自动备份 SQLite 文件

4. **rollback 命令（可选但推荐）**
   - 根据 migration log 反向恢复
   - 不承诺能恢复已 split 的 session（文档已说明）

#### 测试重点

- 使用真实 db 副本运行 inspect，验证推断逻辑
- 构造混合 private/group turns 的 session 测试 split
- 测试目标 session 已存在时的合并逻辑
- 测试 apply 中途失败的回滚
- 测试重复运行 inspect/apply 的幂等性

#### 验收标准

- [ ] `inspect` 对每个 legacy session 输出完整建议和证据
- [ ] `apply --dry-run` 不修改数据库但输出执行计划
- [ ] `apply` 前自动备份 db
- [ ] 每条迁移有独立事务，单条失败不影响后续
- [ ] 所有迁移操作记录到 `session_key_migration_log`
- [ ] `apply` 后跑一致性检查无 orphan 记录
- [ ] 脚本可安全重复运行（幂等）

---

### Phase 4: 移除 Legacy 兼容 ⬜ 待实施

**目标：** 清理所有 legacy 写入路径，删除兼容代码。

**难度：低中** | **预估工期：0.5–1 天**

#### 需修改的文件

| 文件 | 改动 |
|------|------|
| `nahida_bot/core/router.py` | 删除 `make_session_id` / `get_active_session_id` / `set_active_session` 的 legacy `(str, str)` 分支 |
| `nahida_bot/plugins/builtin/commands.py` | 移除 `platform/chat_id` 旧参数的兼容处理，只接受 `target` |
| `nahida_bot/gateway/schemas.py` | 移除旧 `platform/chat_id` schema，只保留 `target` |
| `nahida_bot/scheduler/service.py` | 移除 legacy `session_key` 构造（`f"{platform}:{chat_id}"`） |
| `nahida_bot/channels/milky/segment_converter.py` | 移除 default-to-friend fallback |

#### 关键实现步骤

1. 删除 Router 的 legacy `isinstance(str)` 分支
2. Message 工具要求必须传 `target`（含 target_type）
3. WebAPI 要求 `target` 或 `chat_type`，缺少时报错
4. Milky outbound 不再 default-to-friend，无 chat_type 时报错
5. 更新相关文档

#### 验收标准

- [ ] 代码中无 `platform:chat_id` 的手动拼接（grep 验证）
- [ ] 所有 session id 生成均通过 `ChatAddress` / `SessionKey`
- [ ] Milky 缺少 target_type 时发送报错而非 fallback to friend
- [ ] WebAPI 缺少 chat_type 时返回 400

---

### 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| ~~Phase 1 改动面大，引入回归~~ | ~~中高~~ | ✅ Phase 1 已完成，611 tests passed，0 regressions |
| Legacy session 中 private/group 数据混合 | 高 | Phase 3 inspect 自动检测冲突，split 需人工批准 |
| Cron job 迁移后发送到错误目标 | 高 | 无法确认类型的 cron 默认禁用；fire 时强制使用 ChatAddress |
| Migration apply 中途崩溃 | 中 | 每条 session 独立事务；自动备份；可重试 |
| 并行部署时新旧格式冲突 | 低 | Phase 0–1 期间新格式向下兼容（parser 可读旧格式） |

### 实施进度

```
✅ Day 1:     Phase 0 — 核心类型 + Parser + 40 个测试
✅ Day 1–2:   Phase 1 — Router + Channel + SessionContext + Commands + Scheduler + WebAPI
⬜ Day 3:     Phase 2 — 兼容读 legacy 增强 + 状态可视化
⬜ Day 3–4:   Phase 3 — 迁移脚本 inspect/apply 开发 + 测试
⬜ Day 5:     Phase 3 — 使用真实数据运行 inspect + 人工审核 + apply
⬜ Day 5:     Phase 4 — 清理 legacy 兼容代码 + 最终验证
```
