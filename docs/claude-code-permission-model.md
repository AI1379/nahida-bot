# Claude Code 权限模型分析报告

## 一、概述

Claude Code 的权限系统是一个**多层防御 (Defense in Depth)** 架构，由以下核心层组成：

1. **权限模式 (Permission Modes)** — 用户可切换的宏观授权级别
2. **规则系统 (Permission Rules)** — 基于工具名和内容的细粒度 allow/deny/ask 规则
3. **权限检查管道 (Permission Pipeline)** — 有序的决策流程
4. **沙箱隔离 (Sandbox)** — OS 级别的文件系统和网络隔离
5. **Hooks 系统** — 可编程的审批/拒绝钩子
6. **企业管理 (Enterprise Policy)** — 组织级别的策略覆盖
7. **AI 分类器 (Auto Mode)** — 基于对话上下文的智能审批

---

## 二、权限模式 (Permission Modes)

定义于 `src/utils/permissions/PermissionMode.ts` 和 `src/types/permissions.ts`

| 模式 | 说明 |
|------|------|
| `default` | 默认模式 — 每个非白名单工具都需要用户确认 |
| `plan` | 规划模式 — 只读，工具调用正常提示 |
| `acceptEdits` | 自动接受工作目录内的文件编辑 |
| `bypassPermissions` | YOLO 模式 — 跳过所有权限提示（可被管理员禁用） |
| `dontAsk` | 静默拒绝所有权限请求，不弹提示 |
| `auto` | AI 分类器自动审批/拒绝（需 GrowthBook 功能开关启用） |

用户通过 **Shift+Tab** 在这些模式之间循环切换。

### 模式优先级解析

```
1. --dangerously-skip-permissions (最高优先)
2. --permission-mode <mode> (CLI 参数)
3. settings.permissions.defaultMode (配置文件)
4. default (兜底)
```

---

## 三、规则系统 (Permission Rules)

### 规则格式

规则字符串格式为 `"ToolName(content)"` 或 `"ToolName"`：

```
Bash              — 允许/拒绝所有 bash 命令
Bash(npm install) — 允许/拒绝特定命令模式
Bash(npm:*)       — 前缀匹配语法（npm 或 npm <任何参数>）
Edit(src/**)      — 文件 glob 模式匹配
Read(*.ts)        — 文件扩展名匹配
mcp__server       — MCP 服务器级别的规则
mcp__server__tool — MCP 特定工具的规则
```

### 规则来源（优先级从低到高）

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | `userSettings` | `~/.claude/settings.json` |
| 2 | `projectSettings` | 项目根目录 `.claude/settings.json` |
| 3 | `localSettings` | `.claude/settings.local.json` |
| 4 | `flagSettings` | CLI 标志 |
| 5 | `policySettings` | 企业托管策略（最高文件优先级） |
| 6 | `cliArg` | `--allowed-tools` / `--disallowed-tools`（内存中） |
| 7 | `command` | 运行时命令注入 |
| 8 | `session` | 会话内的用户交互规则（最高优先级） |

### 三种规则行为

- **`allow`** — 自动批准工具使用
- **`deny`** — 自动拒绝工具使用
- **`ask`** — 始终弹出提示（即使 bypassPermissions 模式也不跳过）

---

## 四、权限检查管道 (Permission Pipeline)

核心函数 `hasPermissionsToUseTool` 定义于 `src/utils/permissions/permissions.ts`，按以下顺序执行：

```
Step 1a → 全局 deny 检查       匹配到整个工具名的 deny 规则 → 立即拒绝
Step 1b → 全局 ask 检查        匹配到整个工具名的 ask 规则 → 弹出提示
Step 1c → 工具内部权限检查      tool.checkPermissions() 工具特有逻辑
Step 1d → 工具拒绝             工具内部返回 deny → 立即拒绝
Step 1e → 用户交互必需         requiresUserInteraction() 为 true → 强制 ask
Step 1f → 内容级 ask 规则      工具返回的内容特定 ask → 强制 ask（bypass-immune）
Step 1g → 安全检查             .git/、.claude/、.vscode/ 等路径 → 强制 ask（bypass-immune）
─────────────────────────────────────────────────────────────────
Step 2a → bypassPermissions    如果模式为 bypass → 全部放行（跳过 step 1 安全检查除外）
Step 2b → alwaysAllow 规则     匹配到 allow 规则 → 自动放行
─────────────────────────────────────────────────────────────────
Step 3  → 默认行为             无规则匹配时 → 转为 ask（弹出提示）
```

