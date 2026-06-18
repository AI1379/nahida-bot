# 人员身份与账号映射系统设计

> 记录时间：2026-06-07
> 最近修订：2026-06-18（Phase 0+1 落地：模型/存储/resolver/router/whoami；Phase 2 落地：identity-aware 记忆读取 cascade）
> 状态：Phase 0-2 已实现并验证；Phase 3-5 待实现（identity-aware 记忆写入、管理命令、WebUI、迁移与自助链接）
> 相关文档：
>
> - [memory-scoping.md](memory-scoping.md) — 当前 `chat` / `global` 记忆隔离设计
> - [chat-address-and-session-id.md](chat-address-and-session-id.md) — ChatAddress / SessionKey 边界
> - [memory-system.md](memory-system.md) — 长期记忆系统总体设计
> - [../architecture/runtime-flows.md](../architecture/runtime-flows.md) — 消息上下文与运行流程

## 1. 背景

`memory-scoping.md` 的 V1 方案用 `chat` scope 堵住了不同聊天之间的长期记忆串线，但它没有解决一个更底层的问题：Bot 看到的是聊天入口和平台账号，不知道哪些账号背后是同一个真实聊天对象。

典型场景：

1. 同一个人同时使用 QQ 私聊、Telegram 私聊和 OneBot 群聊身份与 bot 互动。
2. 群聊中多个人共享同一个 `ChatAddress`，但每条消息的发送者不同。
3. 用户在私聊里告诉 bot 的偏好，换到另一个平台后应能被识别为同一个人的偏好。
4. 用户在群里说“我喜欢 Python”，这条记忆应属于发言者，而不是属于整个群。
5. 管理员需要显式声明“这个 QQ 号、这个 Telegram 账号、这个群成员身份是同一个人”，并能审计和撤销。

当前系统已有这些基础：

- `ChatAddress` 表示外部聊天地址，例如 `milky:private:10001`、`telegram:group:-100123`。
- `SessionKey` 表示一段对话历史，不等同于真实人。
- `InboundMessage.user_id` / `SenderContext.platform_user_id` 能表示一条消息的发送者平台账号。
- `MessageContext.sender_id` 已持久化到 `memory_turns.metadata_json.message_context`。
- `memory_items.scope_type` / `scope_id` 已是通用字符串，可以扩展新 scope 类型。

缺口是：没有一个统一的 `person` / `account` 身份层，把“聊天入口”“平台账号”“真实聊天对象”“长期记忆归属”解耦。

## 2. 目标与非目标

### 2.1 目标

- **统一人员身份**：用稳定的 `person_id` 表示 bot 本地认识的一个人。
- **账号映射**：把多个平台账号映射到同一个 `person_id`。
- **群聊发言者识别**：群聊消息的个人事实归属发言者，而不是整个群。
- **记忆归属确定性**：系统决定记忆 scope，LLM 只负责提取内容和受控 subject 类型。
- **会话解耦**：session/history 仍按 `SessionKey` 存储，长期个人记忆按 `person` 或 `account` scope 存储。
- **隐私默认安全**：私聊得到的个人记忆默认不在群聊公开注入。
- **可审计可撤销**：身份链接、解除、迁移都有明确记录和管理员入口。

### 2.2 非目标

- 不把不同账号的 `memory_turns` 聊天历史自动合并。跨平台最近历史检索可后续单独做。
- 不让 LLM **权威地**绑定账号身份（解锁记忆访问）；但允许 LLM 维护非权威的软身份假设用于个性化（见 §4.5）。
- 不用显示名做稳定身份 key。显示名只能作为 observation 或人工确认线索。
- 不要求所有账号必须链接。未链接账号仍应安全工作。
- 不在第一阶段实现复杂社交图谱或自动实体关系推断。

## 3. 核心概念

### 3.1 ChatAddress

`ChatAddress` 是可发送、可接收的聊天入口。

```text
milky:private:10001
milky:group:20001
telegram:private:12345
telegram:group:-10012345
```

它回答的是“消息来自或要发到哪里”，不是“谁是这个人”。

### 3.2 SessionKey

`SessionKey` 是内部对话历史和运行状态 key。

```text
milky:group:20001
milky:group:20001:cron:daily
telegram:private:12345:abcd1234
```

