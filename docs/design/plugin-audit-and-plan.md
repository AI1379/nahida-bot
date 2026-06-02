# 插件系统审计与补全计划

> 状态：设计草案
> 日期：2026-06-02
> 最近更新：2026-06-02
> 目标：审计 plugin.yaml 各字段的运行时实际利用率，补全空壳功能，新增 WebUI 插件管理面板。
> 相关文档：
>
> - [plugin-system.md](../architecture/plugin-system.md)
> - [webui-design.md](webui-design.md)
> - [channel-plugin.md](../architecture/channel-plugin.md)
> - [runtime-flows.md](../architecture/runtime-flows.md)

---

## 1. 审计摘要

对 `plugin.yaml` 的 13 个字段和 `PermissionChecker` 的 8 个方法进行了端到端追踪。结论：**插件系统骨架完整，但约 38% 的 manifest 字段和 38% 的权限检查方法属于"解析入库但无运行时效果"的空壳**。

### 1.1 Manifest 字段利用率

| 字段 | 解析 | 运行时消费 | 消费位置 |
|------|:--:|:---------:|---------|
| `id` | ✅ | ✅ | `manager.py` 主键、事件 payload、权限错误信息、工具/命令归属 |
| `name` | ✅ | ✅ | 日志输出、`PluginPayload.plugin_name`、config schema UI |
| `version` | ✅ | ✅ | 日志输出、`PluginPayload.plugin_version` |
| `description` | ✅ | ❌ | **全代码库零引用**——未被日志、事件、UI 任何地方消费 |
| `entrypoint` | ✅ | ✅ | `loader.py:66-73` 模块导入和类查找，`loader.py:142` 卸载时模块路径提取 |
| `nahida_bot_version` | ✅ | ❌ | **从未与实际 bot 版本比较**——无兼容性门禁 |
| `sdk_version` | ✅ | ❌ | **从未校验**——等 SDK 独立包抽离后再实现 |
| `load_phase` | ✅ | ✅ | `manager.py` 分阶段加载过滤，`api_bridge.py` provider 注册前置校验 |
| `permissions` | ✅ | ⚠️ | 5/8 子检查有调用链（见 §1.2），3 个完全死方法 |
| `capabilities` | ✅ | ❌ | **全代码库零引用**——`Capabilities.tools` 和 `Capabilities.subscribes_to` 无任何代码读取 |
| `config` | ✅ | ✅ | `app.py` 配置注入合并，各插件 `self.manifest.config` 运行时读取 |
| `config_schema` | ✅ | ✅ | `config_schema.py` Web 图形化配置编辑器使用 |
| `depends_on` | ✅ | ❌ | **`PluginManager` 无拓扑排序**——加载顺序完全依赖文件系统扫描顺序 |

**统计**：13 个字段中 5 个是空壳（`description`、`nahida_bot_version`、`sdk_version`、`capabilities`、`depends_on`），占比 38%。

### 1.2 PermissionChecker 方法利用率

| 方法 | 调用者 | 实际触发 |
|------|--------|:-------:|
| `check_network_outbound(url)` | `send_message()` | ⚠️ 有调用但语义错误——传 chat address 做 URL glob 匹配 |
| `check_network_inbound()` | 无 | ❌ |
| `check_filesystem_read("workspace")` | `workspace_read()`, `resolve_workspace_path()` | ✅ builtin 插件大量调用 |
| `check_filesystem_write("workspace")` | `workspace_write()` | ✅ builtin 插件大量调用 |
| `check_memory_read()` | `memory_search()` | ✅ builtin 插件调用 |
| `check_memory_write()` | `memory_store()` | ✅ builtin 插件调用 |
| `check_subprocess()` | 无 | ❌ |
| `check_env_var(key)` | 无 | ❌ |
| `check_signal_handlers` | 不存在 | ❌ 模型有字段但 PermissionChecker 没有方法 |

**额外问题**：14 个 `RealBotAPI` 方法完全没有权限检查——`register_tool`、`register_command`、`register_channel`、`subscribe`、`publish_event` 等全部裸奔。

**统计**：8 个权限维度中 3 个完全死方法 + 1 个不存在的方法，覆盖率 50%。即便是已覆盖的维度，检查也只贴在 5 条 API 路径上。

### 1.3 已有插件对 self.api 的实际使用模式

审计了所有 5 个已有插件的 `self.api` 调用：

| 插件 | 使用的 API |
|------|-----------|
| builtin/commands | `register_command`、`register_tool`、`workspace_read`、`workspace_write`、`memory_search`、`memory_store`、`send_message`、`record_session_event`、`clear_session`、`start_new_session`、`get_session_info`、`get_session_run_status`、`list_models`、`set_session_model`、`update_runtime_settings`、`list_commands`、`resolve_workspace_path`、`scheduler_service` |
| mcp | `register_tool` |
| channels/onebot | `register_channel`、`publish_event`、`register_tool` |
| channels/milky | `register_channel`、`publish_event`、`register_tool` |
| channels/telegram | `register_channel`、`publish_event`、`register_tool` |

