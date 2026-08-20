# 快速开始

## 环境要求

- **Python 3.12+**
- [astral-uv](https://docs.astral.sh/uv/) — Python 包管理
- [pnpm](https://pnpm.io/) — WebUI 前端构建
- 一个 LLM API Key（OpenAI Compatible / DeepSeek / Claude / GLM 等）

## 安装

```bash
git clone https://github.com/AI1379/nahida-bot.git
cd nahida-bot

# 安装 Python 后端依赖
uv sync

# 如需知识库导入 PDF、Word、PowerPoint、Excel 等文档
uv sync --extra document-import

# 如需 Telegram Channel，安装可选依赖
uv sync --group telegram

# WebUI 前端（可选，但推荐）
pnpm install          # 安装 workspace 依赖
pnpm webui:build      # 输出到 webui/dist/，Gateway 启动时自动挂载
```

### 可选：完整知识库文档导入

默认安装支持 UTF-8 编码的纯文本和 Markdown 文件。安装
`document-import` extra 后，知识库 WebUI 还可以导入 PDF、DOCX、
PPTX、XLS/XLSX、HTML、CSV、JSON、XML、EPUB、Outlook MSG
和 Jupyter Notebook：

```bash
uv sync --extra document-import
```

这些格式由
[Microsoft MarkItDown](https://github.com/microsoft/markitdown)
转换为 Markdown，再交给知识库分块和索引。旧版 `.doc` 文件需要先转换
为 `.docx`。WebUI 可以一次选择或拖入最多 20 个文件，单个文件上限为
25 MiB。批量导入采用逐文件结果，部分文件失败时，其余成功文件仍会保留。
扫描 PDF 和以图片为主的文档可能提取不到正文；当前默认集成不启用第三方
OCR 插件或付费云端文档分析服务。

## 配置（推荐：交互式 bootstrap）

最快的方式是运行引导命令，它会以问答方式生成最小可用的 `config.yaml` 和
`.env`：

```bash
uv run nahida-bot bootstrap
```

向导会依次询问：LLM Provider（DeepSeek / SiliconFlow / OpenAI / Claude /
GLM / 通用 OpenAI 兼容）的 API Key、base_url、默认模型，以及要接入的消息
Channel（Telegram / Milky QQ / OneBot，可跳过或选多个）。密钥写入 `.env`，
其余写入 `config.yaml`。

- **已有配置**：再次运行会进入补充模式，`--fix-missing` 保证只补缺不覆盖。
- **脚本/Docker**：`--non-interactive` 配合环境变量静默生成，例如：
  ```bash
  NAHIDA_BOOTSTRAP_PROVIDER=siliconflow \
  NAHIDA_BOOTSTRAP_CHANNELS=telegram \
  uv run nahida-bot bootstrap --non-interactive
  ```

> bootstrap 生成的只是「最小可用」配置。完整能力（记忆检索、知识库向量、
> 定时任务、附属进程监管等）请参考 [配置参考](./configuration.md)。

### 配置文件自动发现

`start` / `doctor` / `config` 等命令按以下顺序解析配置文件，无需手动指定：

| 文件 | 解析顺序 |
| ---- | ---- |
| `config.yaml` | `--config-yaml` / `-c` 参数 > `$NAHIDA_CONFIG` > `./config.yaml` |
| `.env` | `--env` 参数 > `$ENV_PATH` > `./.env` |

配置支持 `${VAR}` 和 `${VAR:default}` 环境变量插值。

## 启动

```bash
uv run nahida-bot doctor    # 部署前体检（可选但推荐）
uv run nahida-bot start
```

`start` 会在启动前做就绪检查；若没有任何可用的 provider，会打印醒目提示并
建议运行 `bootstrap`，但不会阻止启动（Gateway-only 部署仍可工作）。启动后
可通过 WebUI 访问 `http://127.0.0.1:6185` 管理面板。

## CLI 命令

```bash
nahida-bot version                     # 显示版本信息
nahida-bot bootstrap                   # 交互式生成最小 config.yaml + .env
nahida-bot start [--debug]             # 启动应用（含 Gateway + WebUI）
nahida-bot doctor                      # 诊断检查（配置/数据库/就绪度）
nahida-bot config schema               # 显示配置 schema（含插件 schema）
nahida-bot config validate             # 校验配置文件
nahida-bot auth login codex            # ChatGPT Codex OAuth 登录
nahida-bot auth login deepseek-main    # 保存普通 provider API key
nahida-bot auth list                   # 查看 provider 认证状态
nahida-bot auth logout codex           # 删除 auth CLI 保存的凭据
nahida-bot webui hash-password         # 生成 WebUI 管理员密码哈希
nahida-bot tokens stats                # Token 用量统计（默认最近 7 天）
nahida-bot tokens list                 # 最近的 token 用量事件
nahida-bot tokens providers            # 按 provider 汇总用量
nahida-bot tokens clear                # 清空 token 用量历史
```

### start / doctor / bootstrap 通用选项

`start`、`doctor` 和 `bootstrap` 都支持以下与配置文件发现相关的选项：

| 选项 | 说明 |
| ---- | ---- |
| `--config-yaml` / `--config` / `-c` | 指定 YAML 配置文件路径 |
| `--env` | 指定 `.env` 文件路径 |

`start` 的其它选项：

| 选项 | 说明 |
| ---- | ---- |
| `--debug` / `--no-debug` | 调试模式，未显式设置 `log_level` 时强制为 `DEBUG` |
| `--log-file` | 覆盖 `log_file` |
| `--log-file-level` | 覆盖 `log_file_level` |
| `--log-file-max-bytes` | 日志轮转大小（字节），`0` 关闭轮转 |
| `--log-file-backup-count` | 轮转保留的备份文件数 |
| `--skip-preflight` | 跳过启动前的就绪检查 |

默认情况下 `start` 会先做就绪检查：发现阻塞级别（`error`）的问题会打印
并直接退出（退出码 1），仅警告（`warning`）则继续启动。

`doctor` 会依次检查：Python >= 3.12、uv 是否安装、`config.yaml` 是否存在、
`.env` 是否存在、配置能否加载、配置校验（含 SQLite 中已保存的 auth 凭据）、
就绪度（至少一个可用 provider）、数据库路径可写、WebUI 前端是否已构建。
任一检查失败时退出码为 1。

`bootstrap` 在非交互模式下额外支持：

| 环境变量 | 说明 |
| ---- | ---- |
| `NAHIDA_BOOTSTRAP_PROVIDER` | 要配置的 provider 类型（如 `siliconflow`） |
| `NAHIDA_BOOTSTRAP_PROVIDER_ID` | provider ID，默认 `main` |
| `NAHIDA_BOOTSTRAP_CHANNELS` | 逗号分隔的 channel 列表（如 `telegram`） |

非交互模式下，`TELEGRAM_BOT_TOKEN`、`MILKY_ACCESS_TOKEN`、
`ONEBOT_ACCESS_TOKEN` 以及 provider 的 key 环境变量（如
`DEEPSEEK_LLM_API_KEY`）会从当前进程环境自动读取并写入 `.env`。bootstrap
成功结束后会打印一张能力清单，按 `primary` / `vision` / `embedding` /
`memory` 标签映射展示对话、图片理解、记忆向量检索、记忆整理等能力是否就绪。

### auth / config / tokens 的选项

- `auth` 下的 `login`、`logout`、`list` 以及 `config schema`、`config validate`
  均接受 `--config-yaml` / `--config` / `-c` 选项；`nahida-bot auth ls` 是
  `auth list` 的别名。
- `config schema` 还支持：`--section/-s <name>`（只输出某个顶层块）、
  `--format/-f table|json`（输出格式，默认 `table`）、`--providers`
  （展开 provider 条目字段）、`--plugins/--no-plugins`（是否包含插件配置
  条目，默认包含）。
- `config validate` 的检查项：`default_provider` 可解析、model spec 可解析、
  provider 凭据齐全（配置/环境变量或 SQLite auth 存储）、sqlite-vec 依赖与
  维度、启用 fallback 模式时的多模态 fallback 模型。
- `tokens` 组必须带子命令：`stats [--provider/-p <id>] [--days/-d <n>]`、
  `list [--limit/-n <n>] [--provider/-p <id>]`、`providers`、
  `clear [--force/-f]`。它们直接读取 SQLite，不要求 bot 正在运行。

`nahida-bot auth` 专门管理 provider 凭据：Codex 使用设备码 OAuth，其他
provider 会隐藏输入 API key 并保存到 SQLite。数据库中的 key 优先于
`config.yaml` / `.env`；普通 API key 更新后需要重启服务。WebUI 密码哈希使用
独立的 `nahida-bot webui hash-password`，不属于 provider auth。

`auth login <id>` 遇到配置里还不存在的 provider 时会进入交互式配置：先做
拼写纠错（比如 `deepseek-mian` 会提示是否指 `deepseek-main`），确认新建后
依次选择 provider 类型、base_url 和默认模型，随后把最小条目**保注释地**写回
配置文件（会留 `.bak.<时间戳>` 备份），再无缝进入凭据步骤。`codex` 这类
id 本身就是已知类型时会跳过选单直奔 OAuth，且无需任何 api_key。非交互
终端下则降级为打印可手动粘贴的 YAML 片段。

## 手动配置（可选）

如果你不想用 bootstrap，也可以直接编辑 `config.yaml`。最小示例如下：

```yaml
# config.yaml
app_name: "Nahida Bot"
log_level: "INFO"

providers:
  deepseek-main:
    type: deepseek
    api_key: "${DEEPSEEK_LLM_API_KEY:}"
    base_url: "${DEEPSEEK_LLM_BASE_URL:https://api.deepseek.com}"
    stream_responses: true
    models:
      - name: "deepseek-v4-pro"
        tags: [primary]

default_provider: deepseek-main

telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN:}"
```

完整配置项请参阅 [配置参考](./configuration.md)。

## 下一步

- [配置参考](./configuration.md) — 完整的 YAML 配置说明
- [架构总览](../architecture/) — 了解 Nahida Bot 的分层架构设计
- [开发规范](./development.md) — 代码风格、测试、类型检查规范
- [路线图](../ROADMAP) — 项目阶段规划与验收清单