它回答的是“这段上下文和运行状态属于哪条对话 lane”，不是“真实用户是谁”。

### 3.3 AccountKey

`AccountKey` 表示一个平台上可观察到的账号身份。

推荐格式：

```text
{channel}:user:{platform_user_id}
```

例子：

```text
milky:user:10001
telegram:user:12345
onebot:user:10001
```

约束：

- `channel` 必须是配置后的 channel 实例 id，而不是含糊的平台名。多 OneBot / 多 QQ 机器人账号部署时，应使用不同 channel id，例如 `onebot-main`、`onebot-alt`。
- `platform_user_id` 来自 `SenderContext.platform_user_id`，缺失时退回 `InboundMessage.user_id`。
- `AccountKey` 不包含群号。群内显示名、角色和成员状态进入 participant observation，而不是账号 key。

### 3.4 Person

`Person` 是 bot 本地维护的真实聊天对象。

```text
person_id: owner
display_name: Arendellian
accounts:
  - milky:user:10001
  - telegram:user:12345
```

`person_id` 是本地稳定 ID，可由管理员命名或系统生成：

```text
person_owner
person_01HZX...
```

一个 `Person` 可以关联多个 `AccountKey`。一个 `AccountKey` 同一时刻只能归属一个 active `Person`。

### 3.5 Participant Observation

`ParticipantObservation` 记录“某个账号在某个聊天里如何出现”。

例子：

```text
chat_address: milky:group:20001
account_key: milky:user:10001
display_name: 群名片(昵称)
role_tags: admin
first_seen_at: ...
last_seen_at: ...
```

它用于审计、显示和辅助人工链接，不作为自动身份判定依据。

## 4. 身份解析

每条入站消息都应解析出一个 `IdentityResolution`：

```python
@dataclass(frozen=True)
class IdentityResolution:
    chat_address: ChatAddress
    session_id: str
    sender_account_key: str
    person_id: str | None
    confidence: Literal["linked", "unlinked", "unknown"]
    source: Literal["manual_link", "self_verified", "config_seed", "none"]
```

解析流程：

```text
InboundMessage
  -> ChatAddress.from_inbound(...)
  -> sender_account_key = AccountKey.from_message(...)
  -> identity_store.resolve_account(sender_account_key)
  -> IdentityResolution
```

规则：

1. 私聊和群聊都使用同一套 sender account 解析。
2. 群聊的 `chat_address` 是群，`sender_account_key` 是发言者账号。
3. 未链接账号不会被归并到任何 person，但仍可用 `account` scope 隔离个人记忆。
4. 显示名变化只更新 observation，不改变链接。
5. LLM 不参与**权威**身份解析。权威的 account→person 绑定（解锁 person-scope 记忆）必须经过验证闸（OTP / 本人确认 / 管理员），LLM 无法单独完成；但 LLM 可以维护非权威的**软身份假设**并触发验证流程，见 §4.5。

### 4.5 软身份与硬身份

权威身份解析（§4）是记忆访问的信任根，必须经过验证闸。但完全不让 LLM 参与身份判断
会让 bot 显得“失忆”——它其实掌握最多的连续性线索（对话风格、自述、行为模式）。
解法是把身份拆成两层：

| | 软身份（soft） | 硬身份（hard / 权威） |
|---|---|---|
| 来源 | bot 自建的概率性假设 | 经过验证的 account→person 绑定 |
| 解锁 | 无——只驱动个性化、语气、低风险连续性 | person-scope 记忆 + visibility capability |
| 由谁建立 | LLM 自由维护 | 必须过验证闸（OTP / 本人确认 / 管理员），LLM 无法单独完成 |
| 误判后果 | 轻微——回复调错味 | 灾难性——记忆越权泄漏 |

关键不变量：**软身份永远不会静默升级为硬身份。** 升级唯一通道是 §9.3 的验证闸。
软身份被墙在 sensitive memory 之外，所以即使假设错误（把 B 误当 A），也不会泄漏 A
的隐私——它只影响语气和泛连续性，不解锁任何 private/sensitive 记忆。两个设计决定
（软/硬拆 + §9.3 静默到显式）互相加固。

bot 在身份解析中的角色是**提议者 + 验证触发者，不是绑定者**：

1. **观察并假设**：bot 自由维护“账号 B 可能是 person A”的低置信假设，落审计日志
   `(account_key, hypothesized_person_id, confidence, evidence, timestamp)`。