**关键发现**：只有 builtin 插件真正走 `workspace_read/write` 和 `memory_search/store` 的权限检查路径。Channel 插件主要用 `register_channel` + `publish_event` + `register_tool`——这三条路径全部无权限检查。

---

## 2. 待补全功能清单

### 2.1 P0：`nahida-bot-sdk` 独立包抽离 + 插件测试控制台

**现状**：虽然 `Plugin`、`BotAPI`、`PluginManifest`、`OutboundMessage` 等类型在设计上属于 SDK 层，但它们目前全部混在 `nahida_bot/plugins/` 目录中，与 `RealBotAPI`、`PluginManager`、`PermissionChecker` 等运行时实现共存于同一个包内。这意味着：

- 插件开发者必须 `pip install nahida-bot` 整个项目才能获得类型定义，拉入了 `aiosqlite`、`fastapi`、`uvicorn`、`httpx`、`mcp` 等全部重型运行时依赖。
- 插件测试需要启动完整的 bot 或手动 mock 内部对象。
- 插件代码与 bot 内部实现之间没有强制的 import 边界——插件随时可以 `from nahida_bot.core import ...` 绕过 BotAPI。

**本轮测试目标**：在一个完全隔离的目录中编写一个插件（不依赖 nahida-bot 源码），该插件能注册 command 和工具，并通过 `uv pip install` 安装后在 bot 中运行。

**仓库结构**（uv workspace monorepo，主包不移动）：

```text
nahida-bot/                          ← git 仓库根（也是 nahida-bot 主包根）
├── pyproject.toml                   ← nahida-bot 主包 + workspace 声明
├── nahida_bot/                      ← 主包代码（不挪位置）
├── uv.lock                          ← 统一 lockfile
│
├── nahida-bot-sdk/                  ← workspace 子成员（只新增此目录）
│   ├── pyproject.toml               ← nahida-bot-sdk 包
│   ├── nahida_bot_sdk/
│   │   ├── __init__.py              ← 公开 API re-export
│   │   ├── plugin.py                ← Plugin 基类
│   │   ├── api.py                   ← BotAPI 协议 (Protocol, runtime_checkable)
│   │   ├── manifest.py              ← PluginManifest 及所有权限/能力模型
│   │   ├── messaging.py             ← InboundMessage, OutboundMessage, CommandResult
│   │   ├── events.py                ← 事件类型定义 (MessageReceived 等)
│   │   ├── logging.py               ← PluginLogger 协议
│   │   │
│   │   └── testing/
│   │       ├── __init__.py
│   │       ├── mock_api.py          ← MockBotAPI（无需启动 bot）
│   │       └── console.py           ← 插件测试控制台（见下文）
│   │
│   └── tests/
│       └── test_mock_api.py
│
├── docs/
├── tests/
└── ...
```

**配置方式**：

根 `pyproject.toml` 新增 workspace 声明和 SDK 依赖源：

```toml
# 根 pyproject.toml（nahida-bot 主包，位置和内容基本不变）
[project]
name = "nahida-bot"
dependencies = [
    "nahida-bot-sdk",
    # ... 其余依赖不变
]

[tool.uv.sources]
nahida-bot-sdk = { workspace = true }

[tool.uv.workspace]
members = ["nahida-bot-sdk"]
```

SDK 的 `pyproject.toml`：