### 后处理

- **dontAsk 模式**：将 `ask` 静默转为 `deny`
- **auto 模式**：将 `ask` 转给 AI 分类器处理
- **headless agent**：运行 hooks 后自动拒绝

### 关键特性：Bypass-Immune 安全检查

以下安全检查**无法被任何模式或 hook 覆盖**：

- `.git/`、`.claude/`、`.vscode/` 目录的写操作
- `.gitconfig`、`.bashrc`、`.zshrc`、`.env`、SSH 配置等危险文件
- Windows 路径安全检查（NTFS ADS、8.3 短名等）
- 跨机器 bridge 消息

---

## 五、沙箱系统 (Sandbox)

定义于 `src/utils/sandbox/sandbox-adapter.ts`

### 核心配置

```typescript
sandbox: {
  enabled: boolean
  autoAllowBashIfSandboxed: boolean    // 沙箱内命令自动放行（默认 true）
  filesystem: {
    allowWrite: string[]               // 允许写入的路径
    denyWrite: string[]                // 禁止写入的路径
    denyRead: string[]                 // 禁止读取的路径
  }
  network: {
    allowedDomains: string[]
    deniedDomains: string[]
  }
}
```

### 安全强化

沙箱始终：

- **禁止写入所有 `settings.json` 文件**（防止沙箱逃逸）
- **禁止写入 `.claude/skills/`**（与命令/代理同权限）
- **清理裸 git 仓库文件**（防止 git 配置利用）
- 默认允许写入当前目录和 Claude 临时目录

### 平台支持

- macOS（seatbelt sandbox）
- Linux（bubblewrap/bwrap）
- WSL2+（不支持 WSL1）

---

## 六、Hooks 系统

定义于 `src/types/hooks.ts` 和 `src/schemas/hooks.ts`

### Hook 事件类型

| 事件 | 触发时机 |
|------|----------|
| `PreToolUse` | 工具执行前（可阻止/允许/修改） |
| `PostToolUse` | 工具执行后 |
| `PermissionRequest` | 权限提示期间（可编程审批/拒绝） |
| `Notification` | 通知发送时 |
| `Stop` | 代理停止时 |
| `SubagentStop` | 子代理停止时 |

### Hook 类型

- **command** — Shell 命令执行
- **prompt** — LLM prompt 评估
- **agent** — 代理验证器
- **http** — HTTP 端点调用
- **callback** — JavaScript 回调函数

### Hook 结果优先级

```
deny > ask > allow
```

任何 hook 返回 `deny` 会立即阻止工具执行。

### 工作区信任

所有 hooks 要求用户先接受**工作区信任**才能在交互模式下执行。

---

## 七、Auto 模式 / AI 分类器

定义于 `src/utils/permissions/permissions.ts` 第 519-927 行

当 auto 模式激活时，对 `ask` 结果的处理流程：

```
1. acceptEdits 快速路径 → 如果在 acceptEdits 模式下也会放行，直接放行
2. 安全工具白名单 → 某些工具始终安全，自动放行
3. AI 分类器 → 将完整对话 + 待执行操作发送给 AI 模型，返回 allow/deny
```

### 拒绝追踪（熔断机制）

- **连续 3 次拒绝** → 回退到用户提示
- **总计 20 次拒绝** → 回退到用户提示
- headless 模式下触发限制 → 抛出 `AbortError`

### 危险规则剥离

进入 auto 模式时，以下规则被临时移除（防止绕过分类器）：

- 工具级 allow 规则（如 `Bash` 无内容限制）
- 解释器前缀规则（如 `Bash(python:*)`）
- 通配符规则
- 所有 Agent allow 规则

---

## 八、文件系统权限检查

