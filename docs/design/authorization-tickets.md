# 临时授权票据（Phase 1）

> 状态：Phase 1 已实现；关联 issue #33、身份系统 #7。

## 边界

临时授权建立在现有 `identity.admins` 和 `AuthorizationGate` 上：

- 只有平台认证后的 account key 能成为管理员或申请人；
- Person 映射用于身份与记忆，不产生权限；
- `identity_manage` 永远只能由声明管理员执行，不能通过票据委托；
- 可委托工具目前为 `exec`、`message`、`workspace_write`。

## 流程

1. 非管理员用 `/auth request <tool> <JSON arguments>` 申请。
2. 系统生成短期 challenge，绑定申请人的 account key、工具名及规范化参数 SHA-256。
3. 管理员用 `/auth approve <challenge> [ttl_seconds]` 批准。
4. 申请人触发完全相同的工具调用时，授权闸在执行前消费 grant。
5. 无论工具随后成功或失败，该 grant 都不能再次使用。

批准不是 bearer token：challenge 泄露不会让第三方获得权限，grant 也只对原申请账号
生效。参数使用排序后的 canonical JSON 计算指纹，防止批准 `workspace_write` 的一个路径后
被替换为其他路径或内容。

## Fail-safe 行为

- 功能默认关闭，必须同时启用 `identity.enabled` 和
  `identity.authorization_tickets.enabled`；
- challenge 和 grant 都有短 TTL；
- 管理员可用 `/auth revoke` 撤销；
- 状态仅在进程内存保存，重启全部失效；
- 空参数、未知工具、管理员自助申请、身份管理委托均被拒绝。

## 后续阶段

- 持久化只保存哈希、状态和审计字段，并保证重启后的原子消费；
- 为 `exec` 等工具增加结构化 scope/constraint，而不要求调用方复制完整 JSON；
- WebUI 审批界面和跨会话定向通知；
- 管理员审批时显示风险摘要和敏感参数脱敏；
- 将 grant 消费结果写入 canonical execution receipt。