```toml
# nahida-bot-sdk/pyproject.toml
[project]
name = "nahida-bot-sdk"
version = "0.1.0"
description = "Plugin SDK for nahida-bot"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**为什么不把主包也挪进子目录**：移动 `nahida_bot/` 到 `packages/nahida-bot/` 下会产生巨大的重命名 diff，破坏 git blame/blame 历史和所有 PR 的上下文。uv workspace 的 `members` 不要求主包在子目录里——主包就是仓库根本身，只有 SDK 是子成员。**零移动、零重命名**。

**依赖约束**：`nahida-bot-sdk` 只依赖 `pydantic>=2.0` 和 `typing_extensions`。不引入任何网络、数据库、Web 框架。

**开发与发布**：

- 开发：`uv sync` 一步装好主包 + SDK，workspace 内依赖自动联动。
- 发布 SDK：`cd nahida-bot-sdk && uv build && uv publish`，产出独立的 wheel/tarball 上传 PyPI。
- 发布主包：`uv build && uv publish`，PyPI 从 `name` 字段区分包，不看仓库结构。
- CI 自动化：打 tag 后 GitHub Actions 先发 SDK 再发主包（SDK 版本号更新后主包才能引用到新版本）。

**迁移路径**（从当前混合状态到 uv workspace）：

1. 根 `pyproject.toml` 新增 `[tool.uv.workspace]` 和 `[tool.uv.sources]` 段。
2. 创建 `nahida-bot-sdk/` 目录，初始化其 `pyproject.toml`。
3. 将 `nahida_bot/plugins/base.py` 中的 `Plugin`、`BotAPI`、`OutboundMessage`、`InboundMessage`、`CommandResult` 等纯类型定义移动到 SDK。
4. 将 `nahida_bot/plugins/manifest.py` 中的 `PluginManifest` 及权限/能力模型移动到 SDK。
5. 在 SDK 中实现 `MockBotAPI`（已有设计稿，见 `docs/architecture/plugin-system.md` §3.5）。
6. nahida-bot 改为 `from nahida_bot_sdk import Plugin, BotAPI, ...`。
7. 删除 `nahida_bot/plugins/` 中的重复定义，保留 `RealBotAPI`、`PluginManager`、`PermissionChecker` 等运行时实现。
8. 现有 5 个插件改为依赖 `nahida-bot-sdk` 而非 `nahida-bot`。
9. `uv sync` 验证 workspace 正常工作。

**插件测试控制台**：

为降低插件开发门槛，在 SDK 的 testing 模块中提供一个简单的命令行 REPL 作为"假的聊天界面"：

```
$ python -m nahida_bot_sdk.testing.console ./plugins/my-plugin

  ╔══════════════════════════════════════╗
  ║   nahida-bot-sdk Plugin Console      ║
  ║   Plugin: my-plugin v0.1.0           ║
  ║   Type /help for commands            ║
  ╚══════════════════════════════════════╝

  You: /hello
  Bot: Hello! I'm a test plugin.

  You: trigger tool:my_tool {"arg": "value"}
  [Tool my_tool returned]: {"result": "ok"}

  You: event:MessageReceived {"text": "hello"}
  [Plugin handler called with MessageReceived]

  You: /quit
  Goodbye!