2. **静默直到显式触发**：bot 不主动弹验证、不主动告知用户“我觉得你是 X”——那既烦人
   又反向暴露了跨账号关联（一种 meta 隐私泄漏）。软身份假设全程静默工作。
3. **触发验证流，不完成验证**：只有当 subject 本人显式发起 link 指令（见 §9.3）时，
   才进入 OTP 自证流程。bot 发起/辅助验证，验证由系统 + 本人完成。
4. **验证通过 → 落硬链接**（`verification` 记录来源，可审计可撤销）→ 才解锁 person-scope。

触发后，积攒的假设可选地作为**候选浮出**（用户自己触发的、关于自己账号的，低风险）：
bot 说“我觉得你的 QQ X 可能也是你，要验证它吗？”。若追求最大隐私，也可隐藏假设、
走标准 OTP。

## 5. 记忆 Scope 模型

身份系统引入新的长期记忆 scope：

| scope_type | scope_id | 用途 |
|------------|----------|------|
| `global` | `__global__` | 全局共享知识、系统决策、通用流程 |
| `chat` | `{chat_key}` | 群/频道/私聊入口本身的约定、上下文、共享任务 |
| `person` | `{person_id}` | 同一个真实人的偏好、个人事实、长期任务 |
| `account` | `{account_key}` | 未链接账号的个人记忆，或平台账号特有事实 |

### 5.1 默认写入规则

| 内容类型 | 已解析 person | 未解析账号 | 说明 |
|----------|---------------|------------|------|
| 发送者自述偏好 | `person` | `account` | “我喜欢 Python”属于发言者 |
| 发送者个人事实 | `person` | `account` | “我住在上海”属于发言者 |
| 发送者个人待办 | `person` | `account` | “提醒我明天...”属于发言者 |
| 平台账号事实 | `account` | `account` | “我的 Telegram 用户名是...” |
| 群/频道规则 | `chat` | `chat` | “这个群里默认用中文” |
| 当前私聊局部约定 | `chat` 或 `person` | `chat` 或 `account` | 取决于是聊天入口约定还是人的偏好 |
| 项目/系统决策 | `global` | `global` | 与具体人无关 |

关键变化：`preference` / `fact` / `task` 不再无条件写入 `chat` scope。对群聊来说，个人记忆必须优先写入 `person` 或 `account`，否则会把某个群成员的个人信息变成整个群共享记忆。

### 5.2 读取 Cascade

读取由 `MemoryScopeResolver` 根据当前 turn、目标 chat 和隐私策略生成 scope 序列。

私聊默认：

```text
1. person:{person_id}      # 如果已链接
2. account:{account_key}   # 平台账号特有事实或未链接 fallback
3. chat:{private_chat_key}
4. global:__global__
```

群聊默认：

```text
1. chat:{group_chat_key}
2. person:{sender_person_id} items visible_in_current_chat only
3. account:{sender_account_key} items visible_in_current_chat only
4. global:__global__
```

群聊默认不注入私聊来源的 private person memory。这样 bot 可以知道当前发言者是谁，但不会把私聊个人事实带到公开群里。

可配置增强：

```yaml
memory:
  identity:
    group_person_memory: visible_only  # off | visible_only | allow_private
```

推荐默认值是 `visible_only` 或 `off`，不要默认 `allow_private`。

> **意图触发的 capability 扩张**：以上 cascade 和 `group_person_memory` 开关都是静态的
> （部署级配置或 slash 命令）。自然语言触发的跨 scope 召回——例如用户在群聊里说“还记得
> 我们私下聊的吗”——需要额外的**触发判断层**：检测到意图后，把本轮可读集扩张到请求者
> 本人 person/account scope 的非 sensitive 项，召回后的公开仍由 §10.2 把关、sensitive 项
> 仍受 §5.3 硬隔离。该触发层与 KB 触发不对称问题统一处理，见
> [knowledge-base.md §3.1](knowledge-base.md)；本文档不重复其触发判别设计。

### 5.3 可见性

每条 personal memory 需要在 `metadata_json` 或后续列中记录可见性：

```json
{
  "visibility": "private",
  "origin_chat": "telegram:private:12345",
  "allowed_chats": []
}
```

建议取值：

