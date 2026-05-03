# 目录结构与模块边界

## 建议目录边界

建议使用如下目录边界（按当前仓库渐进演化）：

```text
nahida_bot/
  core/
    app.py
    config.py
    events.py
    logging.py
    exceptions.py
  workspace/
    manager.py
    sandbox.py
    templates/
  agent/
    loop.py
    context.py
    memory.py
    tools.py
    providers/
      base.py
      openai.py
      registry.py
  plugins/
    manager.py
    loader.py
    manifest.py
    permissions.py
    registry.py
    base.py                      # Channel、Tool、Hook 基类
    builtin/
      __init__.py
      channel.py                 # ChannelService 协议 / channel 服务契约
      # 具体 Channel 实现（内置插件示例）：
      # - telegram_channel.py
      # - qq_channel.py (via NapCat)
      # - matrix_channel.py
    builtin_tools/
      file_reader.py
      command_executor.py
      web_fetcher.py
      memory_retrieval.py
  gateway/
    server.py
    router.py
    node_manager.py
    protocol.py
  node/
    client.py
    connector.py
    executor.py
  db/
    engine.py
    models.py
    repositories/
  cli/
    main.py
```

## 重点说明

1. **ChannelService 协议** 在 `plugins/base.py` 或专属协议模块中定义
   - 定义标准接口（`handle_inbound_event`、`send_message`、`get_user_info` 等）
   - 声明支持的通信方式（HTTP Server/Client、WebSocket、SSE）
   - 嵌入权限声明和生命周期挂钩

2. **内置 Channel 实现** 在 `plugins/builtin/` 下，作为普通 Plugin 暴露 channel service
   - 每个 Channel 是一个标准 Plugin，有 `plugin.yaml` 和实现代码
   - 通过 Plugin Manager 加载，享受权限隔离和热加载机制

3. **第三方 Channel 插件** 结构相同，可外部贡献
   - 遵循同一的 Plugin 接口契约
   - 无须修改核心代码