```

控制台的功能边界：

| 功能 | 说明 |
|------|------|
| `/help` | 列出可用命令 |
| `/quit` | 退出控制台 |
| 直接输入文本 | 如果插件注册了 message handler，模拟触发 `MessageReceived` 事件 |
| `tool:<name> <json>` | 手动调用已注册的工具，打印返回值 |
| `event:<type> <json>` | 手动触发任意事件 |
| `/commands` | 列出插件注册的命令 |
| `/tools` | 列出插件注册的工具 |

这不是喧宾夺主——它是纯粹的**开发工具**，类似于 `pytest`、`uvicorn --reload`、或者 Rasa 的 `rasa shell`。它的价值在于：

- 插件开发者不需要启动完整的 nahida-bot（数据库、channel、provider、agent loop）就能测试命令和工具的基本逻辑。
- MockBotAPI 提供可控的假数据（假 session、假 workspace 文件、假 memory），测试可重复。
- 控制台本身不到 200 行代码，不引入新依赖（纯 `input()` / `asyncio`）。

**涉及文件**：

- `nahida-bot-sdk/pyproject.toml` —— 新建
- `nahida-bot-sdk/nahida_bot_sdk/` —— SDK 包主体
- `nahida-bot-sdk/nahida_bot_sdk/testing/mock_api.py` —— MockBotAPI
- `nahida-bot-sdk/nahida_bot_sdk/testing/console.py` —— 测试控制台
- `nahida_bot/plugins/base.py` —— 改为从 SDK re-export
- `nahida_bot/plugins/manifest.py` —— 改为从 SDK re-export
- `nahida_bot/pyproject.toml` —— 新增 `nahida-bot-sdk` 依赖

### 2.2 P1：插件外部 Python 依赖管理 —— 每个插件是一个 Python package

**现状**：插件系统完全没有处理外部 Python 库依赖的机制。当前 5 个已有插件的第三方依赖（`httpx`、`aiohttp`、`aiogram` 等）全部作为 nahida-bot 自身的依赖写在根 `pyproject.toml` 中。这对第三方插件不可行。

**设计决策**：每个插件自带 `pyproject.toml`，成为一个标准 Python package。这不是替代 `plugin.yaml`，而是双文件分工：

```text
plugins/my-plugin/
├── plugin.yaml          ← nahida-bot 特有元数据（id、entrypoint、permissions、capabilities…）
├── pyproject.toml       ← Python 标准打包元数据（name、version、dependencies、requires-python）
├── my_plugin/
│   ├── __init__.py
│   └── plugin.py        ← class MyPlugin(Plugin): ...
└── README.md
```

**`pyproject.toml` 示例**：

```toml
[project]
name = "nahida-plugin-image-gen"
version = "0.1.0"
description = "AI image generation plugin for nahida-bot"
requires-python = ">=3.12"
dependencies = [
    "nahida-bot-sdk>=0.1.0",    # SDK 版本约束（替代 sdk_version）
    "nahida-bot>=0.1.0",        # bot 版本约束（替代 nahida_bot_version）
    "pillow>=10.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**`plugin.yaml` 对应精简后的样子**：

```yaml
id: "com.example.image-gen"
name: "Image Generator"
version: "0.1.0"
description: "AI image generation via Stable Diffusion"
entrypoint: "my_plugin.plugin:ImageGenPlugin"
load_phase: "post-agent"

permissions:
  network:
    outbound: ["https://api.stability.ai/*"]
  filesystem:
    read: ["workspace"]
    write: ["workspace"]

capabilities:
  tools:
    - name: "generate_image"
      description: "Generate an image from a text prompt"

depends_on:
  - id: "builtin-commands"
    version: ">=0.1.0"

config:
  api_key: ""
  default_size: "1024x1024"

config_schema:
  type: "object"
  properties:
    api_key:
      type: "string"
      description: "Stability AI API key"
      secret: true
    default_size:
      type: "string"
      default: "1024x1024"
      enum: ["512x512", "768x768", "1024x1024"]
  required: ["api_key"]
```

**字段分工原则**：

| 来源 | 包含内容 | 读者 |
|------|---------|------|
| `pyproject.toml` | Python 包名、版本、Python 依赖、可选依赖组、构建系统 | pip / uv / PyPI |
| `plugin.yaml` | nahida-bot 插件 ID、入口点、权限、能力声明、插件间依赖、配置项 | PluginManager / WebUI |

允许少量重复（`name`、`version`、`description` 在两个文件中都出现）——`pyproject.toml` 的值用于 PyPI 发布，`plugin.yaml` 的值用于运行时日志和 WebUI 展示。如果用户觉得维护两份有些冗余，后续可以加一个 `parse_manifest_from_pyproject()` 自动从 `pyproject.toml` 中提取 `name`/`version`/`description` 作为 fallback。

**安装流程**：

```bash
# 用户获取插件（git clone / 下载 zip / pip install）
git clone https://github.com/user/nahida-plugin-image-gen plugins/image-gen

# 安装插件及其 Python 依赖（uv 一步完成）
uv pip install ./plugins/image-gen

# 启动 bot，PluginManager 扫描 plugin.yaml 并加载
nahida run
```

**PluginManager 侧的改动**：

1. `PluginLoader.load()` 中如果 `importlib.import_module()` 失败，解析 `ImportError`/`ModuleNotFoundError` 给出友好提示：
   ```
   Plugin 'image-gen' failed to load: missing dependency 'pillow'.
   Install it with: uv pip install ./plugins/image-gen
   ```
2. `PluginManager` 在发现插件目录中存在 `pyproject.toml` 时，可以解析 `dependencies` 列表并在 WebUI 中展示，但不自动安装。
3. 是否需要检查依赖是否满足（`importlib.metadata` 校验版本）留到后续 Phase——初期只做友好的错误提示。

**关于安全性**：`PluginManager` **永远不自动执行 `pip install` 或 `uv pip install`**。依赖安装是管理员的显式操作。WebUI 可以在插件详情面板中显示"缺失依赖：pillow>=10.0"并指引管理员手动安装。

**对现有字段的影响**：

- `nahida_bot_version` → 主要版本门禁改为 `pyproject.toml` 中的 `nahida-bot>=X.Y` 依赖声明。manifest 中的 `nahida_bot_version` 降级为辅助字段（warn-only）。
- `sdk_version` → 被 `pyproject.toml` 中的 `nahida-bot-sdk>=X.Y` 替代，manifest 字段保留但意义不大。
- `requires` → 不需要在 `plugin.yaml` 中新增此字段，`pyproject.toml` 的 `dependencies` 已经是标准方案。

### 2.3 P0：`depends_on` —— 插件依赖拓扑加载

**现状**：`PluginDependency` 模型完整定义（含 `id` 和 `version`），`PluginManifest.depends_on` 字段就位。但 `PluginManager` 按 `_records` 插入顺序（即文件系统扫描顺序）加载，不做任何依赖排序。

**影响**：插件 A 依赖插件 B 先注册某个 provider type 或 tool，但 B 的目录名按字母序排在 A 后面时，A 的 `on_load` 可能找不到所需资源。当前靠运气和命名约定规避。

**实现要点**：

1. 在 `PluginManager` 中新增 `_resolve_load_order(plugins: list[str]) -> list[str]`，使用 Kahn 算法做拓扑排序。
2. 循环依赖检测：如果存在环，将所有循环参与者标记为 `ERROR`，并给出清晰的错误信息列出环中所有插件。
3. 缺失依赖检测：如果某个插件依赖的 `plugin_id` 不存在于已发现的插件列表中，拒绝加载并报错。
4. 版本约束匹配：使用 `packaging` 库（Python 标准库候选）做 `>=1.0,<2.0` 风格的版本约束校验。初始版本可以宽松匹配——只 warn 不 block。
5. 将 `load_all()` 和 `enable_all()` 中的遍历顺序从 `list(self._records)` 改为 `self._resolve_load_order(...)`。

**依赖标识方式**：`depends_on` 通过目标插件的 `id` 字段来标识依赖。例如，如果插件 A 需要在插件 B（其 `plugin.yaml` 中 `id: "builtin-commands"`）之后加载：

```yaml
# 插件 A 的 plugin.yaml
depends_on:
  - id: "builtin-commands"
    version: ">=0.1.0"
```

其中 `id` 必须精确匹配目标插件的 `plugin.yaml` 中声明的 `id` 字段。`version` 使用 PEP 440 约束语法，为空字符串时表示任意版本。

**涉及文件**：

- `nahida_bot/plugins/manager.py` —— 新增拓扑排序和依赖校验逻辑
- `nahida_bot/plugins/manifest.py` —— 可能需要补充 `PluginDependency` 的版本解析辅助方法
- `tests/test_plugin_manager.py` —— 新增依赖排序测试用例

### 2.4 P1：`nahida_bot_version` —— 版本兼容性门禁

**现状**：字段解析后从未与运行时 bot 版本比较。

**影响**：插件声明 `nahida_bot_version: ">=0.2.0"` 但运行在 `0.1.0` 上时，静默加载，可能在运行时因 API 不兼容而崩溃。

**实现要点**：

1. 从 `nahida_bot.__version__` 获取当前 bot 版本。
2. 在 `PluginManager.load()` 中，`PermissionChecker` 创建之前，解析并校验版本约束。
3. 使用 PEP 440 版本规范 + `packaging.specifiers.SpecifierSet` 或简化版手动解析。
4. 不匹配时的行为：默认 **warn + 继续加载**（兼容现有行为），可配置为 `strict` 模式拒绝加载。
5. `nahida_bot_version` 为空字符串时不检查（向后兼容当前所有插件）。

**涉及文件**：

- `nahida_bot/plugins/manager.py` —— `load()` 方法中新增版本检查
- `nahida_bot/__init__.py` —— 确认 `__version__` 存在
- `nahida_bot/plugins/manifest.py` —— 可选：新增 `check_version_compatibility()` 静态方法

### 2.5 P1：`capabilities` —— 工具声明校验与文档生成

**现状**：`Capabilities.tools` 和 `Capabilities.subscribes_to` 完全无人读取。builtin 插件在 yaml 中写了 13 个 tool 的描述，但与 `register_tool` 的实际注册没有任何关联。

**设计决策**：`capabilities` 不应该作为"替代注册"的机制（工具注册必须在代码中完成，因为需要 handler 函数）。它的正确用途是：

1. **静态声明**：让插件在加载前就能被检查"声称提供什么能力"。
2. **冲突检测**：`PluginManager.enable()` 时可以对比 manifest 声明的 tools 和 `ToolRegistry` 中已注册的 tools，提前发现命名冲突。
3. **WebUI 文档**：插件面板展示每个插件提供了哪些工具、监听了哪些事件。
4. **可选**：加载后做 reconciliation——实际注册的工具集 vs manifest 声明的工具集是否一致。

**实现要点**：

1. 在 `PluginManager.enable()` 成功后做 reconciliation：对比 `capabilities.tools` 中声明的 name 和通过 `self.api.register_tool` 实际注册的 tool name。
   - 声明了但未注册 → warning 日志。
   - 注册了但未声明 → info 日志（不强制，很多插件动态注册工具）。
2. 冲突检测：加载前检查 manifest 的 `capabilities.tools[].name` 是否与 `ToolRegistry` 中已有工具名冲突，提前报错而非等到 `register_tool` 时抛 `KeyError`。
3. 将 `capabilities` 数据暴露到 PluginManager 的公开 API，供 WebUI 插件面板消费。

**涉及文件**：

- `nahida_bot/plugins/manager.py` —— enable 后 reconciliation + 预检查
- `nahida_bot/plugins/registry.py` —— 可选新增 `has_tool(name) -> bool`
- WebUI 新增的插件 API（见 §3）

### 2.6 P2：权限系统补全

#### 2.6.1 修复 `send_message` 的权限检查

**问题**：`send_message(target, ...)` 将 chat address（如 `"group:12345"`）传给 `check_network_outbound()`，后者用 URL glob 匹配。对于声明了精确 URL 而非 `"*"` 的插件，发消息会直接 `PermissionDenied`。

**修复**：`send_message` 应改用 channel 级别的权限声明，或者在当前模型下直接移除错误的 `check_network_outbound` 调用。发消息走 channel service，不是网络请求。如果未来需要限制"哪些插件可以向哪些 chat 发消息"，应新增 `permissions.messaging` 维度。

#### 2.6.2 补全缺失的权限检查

| 新增检查 | 应贴在哪些 API 方法上 | 优先度 |
|---------|---------------------|:----:|
| `check_subprocess()` | 目前无对应 API 方法。等 `exec` 类 API 加入 BotAPI 后接入。 | P2 |
| `check_env_var(key)` | 等 `api.get_env(key)` 加入 BotAPI 后接入。 | P2 |
| `check_signal_handlers()` | 新增 `PermissionChecker.check_signal_handlers()` 方法。等需要时接入。 | P3 |
| `check_network_inbound()` | `register_channel()` 调用前检查 channel 插件是否声明了 inbound 权限。 | P1 |

#### 2.6.3 为关键注册方法加入权限门禁

当前 `register_tool`、`register_command`、`register_channel` 等方法无任何权限检查。但这些操作影响全局状态——任意插件都可以注册与核心命令同名的命令来覆盖行为。

**建议**：

- `register_command` 应至少校验插件声明了某种能力（从 `capabilities` 或新增 `permissions.agent` 维度）。
- `register_tool` 同上。
- `register_channel` 应校验 `permissions.network.inbound`。
- `subscribe`（某些高频事件如 `MessageReceived`）可考虑加入声明校验，防止插件静默监听所有消息。

这是一个需要权衡的设计决策：过严会降低插件开发体验，过松会留下隐患。建议先做 `register_channel` → `check_network_inbound` 这条最明确的链路。

### 2.7 P2：`description` —— 日志与 UI 展示

**现状**：`manifest.description` 全代码库零引用。

**改进**：

1. 在 `PluginManager.load()` 的日志中加入 description（截断到 80 字符）。
2. 在 WebUI 插件面板中作为插件的说明文字展示。
3. 在 `nahida bot plugins list` CLI 命令中展示。

### 2.8 未来探索：AstrBot 兼容层

AstrBot 是另一个 Python LLM bot 框架，有自己的插件 API（`astrbot.core.plugin`、`on_message` 装饰器、`Context` 对象等）。如果能在 nahida-bot 的 `BotAPI` 协议之上构建一个 AstrBot 兼容适配层，可以让现有的 AstrBot 插件以最小修改运行在 nahida-bot 上。

**可行性分析**：

| 概念 | nahida-bot | AstrBot | 兼容难度 |
|------|-----------|---------|:------:|
| 插件基类 | `Plugin(ABC)` + `on_load()` | `StarlettePlugin` + `run()` | 中——生命周期模型不同，需要适配器 |
| 消息接收 | `MessageReceived` 事件 | `on_message()` 装饰器 | 低——事件模型可映射 |
| 发送消息 | `api.send_message(target, msg)` | `context.send(msg)` | 低——语义相近 |
| 命令注册 | `api.register_command(name, handler)` | 装饰器或 `register_command` | 低——直接映射 |
| 工具注册 | `api.register_tool(name, desc, params, handler)` | 无直接等价物 | 高——AstrBot 没有 tool 概念 |
| 配置 | `plugin.yaml` + `config` | `config.yml` | 中——格式不同但概念相同 |
| 权限 | manifest `permissions` 声明式 | 无 | 高——AstrBot 无权限模型 |

**结论**：消息收发和命令注册层面兼容是可行的，但 AstrBot 没有 tool 系统和权限模型，这两个概念无法映射。建议将 AstrBot 兼容层作为一个**独立的适配器插件**来实现——它本身是一个 nahida-bot 插件，加载后提供 AstrBot API 的模拟层，让 AstrBot 插件在 nahida-bot 的 PluginManager 管理下运行。**此项不在当前路线图中，列为未来探索方向**。

---

## 3. WebUI 插件管理面板

### 3.1 目标

在 WebUI 中新增一个独立的 "Plugins" 页面，提供插件发现、状态监控、配置管理的可视化界面。

### 3.2 信息架构

页面结构：

```text
┌─ Plugins ──────────────────────────────────────────────┐
│ 搜索: [____________]  状态过滤: [全部▾]  阶段: [全部▾]  │
│                                                         │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 📦 builtin-commands                     enabled  ✅ │ │
│ │   Builtin Commands                                 │ │
│ │   Core commands, workspace tools, exec, web fetch  │ │
│ │   v0.1.0 | post-agent | 13 tools | 0 events        │ │
│ │   [Disable] [Reload] [Config]                      │ │
│ ├─────────────────────────────────────────────────────┤ │
│ │ 🔌 onebot                                enabled  ✅ │ │
│ │   OneBot Channel                                   │ │
│ │   QQ and other IM channels via OneBot v11 protocol │ │
│ │   v0.1.0 | post-agent | 1 tool | channel           │ │
│ │   [Disable] [Reload] [Config]                      │ │
│ ├─────────────────────────────────────────────────────┤ │
│ │ ...                                                 │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ 插件详情面板（点击展开）                                  │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Manifest  /  Tools  /  Commands  /  Events  /  Config│ │
│ └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 3.3 后端 API

需要新增以下端点：

```text
GET  /api/plugins                           # 列出所有插件及状态
GET  /api/plugins/{plugin_id}               # 单个插件详情
POST /api/plugins/{plugin_id}/enable        # 启用插件
POST /api/plugins/{plugin_id}/disable       # 禁用插件
POST /api/plugins/{plugin_id}/reload        # 热重载插件
GET  /api/plugins/{plugin_id}/config        # 获取插件配置
PUT  /api/plugins/{plugin_id}/config        # 更新插件配置
GET  /api/plugins/discovery                  # 触发重新扫描插件目录
```

#### `GET /api/plugins` 响应模型

```json
{
  "plugins": [
    {
      "id": "builtin-commands",
      "name": "Builtin Commands",
      "version": "0.1.0",
      "description": "Core commands, workspace tools, exec, web fetch, plan, cron, and agent orchestration",
      "state": "enabled",
      "load_phase": "post-agent",
      "error_message": "",
      "capabilities": {
        "tools": [
          {"name": "workspace_read", "description": "Read a text file from the active workspace"},
          {"name": "workspace_write", "description": "Write a text file to the active workspace"}
        ],
        "subscribes_to": []
      },
      "dependencies": [],
      "permissions_summary": {
        "network_outbound": ["*"],
        "filesystem_read": ["workspace"],
        "filesystem_write": ["workspace"],
        "memory_read": true,
        "memory_write": true,
        "subprocess": true
      },
      "tool_count": 13,
      "command_count": 12,
      "event_subscription_count": 0,
      "channel_count": 0
    }
  ],
  "summary": {
    "total": 5,
    "enabled": 4,
    "disabled": 0,
    "error": 1,
    "loaded": 0
  }
}
```

#### `GET /api/plugins/{plugin_id}` 响应

在列表响应基础上追加：

```json
{
  "registered_tools": ["workspace_read", "workspace_write", "exec", "web_fetch", "plan", ...],
  "registered_commands": ["/plan", "/memory", "/model", "/cron", "/stop", ...],
  "event_subscriptions": ["MessageReceived"],
  "channels": [],
  "config_schema": { ... },
  "config_current": { ... },
  "has_pyproject_toml": true,
  "python_dependencies": ["nahida-bot-sdk>=0.1.0", "pillow>=10.0"],
  "python_dependencies_satisfied": true
}
```

新增字段说明：

- `has_pyproject_toml`：插件目录中是否存在 `pyproject.toml`。
- `python_dependencies`：从 `pyproject.toml` `[project] dependencies` 解析的依赖列表（仅展示，不自动安装）。
- `python_dependencies_satisfied`：是否所有依赖已安装（用 `importlib.metadata` 校验），用于在 WebUI 中显示"缺失依赖"警告。

### 3.4 前端页面

**位置**：`webui/src/features/plugins/`

**主要组件**：

| 组件 | 功能 |
|------|------|
| `PluginsPage.vue` | 页面顶层：搜索、过滤、插件卡片列表 |
| `PluginCard.vue` | 单个插件卡片：名称、状态、版本、capabilities 摘要、操作按钮 |
| `PluginDetailPanel.vue` | 侧边或展开面板：Tab 切换 manifest / tools / commands / events / config |
| `PluginConfigTab.vue` | 复用现有 config schema 表单组件，渲染插件配置 |
| `PluginToolList.vue` | 展示插件注册的工具列表（名称 + 描述） |
| `PluginEventList.vue` | 展示插件订阅的事件列表 |

**交互**：

- 卡片点击展开详情面板。
- Enable / Disable / Reload 按钮带二次确认（Disable 和 Reload 是破坏性操作）。
- Config tab 如果插件有 `config_schema`，用 schema-driven 表单渲染；没有则退化为 YAML 编辑器。
- 搜索框支持按插件 id、name、description 过滤。
- 状态过滤：All / Enabled / Disabled / Error。

### 3.5 实施阶段

#### Phase A：后端 API（与 §2 的 P0/P1 并行）

1. 在 `PluginManager` 上新增公开查询方法（如需要）：
   - `get_plugin_summary(plugin_id) -> dict`
   - `get_plugins_summary() -> dict`
2. 新增 `nahida_bot/gateway/routes/plugins.py`
3. 注册到 Gateway router
4. 新增认证/授权中间件挂载

#### Phase B：前端页面

1. 新增 `webui/src/features/plugins/` 目录和组件
2. 新增 `webui/src/api/plugins.ts` queries
3. 注册路由 `/plugins`
4. 注册到导航栏

#### Phase C：配置编辑集成

1. 插件 Config tab 集成 schema-driven 表单（复用配置页的 `_walk_json_schema` 逻辑）
2. 保存插件配置（直接通过现有 `PATCH /api/config/current` 或专用 `PUT /api/plugins/{id}/config`）

---

## 4. 实施路线图

### Phase A：nahida-bot-sdk 独立包 + 测试控制台

- [x] 创建 `nahida-bot-sdk/` 目录，初始化 `pyproject.toml`（依赖仅 `pydantic>=2.0`）
- [x] 将 `Plugin`、`BotAPI`、`PluginManifest`、消息类型、事件类型从 `nahida_bot/plugins/` 迁移到 SDK
- [x] 实现 `MockBotAPI`（含假 workspace、假 memory、假 session、工具/命令注册记录）
- [x] 实现 `python -m nahida_bot_sdk.testing.console` 插件测试控制台
- [x] nahida-bot 改为 `from nahida_bot_sdk import ...`，删除 `nahida_bot/plugins/` 中的重复类型定义
- [x] 现有 5 个插件的 import 改为依赖 `nahida-bot-sdk`（通过墊片自动兼容，零改动）
- [x] **验收测试**：在一个完全隔离的目录中编写插件（只依赖 nahida-bot-sdk），注册 command + tool，通过控制台验证功能

### Phase B：插件依赖拓扑加载 `depends_on`

- [ ] `manager.py`: 实现 `_resolve_load_order()` — Kahn 算法拓扑排序
- [ ] `manager.py`: 循环依赖检测 → 标记所有环参与者为 ERROR
- [ ] `manager.py`: 缺失依赖检测 → `plugin_id` 不存在时报错
- [ ] `manager.py`: 版本约束基础匹配（`>=1.0,<2.0` 语法，warn 不 block）
- [ ] `manager.py`: `load_all()` / `enable_all()` 改用拓扑顺序
- [ ] `loader.py`: `ImportError` 时解析缺失包名，给出友好错误信息（外部依赖 §2.7 短期方案）
- [ ] 测试：依赖排序、循环检测、缺失依赖、版本约束

### Phase C：版本兼容性门禁 + capabilities 校验

- [ ] `manager.py`: `nahida_bot_version` 校验（PEP 440，warn 模式，空字符串放行）
- [ ] `manager.py`: capabilities reconciliation — enable 后对比声明 vs 实际注册的工具
- [ ] `manager.py`: 工具名冲突预检查 — 加载前用 manifest 的 capabilities.tools 做冲突检测
- [ ] `manager.py`: 公开 `get_plugin_summary()` / `get_plugins_summary()` 供 WebUI 消费
- [ ] `registry.py`: 可选新增 `has_tool(name) -> bool`
- [ ] 测试：版本校验、reconciliation、冲突预检查

### Phase D：权限系统补全

- [ ] `api_bridge.py`: 修复 `send_message()` 中错误的 `check_network_outbound(target)` 调用
- [ ] `api_bridge.py`: `register_channel()` 加入 `check_network_inbound()` 检查
- [ ] `permissions.py`: 新增 `check_signal_handlers()` 方法（占位，等后续接入）
- [ ] 测试：权限检查覆盖新增路径

### Phase E：WebUI 插件管理面板

- [ ] 后端：新增 `nahida_bot/gateway/routes/plugins.py` + 7 个 API 端点
- [ ] 后端：`PluginManager` 暴露插件摘要数据（name、state、capabilities、tool/command count 等）
- [ ] 前端：新增 `webui/src/features/plugins/` 目录和 6 个组件
- [ ] 前端：新增 `webui/src/api/plugins.ts` API queries
- [ ] 前端：注册 `/plugins` 路由 + 导航栏入口
- [ ] 前端：插件配置 Tab 集成 schema-driven 表单
- [ ] 前端：Enable / Disable / Reload 操作（带二次确认）

### Phase F：锦上添花

- [ ] `manager.py`: load 日志中输出 `manifest.description`
- [ ] WebUI 插件面板展示 description
- [ ] `loader.py`: import 失败时检测 pyproject.toml 是否存在，存在则解析 `dependencies` 并提示 `uv pip install ./plugins/<name>`
- [ ] WebUI 插件详情面板展示 pyproject.toml 中的依赖列表
- [ ] CLI: `nahida plugins list` 命令增强
- [ ] 文档：编写插件开发指南（含 pyproject.toml + plugin.yaml 双文件模板）
- [ ] `sdk_version` / `nahida_bot_version` 降级为辅助字段，pyproject.toml 依赖声明是主版本门禁
- [ ] 未来探索：AstrBot 兼容层原型验证（§2.9）

### 依赖关系

```
Phase A (SDK 包) ───────── 基础依赖，必须先做
    │
    ├──> Phase B (depends_on)
    ├──> Phase C (capabilities) ──┐
    ├──> Phase D (permissions) ──┼──> Phase E (WebUI 面板)
    └──> (插件开发体验改进)      │
                                 │
Phase F (锦上添花) ──────────────┘ 最后做
```

---

## 5. 设计约束

1. **向后兼容**：所有新增校验默认 warn 不 block。现有 5 个插件的 `plugin.yaml` 不需要修改即可通过所有新检查。
2. **不改变插件编写方式**：`capabilities` reconciliation 是 observation 而非 enforcement——声明了但没注册只打 warning，注册了但没声明只打 info。
3. **WebUI 插件面板遵循现有 WebUI 设计规范**：Vue 3 + shadcn-vue/Reka UI，API-first，所有 mutation 走 REST。
4. **CLI 不重复造轮子**：如果增加 `nahida plugins list` 命令，它应调用 `PluginManager` 的公开方法而非直接读文件系统。
