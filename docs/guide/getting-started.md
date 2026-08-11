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
nahida-bot version                # 显示版本信息
nahida-bot bootstrap              # 交互式生成最小 config.yaml + .env
nahida-bot start [--debug]        # 启动应用（含 Gateway + WebUI）
nahida-bot doctor                 # 诊断检查（配置/数据库/就绪度）
nahida-bot config schema          # 显示配置 schema（含插件 schema）
nahida-bot config validate        # 校验配置文件
nahida-bot auth login codex       # ChatGPT Codex OAuth 登录
nahida-bot auth login deepseek-main  # 保存普通 provider API key
nahida-bot auth list              # 查看 provider 认证状态
nahida-bot auth logout codex      # 删除 auth CLI 保存的凭据
nahida-bot webui hash-password    # 生成 WebUI 管理员密码哈希
nahida-bot tokens                 # Token 用量统计
```

`nahida-bot auth` 专门管理 provider 凭据：Codex 使用设备码 OAuth，其他
provider 会隐藏输入 API key 并保存到 SQLite。数据库中的 key 优先于
`config.yaml` / `.env`；普通 API key 更新后需要重启服务。WebUI 密码哈希使用
独立的 `nahida-bot webui hash-password`，不属于 provider auth。

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
