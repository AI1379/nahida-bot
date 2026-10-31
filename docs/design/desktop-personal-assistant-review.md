# Code Quality Review

## Summary

新增业务已分离为 TODO、简报和通知路由插件；主要维护风险仍在原有大型 Gateway、API bridge 和 Desktop 连接模块，当前改动限定为协议及服务接线。

## Overall Assessment

使用仓库 `fuck-u-code-analysis` 技能与 `fuck-u-code 2.2.2`，对本轮涉及的完整实现文件做扫描，并与相同已有文件的 HEAD 版本比较。以下不是全仓库评分；Vue 文件未被扫描器解析。

| Metric | Score |
|--------|-------|
| Overall | 75.53/100 |
| Existing-file baseline | 73.64/100 |
| New-file weighted score | 92.28/100 |
| Files Analyzed | 39 |
| Critical Issues | 28 个扫描器指标标记；人工检查未确认本轮新增的阻断问题 |

扫描器没有输出 `locations`，下文行号来自源码核对。指标不能替代行为测试；特别是 Python
私有方法的下划线命名，以及让异常上抛到已有边界，被扫描器误判为命名或错误处理问题。

2026-10-31 发布前追加复核：包含 `/status` 精简改动的 40 个实现文件扫描得分 75.01；
`builtin/commands.py` 为 70.07，主要标记为既有文件体积（1,190 行代码）和命名启发式。
实际新增差异经过人工检查与定向回归，未发现阻断部署的问题。

## Key Issues (sorted by severity)

- **`RealBotAPI` (`nahida_bot/plugins/api_bridge.py`)**：完整文件 2,107 行代码、构造参数 31 个。新增服务逻辑已抽到 `service_api.py`；后续接入优先复用注册表。
- **`run_once` (`desktop/src-tauri/src/gateway_node/mod.rs:408`)**：所在连接模块 1,029 行代码。新增状态采集已独立到 `desktop_context.rs`，业务路由保留服务器插件。
- **`NotificationDelivery._process` (`nahida_bot/plugins/notification_router/delivery.py:30`)**：首次扫描通知入口得分 79.71；拆开注册、纯路由选择及投递后，入口 86.59、投递模块 86.81。
- **`TaskService.update` (`nahida_bot/plugins/todo/service.py:62`)**：人工检查补充操作 ID 内容一致性校验；重复操作不得隐式覆盖其他修改。
- **`acceptReadingNotification` (`desktop/src/services/readingNotifications.ts`)**：人工检查修复本地保存失败后的内存去重残留；磁盘失败不能导致重试返回虚假的 accepted。

## Refactoring Plan

1. 已完成：将通知投递拆到 `notification_router/delivery.py`，路由选择留在 `models.py`。
2. 已完成：将桌面阅读流程拆到 `readingNotificationController.ts`，绕开语音流水线。
3. 已完成：将 Gateway 传输注册封装到 `desktop_delivery.py`，避免把通知规则写入 Gateway 初始化。
4. 后续扩展复用这些接口；大型既有模块的整体重构另行安排，不混入本轮业务实现。

## Security Concerns

未发现本轮新增的未处理跨账户访问路径。Desktop 调用使用 token 绑定身份；服务显式开放后
才可由 Desktop 调用；任务按账户隔离；客户端 metadata 不参与授权；私聊备用目标只能是
通知所属账户。状态数据不包含窗口标题、输入内容或截图。

服务端插件属于可信 Python 扩展，不宣称该接口可以隔离恶意插件代码。外部渠道发送结果
未知时保留 unknown；Desktop accepted 仅代表本地持久化，不代表用户已读。
