# Channel Service Plugin 设计细节

## 背景与设计目标

Channel 仍然是插件系统的一部分，但不再通过专门的 channel 基类建模。
当前设计将 Channel 收敛为：

- 一个普通 `Plugin`
- 在生命周期里显式调用 `api.register_channel(...)`
- 暴露满足 `ChannelService` 协议的运行时服务

这样做的目标是：

- 复用统一的插件权限、生命周期、异常隔离与配置机制
- 避免为 Channel 和 Provider 维护两套特殊插件继承体系
- 让运行时只依赖协议和显式注册，而不是依赖具体插件子类

## ChannelService 协议

```python
class ChannelService(Protocol):
    @property
    def channel_id(self) -> str: ...

    async def handle_inbound_event(self, event: dict[str, Any]) -> None: ...

    async def send_message(
        self, target: str, message: OutboundMessage
    ) -> str: ...
```

可选扩展能力：

- `get_user_info(user_id)`
- `get_group_info(group_id)`
- `download_media(file_id, destination=None)`

这些辅助接口不是注册所必需的最小契约，由具体 channel 按需实现。

## 插件如何暴露 Channel

Channel 由普通插件实例自己注册：

```python
class TelegramPlugin(Plugin):
    @property
    def channel_id(self) -> str:
        return self.manifest.id

    async def on_load(self) -> None:
        ...
        self.api.register_channel(self)
```

`PluginManager` 不再根据插件类型自动识别和注册 channel。
`ChannelRegistry` 只存储满足 `ChannelService` 协议的对象。

## 注册校验

`RealBotAPI.register_channel()` 通过运行时协议校验确保注册对象合法：

- 对象必须满足 `ChannelService` 协议（`isinstance` 检查）
- 通过注册表显式加入 `ChannelRegistry`
- 插件 disable/unload 时自动取消注册

这意味着：

- "是不是 channel"由行为决定，而不是由 manifest 字段或继承关系决定
- 核心只依赖 `ChannelService` 协议，不依赖专门基类

## 与 Provider 的对称性

Provider 采用：

- 普通 `Plugin`
- `load_phase: pre-agent`
- `api.register_provider_type(...)`

Channel 采用：

- 普通 `Plugin`
- 注册时 `isinstance(channel, ChannelService)` 校验
- `api.register_channel(...)`

两者都属于"普通插件 + 服务注册"的统一设计，只是约束轴不同：

- Provider 主要关心初始化时序（`load_phase`）
- Channel 主要关心运行时协议满足（`ChannelService`）

## 当前边界

本轮重构不包含：

- webhook 自动挂载
- plugin config schema 的 host 侧校验
- provider 静态注册整体插件化

当前重点只是统一插件类别设计，移除这类架构必需的专门 channel 基类。
