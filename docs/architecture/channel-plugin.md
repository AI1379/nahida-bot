# ChannelPlugin 设计细节（Plugin 系统的扩展）

## 背景与设计目标

**为什么 Channel 是 Plugin？**

- 复用插件权限系统、生命周期管理和能力注册机制
- 支持第三方 Channel 插件无须修改核心代码
- 灵活支持多种通信协议（HTTP、WebSocket、SSE）组合

**参考项目**：OpenClaw Gateway-Channel 模型、OneBot 协议、NapCat 的多通道设计

## ChannelPlugin 核心接口

```python
class ChannelPlugin(Plugin):
    """所有 Channel 插件的基类。"""

    # 通信方式支持声明（在 plugin.yaml 中对应 capabilities）
    SUPPORT_HTTP_SERVER: bool = False      # Bot 提供 HTTP 端点，外部推送
    SUPPORT_HTTP_CLIENT: bool = False      # Bot 主动 HTTP 请求外部系统
    SUPPORT_WEBSOCKET_SERVER: bool = False # Bot 提供 WebSocket，外部连接
    SUPPORT_WEBSOCKET_CLIENT: bool = False # Bot 连接到外部 WebSocket
    SUPPORT_SSE: bool = False              # Bot 通过 SSE 单向推送

    async def handle_inbound_event(self, event: dict) -> None:
        """处理来自外部系统的事件（webhook、WebSocket 消息等）。

        将平台原生事件格式转换为 InboundMessage，并触发 Agent Loop。
        """
        ...

    async def send_message(
        self,
        target: str,  # 平台用户/群组 ID
        message: OutboundMessage
    ) -> str:
        """向外部系统发送消息，返回平台消息 ID。

        支持流式响应分片、速率限制等。
        """
        ...

    async def get_user_info(self, user_id: str) -> dict:
        """获取用户信息（可选）。"""
        ...

    async def get_group_info(self, group_id: str) -> dict:
        """获取群组信息（可选）。"""
        ...
```

## 通信方式详解

各 Channel 可选择一种或多种组合：

| 方式 | 说明 | 场景 | 示例 |
| ---- | ---- | ---- | ---- |
| **HTTP Server** | Bot 提供 HTTP POST 端点接收 webhook | 外部系统主动推送事件 | NapCat `POST /channels/qq/webhook` |
| **HTTP Client** | Bot 主动轮询或通过 HTTP 请求向外部发送消息 | Bot 需主动控制消息流向 | Telegram Polling API、HTTP 心跳 |
| **WebSocket Server** | Bot 监听 WebSocket 端口，外部系统连接 | 需要持久连接、双向实时通信 | Web 端、自定义客户端 |
| **WebSocket Client** | Bot 连接到外部 WebSocket 端点 | Bot 作为客户端和中心网关通信 | 云服务集中管理 |
| **SSE** | Bot 通过 HTTP SSE 单向推送事件 | 只需事件流，不需请求-响应模式 | 浏览器、Node.js 客户端 |

**注意：这里的通信方式只是可选，这里同时也允许 ChannelPlugin 使用原生 Python API 直接调用对应 Channel 的处理，比如直接调用 Python 库操作 Telegram Bot。**

## 典型设计模式（参考 NapCat/OneBot）

### 模式 A：HTTP Server + HTTP Client（推荐开始）

```text
外部系统 ──webhook──> Bot HTTP Server ──处理──> InboundMessage
        <────HTTP────                          OutboundMessage
```

优点：

- 无需长连接，可跨域
- 支持负载均衡和水平扩展
- 外部系统可异步推送，Bot 可异步发送

缺点：

- 需要鉴权机制（防 webhook 伪造）
- 频率限制和重试需自行管理

NapCat 示例配置：

```yaml
channels:
  qq:
    type: qq
    plugin: qq_channel
    config:
      webhook_url: "http://bot.local:8888/channels/qq/webhook"
      api_endpoint: "http://napcat.local:3000"
      token: "secret_token"
```

### 模式 B：WebSocket 双向（长连接、实时）

```text
外部系统 ◄──► Bot WebSocket Server
```

优点：

- 真正双向、低延迟
- 一个连接复用，性能好

缺点：

- 需要连接管理和重连机制
- 难以水平扩展（需要 session 亲和性）

### 模式 C：混合模式（最灵活）

```text
接收事件用 WebSocket Server
发送消息用 HTTP Client
```

或其他组合，根据平台特性选择。

## plugin.yaml 示例

```yaml
id: qq_channel
name: "QQ Channel (via NapCat)"
version: "0.1.0"
description: "QQ 平台接入（通过 NapCat OneBot 协议）"

# 权限声明
permissions:
  - resource: "network"
    action: ["http_post", "http_get", "websocket"]
    targets:
      - "http://napcat.local:*"
      - description: "NapCat 本地服务"

# 能力声明
capabilities:
  - type: "channel"
    name: "qq_channel"
    protocols:
      - "http_server"    # Bot 提供 webhook 端点
      - "http_client"    # Bot 向 NapCat 发送消息

# 配置架构
configSchema:
  type: "object"
  properties:
    webhook_secret:
      type: "string"
      description: "Webhook 鉴权密钥（玄性）"
    napcat_endpoint:
      type: "string"
      description: "NapCat API 端点"
      default: "http://localhost:3000"
    bot_qq:
      type: "string"
      description: "Bot 的 QQ 号"
```

## 权限与安全

- ChannelPlugin 需声明 network 权限（HTTP、WebSocket）
- Webhook 端点需要鉴权（HMAC 签名或 Token）
- 频率限制由 Plugin 或 Gateway 负责
- 敏感信息（Token、密钥）走系统级 Secret 管理

## 与 Phase 的关系

- **Phase 3**：定义 ChannelPlugin 基类、接口规范、manifest 契约
- **Phase 4**：实现第一个 ChannelPlugin（如 Telegram 或 QQ/NapCat）
- **Phase 5+**：第三方 Channel 插件生态，无需核心改动
