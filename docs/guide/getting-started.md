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
pnpm webui:build    # 输出到 webui/dist/，Gateway 启动时自动挂载
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

## 最小配置

复制 `config.yaml` 并填入你的 API 信息：

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

  siliconflow:
    type: "openai-compatible"
    api_key: "${SILICONFLOW_LLM_API_KEY:}"
    base_url: "${SILICONFLOW_LLM_BASE_URL:https://api.siliconflow.cn/v1}"
    stream_responses: true
    models:
      - name: "Qwen/Qwen3.6-35B-A3B"
        tags: [vision]
        capabilities:
          image_input: true

default_provider: deepseek-main

telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN:}"
```

配置支持 `${VAR}` 和 `${VAR:default}` 环境变量插值，可选 `.env` 文件加载。完整配置参考请参阅 [配置参考](./configuration.md)。

## 启动

```bash
uv run nahida-bot start
```

启动后可以通过 WebUI 访问 `http://127.0.0.1:6185` 管理面板。

## CLI 命令

```bash
nahida-bot version                # 显示版本信息
nahida-bot start [--debug]        # 启动应用（含 Gateway + WebUI）
nahida-bot config                 # 显示当前配置
nahida-bot config schema          # 显示配置 schema（含插件 schema）
nahida-bot config validate        # 校验配置文件
nahida-bot doctor                 # 运行诊断检查
nahida-bot gateway                # 仅启动 Gateway（WebAPI + WebUI）
```

## 下一步

- [配置参考](./configuration.md) — 完整的 YAML 配置说明
- [架构总览](../architecture/) — 了解 Nahida Bot 的分层架构设计
- [开发规范](./development.md) — 代码风格、测试、类型检查规范
- [路线图](../ROADMAP) — 项目阶段规划与验收清单