| visibility | 含义 |
|------------|------|
| `private` | 只在该 person 的私聊或该 person 触发的私有任务中使用 |
| `origin_chat` | 只在来源 chat 中使用 |
| `allowed_chats` | 只在白名单 chat 中使用 |
| `public` | 可在任意 chat 中使用 |

个人记忆默认 `private`。群聊中明确由用户说出的“可以在这个群记住”才写成 `allowed_chats` 或 `origin_chat`。

**subject ≠ visibility。** subject / scope 回答“这条记忆是关于谁的”（跨场合召回的
连接组织），visibility 回答“它能在哪里被说出来”（访问控制）。两者必须分开：subject
越松越好用（驱动跨群召回 `memory_search(subject=person_id)`），visibility 该紧。把两者
捆在一个 scope 字段里，就是“要么全记串、要么全切断”——这正是 §10 默认策略显得太硬
的根因。

**揭示的访问规则**：把某条 person-subject memory 说给当前 channel C，当且仅当
`audience(C) ⊆ subject 信任的集合` 且 sensitivity 允许。最小情形是 C 与 subject 的
1:1（audience 就是本人）→ 总是允许；群聊默认只让 `shareable` 流动，或 subject 给过
`(person, channel)` grant 且内容非 `sensitive`。

**敏感度分级**（写记忆时由用户或做梦打标，与 visibility 正交）：

| sensitivity | 含义 |
|---|---|
| `shareable` | 偏好、闲聊连续性，可跨场合使用 |
| `private` | 默认 1:1，需 grant 才进群 |
| `sensitive` | 硬隔离，只在 subject 独处的 1:1 揭示，grant 也不能越权 |

软身份（§4.5）不授予任何 visibility capability——访问边界不被软身份跨越。

## 6. Consolidation 设计

### 6.1 Subject 不由 LLM 发明 ID

Extractor 可以输出受控 subject：

```json
{
  "kind": "preference",
  "subject": "current_sender",
  "content": "用户偏好使用 Python。"
}
```

允许的 subject：

| subject | 系统映射 |
|---------|----------|
| `current_sender` | `person` 或 `account` scope |
| `current_chat` | `chat` scope |
| `global` | `global` scope |
| `mentioned_account:{account_key}` | 仅当系统提供 allowlist 且策略允许 |

禁止 extractor 输出任意 `person_id`、`account_key` 或 scope string。系统只接受当前 turn 可验证的 subject。

### 6.2 写入流程

```text
current_session + InboundMessage
  -> IdentityResolution
  -> MemoryExtractor extracts content + controlled subject
  -> MemorySubjectResolver maps subject to scope
  -> VisibilityPolicy assigns visibility
  -> MemoryStore.append_item(scope_type, scope_id, metadata)
```

### 6.3 Duplicate 检测

重复检测必须在目标 scope 内执行：

- `person:owner` 内检查 owner 的偏好是否已有重复。
- `account:telegram:user:12345` 内检查平台账号事实是否已有重复。
- `chat:milky:group:20001` 内检查群约定是否已有重复。

不同 person/account 之间不能互相判重，否则会误删别人的记忆。

## 7. 数据模型

### 7.1 新增表

```sql
CREATE TABLE IF NOT EXISTS persons (
    person_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_accounts (
    account_key TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'user',
    platform_account_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    verification TEXT NOT NULL DEFAULT 'manual',
    linked_by TEXT NOT NULL DEFAULT '',
    linked_at TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
);

CREATE INDEX IF NOT EXISTS idx_person_accounts_person
    ON person_accounts(person_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_person_accounts_unique_active
    ON person_accounts(channel, account_type, platform_account_id)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS account_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    chat_address TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role_tags_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_message_id TEXT NOT NULL DEFAULT '',
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_account_observations_account
    ON account_observations(account_key, last_seen_at);

CREATE INDEX IF NOT EXISTS idx_account_observations_chat
    ON account_observations(chat_address, last_seen_at);

CREATE TABLE IF NOT EXISTS identity_link_requests (
    request_id TEXT PRIMARY KEY,
    person_id TEXT,
    source_account_key TEXT NOT NULL,
    target_account_key TEXT,
    code_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    metadata_json TEXT
);
```

### 7.2 Memory metadata

新增或规范化这些 metadata：