定义于 `src/utils/permissions/filesystem.ts`

### 读权限管道（8 步）

```
1. 阻止 UNC 路径 (\\server\share)
2. 检测 Windows 可疑路径模式
3. 检查 deny 规则
4. 检查 ask 规则
5. Edit 隐含 Read（Edit 允许时 Read 也允许）
6. 检查工作目录范围
7. 检查内部路径（Claude temp、MCP state）
8. 检查 allow 规则
```

### 危险文件清单

始终触发安全提示的文件：`.gitconfig`、`.bashrc`、`.zshrc`、`.profile`、`.env`、`.mcp.json`、`.claude.json`、SSH 配置、GPG 配置、AWS 凭证等。

### Windows 路径安全

检测：NTFS 备用数据流（`file.txt:Zone.Identifier`）、8.3 短名、长路径前缀（`\\?\`）、尾部点号、DOS 设备名。

---

## 九、企业管理功能

### 策略锁定

| 配置项 | 效果 |
|--------|------|
| `allowManagedPermissionRulesOnly` | 仅策略规则生效，忽略其他来源 |
| `allowManagedHooksOnly` | 仅策略 hooks 执行 |
| `sandbox.network.allowManagedDomainsOnly` | 仅策略网络域名有效 |
| `sandbox.filesystem.allowManagedReadPathsOnly` | 仅策略读取路径有效 |

### Bypass 模式紧急停止

- GrowthBook 门控 `tengu_disable_bypass_permissions_mode` — 组织级禁用
- 设置 `permissions.disableBypassPermissionsMode: 'disable'` — 本地禁用
- 活跃会话中触发 → 调用 `gracefulShutdown(1, 'bypass_permissions_disabled')`

### Auto 模式门控

- `tengu_auto_mode_config.enabled`: `enabled` | `disabled` | `opt-in`
- 用户通过 `--enable-auto-mode` 或 `skipAutoPermissionPrompt` 设置选择加入
- 需要模型支持检查

---

## 十、工具接口权限扩展点

定义于 `src/Tool.ts`

每个工具可实现以下权限相关方法：

```typescript
checkPermissions(input, context): Promise<PermissionResult>  // 工具特定权限逻辑
requiresUserInteraction?(): boolean                           // bypass-immune 标记
preparePermissionMatcher?(input): Promise<Matcher>            // hook 匹配器
toAutoClassifierInput(input): unknown                        // 分类器输入压缩
```

工具特定的权限 UI 组件（如 `BashPermissionRequest`、`FileEditPermissionRequest` 等）在 `src/components/permissions/PermissionRequest.tsx` 中注册。

---

## 十一、架构总结

```
┌─────────────────────────────────────────────────┐
│               用户交互 / CLI / Bridge            │
├─────────────────────────────────────────────────┤
│         Permission Mode (Shift+Tab 切换)         │
│  default | plan | acceptEdits | bypass | auto    │
├─────────────────────────────────────────────────┤
│            Permission Rules (5+3 层来源)          │
│     allow / deny / ask × ToolName(content)       │
├─────────────────────────────────────────────────┤
│          Permission Pipeline (13 步决策)          │
│  deny → ask → tool-specific → safety → allow     │
├─────────────────────────────────────────────────┤
│              Hooks (PreToolUse / PermissionReq)   │
│          可编程审批/拒绝/修改工具输入              │
├─────────────────────────────────────────────────┤
│             Sandbox (OS 级隔离)                   │
│    filesystem isolation | network isolation       │
├─────────────────────────────────────────────────┤
│          Enterprise Policy (最高覆盖)              │
│  managed-only | kill switch | feature gates       │
└─────────────────────────────────────────────────┘
```

**核心设计原则**：

1. **默认拒绝** — 无规则匹配时始终 ask
2. **Bypass-Immune** — 安全检查无法被任何模式覆盖
3. **分层覆盖** — 会话 > CLI > 策略 > 项目 > 用户
4. **深度防御** — 规则 + 管道 + hooks + 沙箱 + 策略五重保障
5. **可观测性** — 每个决策都有 `decisionReason` 追溯原因
