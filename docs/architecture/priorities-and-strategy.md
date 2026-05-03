# 模块优先级与开发策略

## 模块优先级

不严格按时间，而按"依赖风险 + 价值密度"排序。

### P0 - 必须先做

- `core`：应用容器、配置、日志、异常、事件
- `workspace`：安全文件边界、模板、上下文读取
- `agent.loop`：基础回路和终止条件
- `agent.providers.base + openai`：首个可用 Provider
- `agent.tools`：工具协议与执行器

原因：这些模块决定系统是否能形成最小闭环。

### P1 - MVP 关键能力

- `plugins.base + plugins.manifest/loader/manager`：插件发现、加载和生命周期管理
  - 包括 ChannelService 协议与显式服务注册设计（参考 OneBot/NapCat 多协议设计）
  - Plugin manifest 要明确支持的通信方式
- `plugins.permissions`：声明式权限系统（文件、网络、环境变量等）
- 首个具体 channel service plugin 实现（内置或内置示例）
  - 选择 **Telegram**（推荐：API 简单、建议 aiogram 库参考）
  - 或 **NapCat/QQ**（参考 OneBot webhook + HTTP 双向通信）
  - 设计重点：支持至少两种通信方式（如 HTTP Server + HTTP Client）
- `agent.memory + db.engine + repositories`：会话与记忆持久化

原因：把"单机可跑"推进到"多平台真实可用"，且 **Channel 通过插件系统接入，无需改动核心代码**。

### P2 - 可扩展与分布式

- `gateway.server/router/node_manager`：Gateway 消息路由和节点管理
- `node.client/connector/executor`：Node 连接器和远程执行
- `gateway.protocol`（版本化）：WebSocket 协议定版
- `plugins.hook` 和热加载：Hook 系统和插件动态加载
- 更多 channel service plugin 实现（第二、第三个平台）：
  - 参考第一个 Channel 的设计，复用普通 Plugin + ChannelService 协议模式
  - 可通过开发文档指导第三方贡献 Channel 插件

原因：进入多节点和复杂扩展场景，同时 Channel 生态可独立扩展。

### P3 - 体验与生态

- `cli` 完整命令集
- WebUI 管理与可视化
- 文档体系、示例插件、发布流水线

原因：提升可维护性、可交付性和社区可接入性。

## 开发策略建议

- 先契约后实现：先固定输入输出模型，再写具体逻辑。
- 每完成一个优先级层级，进行一次接口冻结。
- 每个模块至少同时交付：代码、测试、文档最小集。
- 避免跨层捷径调用，宁可加一个小的协议层。