```json
{
  "subject_type": "person",
  "subject_id": "owner",
  "origin_account_key": "telegram:user:12345",
  "origin_chat_address": "telegram:private:12345",
  "origin_session_id": "telegram:private:12345",
  "visibility": "private",
  "allowed_chats": []
}
```

`scope_type` / `scope_id` 仍是查询过滤的主字段，metadata 用于审计、迁移和 UI 展示。

## 8. API 与代码边界

### 8.1 新模块

| 文件 | 责任 |
|------|------|
| `nahida_bot/identity/models.py` | `AccountKey`、`Person`、`IdentityResolution` 数据模型 |
| `nahida_bot/identity/store.py` | 身份存储协议 |
| `nahida_bot/identity/sqlite.py` | SQLite 实现 |
| `nahida_bot/identity/resolver.py` | 从 inbound/context 解析 person/account |
| `nahida_bot/identity/policy.py` | 记忆可见性和 group 注入策略 |
| `nahida_bot/agent/memory/identity_scope.py` | 根据 IdentityResolution 生成读写 scope |

### 8.2 SessionContext 扩展

建议扩展：

```python
@dataclass(slots=True, frozen=True)
class SessionContext:
    ...
    user_id: str = ""
    sender_display_name: str = ""
    sender_account_key: str = ""
    person_id: str | None = None
```

`user_id` 保持兼容，新的代码优先用 `sender_account_key` / `person_id`。

### 8.3 Router 接入点

`MessageRouter` 在设置 `current_session` 前执行身份解析：

```text
inbound -> ChatAddress -> IdentityResolution -> SessionContext
```

同时记录 `account_observations`：

```text
account_key + chat_address + display_name + role_tags + last_seen
```

### 8.4 Memory 接入点

| 路径 | 改动 |
|------|------|
| `SessionRunner._load_relevant_memory` | 使用 identity-aware cascade |
| `MemoryConsolidator.consolidate_turn` | 接收 `IdentityResolution` 或 `MemoryScopeContext` |
| `scheduler/service.py` | cron run 带创建者 account/person 和投递 chat policy |
| `plugins/api_bridge.py` | `memory_search/store` 可显式传 subject 或默认当前 sender |
| `gateway/routes` | WebUI 增加身份与账号管理页面 |

## 9. 管理与自助链接

### 9.1 管理员配置

第一阶段可用配置 seed：

```yaml
identity:
  people:
    - person_id: owner
      display_name: Arendellian
      accounts:
        - channel: milky
          account_type: user
          platform_account_id: "10001"
          label: QQ
        - channel: telegram
          account_type: user
          platform_account_id: "12345"
          label: Telegram
```

启动时将配置 upsert 到 DB。配置只新增或更新 `config_seed` 链接，不自动删除 DB 中通过命令创建的链接。

### 9.2 管理命令

建议命令：

```text
/identity whoami
/identity person create <person_id> [display_name]
/identity link <person_id> <account_key>
/identity unlink <account_key>
/identity accounts <person_id>
/identity account <account_key>
/identity observations [chat_address]
```

权限：

- `create/link/unlink` 默认仅 admin。
- `whoami` 可所有用户使用。
- `observations` 可能暴露群成员信息，默认 admin。

### 9.3 自助验证

后续可支持用户自己链接账号：

```text
账号 A: /identity link-start
Bot 返回一次性 code
账号 B: /identity link-confirm <code>
```

约束：

- **静默直到显式触发**：验证流默认休眠，只有 subject 本人显式发起 link 指令才启动。
  bot 不主动弹验证、不主动告知“我觉得你是 X”（避免烦人和反向暴露跨账号关联）。
- **触发者必须是 subject 本人且自证**：指令是“关联**我**的账号”，不是“验证 B 是不是
  A”（后者本身是个可被滥用的探针）。只有“我能证明同时控制这两个账号”才进入流程。
- **触发后，积攒的软身份假设（§4.5）可作为候选浮出**（“我觉得你的 QQ X 可能也是你，
  要验证它吗？”）；追求最大隐私时也可隐藏假设、走标准 OTP。
- 所有软身份假设全程落审计日志，即使对用户静默。
- code 有过期时间。
- 完成后记录 `verification="self_verified"`。
- 群聊里启动链接时必须确认链接的是当前发言者账号，不能链接被提及的人。

## 10. 群聊隐私策略

