# MCP 动态服务管理

## 背景

MCP（Model Context Protocol）服务配置原本只支持通过 `config.yaml` 静态定义，启动时加载一次。但在实际使用中，bot 需要在运行时动态添加 MCP 服务器（例如用户让 bot 自己连接一个新的工具服务）。

由于项目整体不计划支持 config 热更新，MCP 的动态配置通过 **Plugin Data Store**（插件数据存储）实现，将动态服务器配置持久化到 `plugin_data` 表中。

## 数据流

```text
config.yaml (static)          plugin_data 表 (dynamic)
       │                              │
       ▼                              ▼
  MCPServerConfig              MCPServerConfig
       │                              │
       └──────────┬───────────────────┘
                  ▼
         合并（static 优先）
                  │
                  ▼
          _connect_and_register()
                  │
                  ▼
         ToolRegistry 注册工具
```

**合并策略**：当 `server_key` 在 static 和 dynamic 中同时存在时，static 定义优先。这保证运维在 `config.yaml` 中的配置不会被动态操作意外覆盖。

## 管理入口

### LLM 工具（bot 自管理）

MCP 插件在 `on_load` 时自动注册以下工具，LLM agent 可以通过对话来管理 MCP 服务器：

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `mcp_add_server` | 添加动态 MCP 服务器并连接 | `server_key`, `transport`, `command`/`args`/`url`, `namespace`, `enabled` |
| `mcp_remove_server` | 移除动态服务器（断开连接+删除配置） | `server_key` |
| `mcp_list_servers` | 列出所有服务器及其状态 | 无 |
| `mcp_reload_server` | 重连指定服务器 | `server_key` |

动态添加默认受 allowlist 限制，避免对话侧直接让 bot 启动任意本机命令或连接任意 URL：

```yaml
mcp:
  allowed_dynamic_url_prefixes:
    - "http://localhost:3000/"
  allowed_dynamic_stdio_commands:
    - "npx"
```

空 allowlist 表示不允许对应类型的动态服务器。static `servers:` 配置不受这个限制，因为它来自运维配置文件。

**示例对话**：

```
用户: 帮我连上本地 3000 端口的 MCP 服务器
Bot: [调用 mcp_add_server, server_key="local-api", transport="sse", url="http://localhost:3000/sse"]
Bot: 已连接 local-api 服务器，发现 5 个工具可用。
```

> **注意**：MCP 作为插件，没有专属的 REST API 端点。动态管理完全通过 LLM 工具完成。外部工具如需操作，可通过 `/api/plugins/mcp/reload` 触发插件重载，或未来通过通用的 `/api/plugins/{id}/data` 端点管理插件数据。

## 持久化与重启

动态服务器配置存储在 `plugin_data` 表中，键格式为 `server:{server_key}`，值为 `MCPServerConfig` 的 JSON 序列化。

重启流程：
1. `MCPPlugin.on_load()` 调用 `plugin_data_list(prefix="server:")` 读取所有动态配置
2. 过滤不再满足 allowlist 的动态配置
3. 与 static config 合并
4. 逐个连接并注册工具

## 权限要求

MCP 插件需要在 `plugin.yaml` 中声明：

```yaml
permissions:
  network:
    outbound: ["*"]
  system:
    subprocess: true
  plugin_data:
    read: true
    write: true
```

## 限制

- **不能移除 static 服务器**：`mcp_remove_server` 只能移除动态添加的服务器。static 服务器需要修改 `config.yaml` 并重启。
- **工具命名空间**：动态服务器的工具同样使用 `{namespace}__{tool_name}` 命名格式。
- **连接失败不阻塞**：添加服务器时如果连接失败，配置仍会保存。下次重启时会自动尝试重连。