群聊是身份系统最容易出错的地方，默认策略必须保守。

### 10.1 默认注入规则

在群聊里：

1. 总是可以注入 `chat:{group}` 和 `global`。
2. 可以识别当前发送者的 `person_id`，但不默认注入 private person memory。
3. 只有 `visibility in {"origin_chat", "allowed_chats", "public"}` 且当前群符合条件的个人记忆才能注入。
4. 不因显示名相同而注入其他人的记忆。
5. 不因用户提到“Alice”就注入 Alice 的私有记忆，除非有明确 mention/account 解析且 policy 允许。

### 10.2 回复约束

即使某条个人记忆允许用于群聊，也应尽量避免无必要地复述敏感来源。例如可用于调整建议，但不要说“你上次在私聊告诉我...”，除非用户明确要求。

这可以通过 system prompt 加一条低成本约束：

```text
When using identity-linked personal memory in a group chat, do not reveal
private origins or sensitive details unless the user explicitly asks and
the memory visibility permits disclosure in this chat.
```

## 11. 迁移策略

### 11.1 从 chat-scoped personal memory 迁移

迁移脚本应支持 `inspect` / `apply`：

1. 扫描 `memory_items` 中 `scope_type="chat"` 的 `preference` / `fact` / `task`。
2. 从 `evidence_json` / `metadata_json` 读取 `origin_session_id`、`message_context.sender_id`、`origin_chat_address`。
3. 构造 `account_key`。
4. 如果 account 已链接 person，建议迁移到 `person:{person_id}`。
5. 如果 account 未链接，建议迁移到 `account:{account_key}`。
6. 如果无法确定 sender，保留 `chat` scope 并标记 review。
7. 群聊来源的个人记忆迁移后 visibility 默认 `origin_chat`，私聊来源默认 `private`。

### 11.2 从 global-scoped personal memory 迁移

沿用 `memory-scoping.md` Phase 4 的思路，但目标 scope 改为：

```text
person/account > chat > global
```

只有证据足够确定“这条记忆属于哪个发送者”时才迁移到 person/account。无法确定时保留 global 并进入人工 review。

### 11.3 链接变更后的记忆处理

当 `account` 链接到 `person`：

- 不要求立即重写所有 `account` scoped memory。
- 读取时 cascade 同时包含 `person` 和 `account`。
- 后台 compaction 可逐步把长期稳定的 `account` memory 合并到 `person` scope。

当账号从 person 解绑：

- 不自动删除历史 person memory。
- 解绑后该 account 不再读取旧 person scope。
- 管理员可选择把该 account 来源的记忆迁出或归档。

## 12. 实施路线

### Phase 0：模型与文档

- [x] 新增 `AccountKey`、`IdentityResolution` 模型。（`nahida_bot/identity/models.py`，含 `Person` / `AccountLink` / `ParticipantObservation`。）
- [x] 明确 account key 格式和 channel instance id 约束。（`{channel}:user:{platform_user_id}`；channel 取自 typed ChatAddress，**不**回退到 platform 名，避免多实例 namespace 碰撞。）
- [x] 更新 memory scope 文档，声明 personal memory 不应默认写入 group chat scope。（原则见本文 §5.1 / §10；memory 读写尚未切到 person/account scope，Phase 2/3 落地。）

### Phase 1：配置驱动身份映射

> 状态：**Phase 0+1 已实现并验证（2026-06-18）**；`uv run pytest` / `ruff` / `pyright` 全绿。整层 gated 在 `identity.enabled`（默认 `False`），关闭时 resolver no-op、`SessionContext` 身份字段为空，现有行为零变化。

- [x] 新增 identity 配置 seed。（`IdentityConfig` / `IdentityPersonSeed` / `IdentityAccountSeed`，启动时 upsert，`verification=config_seed`，只增改不删除。）
- [x] 新增 `persons`、`person_accounts`、`account_observations` migration。（migration 016；`identity_link_requests` 留到 Phase 5。）
- [x] Router 解析 current sender account 并写入 `SessionContext`。（`IdentityResolver` 在 `_build_session_context` 解析，三个 handler 共用；同时记录 `account_observations`。）
- [x] `whoami` 命令显示当前 account/person。（`/identity whoami`，读 `current_session`。）

### Phase 2：identity-aware memory read

> 状态：**已实现并验证（2026-06-18）**；`uv run pytest` / `ruff` / `pyright` 全绿（仅 live/network 集成测试因沙箱无网络被跳过）。读取 cascade gated 在身份解析结果上：identity 关闭或 sender 未链接时 cascade 退化为 V1 的 `chat → global`（legacy 仅 `global`），默认行为零变化。群聊默认仍只加载 `chat + global`，sender 的 private person memory 不注入；`group_person_memory="allow_private"` 才会加入 sender person/account scope（per-item visibility 过滤随 Phase 3 写入 visibility tag 后生效）。

- [x] `SessionRunner._load_relevant_memory` 使用 identity-aware cascade。（`nahida_bot/identity/policy.py` 生成 scope 序列，`MemoryStoreRetrievalAdapter` 泛化为 N 级 cascade；`RetrievalRequest.scopes` 驱动。）
- [x] 私聊加载 person/account/chat/global。（policy：`person → account → chat → global`，按优先级填满 budget 并按 `result_id` 去重。）
- [x] 群聊只加载 chat/global 和允许在当前群可见的 sender memory。（默认 `chat → global`；`allow_private` opt-in 加入 sender scope；per-item `visible_only` 过滤待 Phase 3。）
- [x] `api_bridge.memory_search` 支持相同策略。（同一 policy + budget-fill cascade。）

### Phase 3：identity-aware memory write

- [ ] Consolidator 接收 `IdentityResolution`。
- [ ] Extractor subject 限定为 `current_sender` / `current_chat` / `global`。
- [ ] personal memory 写入 person/account scope。
- [ ] duplicate 检测按目标 scope 过滤。

### Phase 4：管理命令与 WebUI

- [ ] 管理员命令：create/link/unlink/accounts/observations。
- [ ] WebUI 身份页面：person 列表、账号、观察记录、记忆 scope。
- [ ] 所有链接操作写审计日志。

### Phase 5：迁移与自助链接

- [ ] 迁移脚本 inspect/apply。
- [ ] 支持 OTP/self-verified link。
- [ ] 后台 compaction 合并 account scope 到 person scope。

## 13. 测试计划

核心测试：

- 两个未链接账号互相看不到 personal memory。
- 两个账号链接到同一 person 后，在私聊中能召回同一 person memory。
- 群聊中 Alice 的 personal memory 不会注入 Bob 的 turn。
- 群聊中 Alice 的 private memory 默认不会公开注入。
- 群聊 shared/allowed personal memory 只在允许 chat 注入。
- 群聊规则写入 chat scope，对所有群成员可见。
- 显示名相同的两个账号不会自动链接。
- account unlink 后，该账号不再读取 person scope。
- cron job 使用创建者 identity 和投递目标 chat policy。
- 迁移脚本 dry-run 不修改数据库，apply 前备份。

## 14. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 错误链接账号 | 个人记忆泄露给错误的人 | 默认手动/验证链接；审计日志；支持 unlink 和迁移回滚 |
| 群聊公开私聊记忆 | 隐私泄露 | 群聊默认不注入 private person memory；visibility policy 强制过滤 |
| 显示名碰撞 | 错误归并 | 显示名只做 observation，不做自动链接依据 |
| 多 bot 账号 namespace 碰撞 | account key 错误 | channel id 必须表示配置实例；多账号部署使用不同 channel id |
| LLM 抽取 subject 错误 | 记忆写错 scope | subject allowlist；系统映射 scope；高风险候选进入 review |
| 解绑后旧记忆残留 | 用户误以为完全隔离 | 解绑只改变读取；提供迁出/归档工具并在 UI 明示 |

## 15. 结论

`chat` scope 只能回答“这段聊天入口是什么”，不能回答“这个发言者是谁”。完整方案应引入 `Person` / `AccountKey` / `ParticipantObservation` 三层：

```text
ChatAddress  -> 消息在哪里
SessionKey   -> 对话历史是哪条 lane
AccountKey   -> 这条消息由哪个平台账号发出
Person       -> 哪些账号属于同一个真实聊天对象
```

长期个人记忆归属 `person` 或 `account`，群/频道约定归属 `chat`，系统知识归属 `global`。这样既能识别跨平台同一个人，也能避免把群聊里某个成员的个人事实错误地提升为整个群的共享记忆。
