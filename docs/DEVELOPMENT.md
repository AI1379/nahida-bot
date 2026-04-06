# Nahida Bot 代码风格准则

> 本文档定义了项目的代码风格、测试规范和类型检查要求，旨在提高代码的可维护性和质量。

## 目录

- [1. 工具链配置](#1-工具链配置)
- [2. 单元测试规范](#2-单元测试规范)
- [3. 类型检查规范](#3-类型检查规范)
- [4. 代码风格规范](#4-代码风格规范)
- [5. 项目结构规范](#5-项目结构规范)
- [6. 文档规范](#6-文档规范)
- [7. Git 规范](#7-git-规范)

---

## 1. 工具链配置

### 1.1 依赖管理

使用 `uv` 或 `pip` 配合 `pyproject.toml` 管理依赖：

```toml
[project]
name = "nahida-bot"
version = "0.1.0"
requires-python = ">=3.12"

[dependency-groups]
dev = [
    # 测试
    "pytest>=8.4.1",
    "pytest-asyncio>=1.1.0",
    "pytest-cov>=6.2.1",

    # 类型检查
    "pyright>=1.1.0",

    # Linting & Formatting
    "ruff>=0.15.0",

    # Pre-commit
    "pre-commit>=4.0.0",
]
```

### 1.2 Ruff 配置

在 `pyproject.toml` 中添加：

```toml
[tool.ruff]
target-version = "py312"
line-length = 88
exclude = ["tests/fixtures", "build", "dist"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
```

### 1.3 Pyright 配置

```toml
[tool.pyright]
typeCheckingMode = "standard"
pythonVersion = "3.12"
reportMissingTypeStubs = false
reportMissingImports = true
reportUnusedImport = "warning"
reportUnusedVariable = "warning"
reportConstantRedefinition = "error"
reportOptionalSubscript = "error"
reportOptionalMemberAccess = "error"
reportOptionalCall = "error"
reportOptionalIterable = "error"
reportOptionalContextManager = "error"
reportOptionalOperand = "error"
reportUntypedFunctionDecorator = "warning"
reportUntypedClassDecorator = "warning"
reportUntypedBaseClass = "error"
reportUntypedNamedTuple = "error"
reportPrivateUsage = "warning"
reportOverlappingOverload = "error"
include = ["nahida_bot", "tests"]
exclude = ["**/node_modules", "**/__pycache__", "build", "dist"]
```

### 1.4 Pytest 配置

```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
addopts = [
    "-ra",
    "-q",
    "--strict-markers",
    "--strict-config",
    "--tb=short",
]
markers = [
    "unit: 单元测试",
    "integration: 集成测试",
    "slow: 慢速测试",
    "network: 需要网络连接的测试",
]
filterwarnings = [
    "error",
    "ignore::DeprecationWarning",
    "ignore::PendingDeprecationWarning",
]
```

---

## 2. 单元测试规范

### 2.1 测试原则

#### AAA 模式

每个测试应遵循 **Arrange-Act-Assert** 模式：

```python
def test_parse_message():
    # Arrange - 准备测试数据
    raw_message = '{"content": "hello", "user_id": "123"}'
    parser = MessageParser()

    # Act - 执行被测试的操作
    result = parser.parse(raw_message)

    # Assert - 验证结果
    assert result.content == "hello"
    assert result.user_id == "123"
```

#### FIRST 原则

- **F**ast: 测试应该快速执行
- **I**ndependent: 测试之间不应有依赖
- **R**epeatable: 在任何环境下都可重复
- **S**elf-validating: 自动判定通过/失败
- **T**imely: 与代码同步编写

### 2.2 测试命名

```python
# 格式: test_<被测功能>_<场景>_<预期结果>
def test_send_message_when_connected_returns_success():
    ...

def test_send_message_when_disconnected_raises_connection_error():
    ...

# 或使用中文描述（团队偏好）
def test_发送消息_连接正常_返回成功():
    ...
```

### 2.3 测试组织

```text
tests/
├── conftest.py           # 共享 fixtures
├── fixtures/             # 测试数据和 fixtures
│   ├── __init__.py
│   ├── helpers.py        # 辅助函数
│   └── data/             # 测试数据文件
├── unit/                 # 单元测试
│   ├── test_parser.py
│   └── test_handler.py
├── integration/          # 集成测试
│   └── test_api.py
└── e2e/                  # 端到端测试
    └── test_bot_flow.py
```

### 2.4 Fixture 使用

```python
# conftest.py
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """创建临时测试目录。"""
    return tmp_path

@pytest.fixture
def mock_config(temp_dir: Path) -> dict:
    """创建测试配置。"""
    return {
        "data_dir": str(temp_dir),
        "log_level": "DEBUG",
    }

@pytest.fixture
def mock_http_client():
    """创建模拟的 HTTP 客户端。"""
    client = MagicMock()
    client.get = AsyncMock(return_value={"status": "ok"})
    return client
```

### 2.5 异步测试

```python
import pytest

@pytest.mark.asyncio
async def test_async_operation():
    """测试异步操作。"""
    result = await some_async_function()
    assert result is not None

@pytest.mark.asyncio
async def test_with_async_fixture(mock_async_client):
    """使用异步 fixture 的测试。"""
    await mock_async_client.connect()
    assert mock_async_client.is_connected
```

### 2.6 参数化测试

```python
import pytest

@pytest.mark.parametrize("input_value,expected", [
    ("hello", "HELLO"),
    ("WORLD", "WORLD"),
    ("", ""),
    ("  spaces  ", "  SPACES  "),
])
def test_to_uppercase(input_value: str, expected: str) -> None:
    """测试大写转换。"""
    assert input_value.upper() == expected

@pytest.mark.parametrize("user_type", ["admin", "user", "guest"])
def test_user_permissions(user_type: str) -> None:
    """测试不同用户类型的权限。"""
    permissions = get_permissions(user_type)
    assert permissions is not None
```

### 2.7 测试覆盖率

#### 目标

- **总体覆盖率**: ≥ 80%
- **核心模块**: ≥ 90%
- **新增代码**: ≥ 95%

#### 运行覆盖率报告

```bash
# 生成覆盖率报告
pytest --cov=nahida_bot --cov-report=html --cov-report=term

# 只检查新增代码的覆盖率
pytest --cov=nahida_bot --cov-fail-under=80
```

### 2.8 Mock 最佳实践

```python
from unittest.mock import AsyncMock, MagicMock, patch

def test_external_api_call():
    """使用 patch 模拟外部依赖。"""
    with patch("module.external_api") as mock_api:
        mock_api.return_value = {"status": "ok"}

        result = call_external_api()

        mock_api.assert_called_once()
        assert result["status"] == "ok"

@pytest.fixture
def mock_database():
    """创建模拟数据库。"""
    db = MagicMock()
    db.query = AsyncMock(return_value=[])
    db.insert = AsyncMock(return_value=1)
    return db
```

### 2.9 异常测试

```python
import pytest

def test_invalid_input_raises_error():
    """测试无效输入抛出异常。"""
    with pytest.raises(ValueError, match="Invalid input"):
        parse_input("invalid")

def test_custom_exception():
    """测试自定义异常。"""
    with pytest.raises(BotConnectionError) as exc_info:
        connect_to_server("invalid_url")

    assert "connection failed" in str(exc_info.value).lower()
```

---

## 3. 类型检查规范

### 3.1 基础类型注解

```python
from collections.abc import Callable, Sequence
from typing import TypeAlias

# 基本类型
def greet(name: str) -> str:
    return f"Hello, {name}"

# 容器类型
def process_items(items: list[str]) -> dict[str, int]:
    return {item: len(item) for item in items}

# 可选类型
from typing import TypeVar

T = TypeVar("T")

def first_or_none(items: list[T]) -> T | None:
    return items[0] if items else None
```

### 3.2 自定义类型

```python
from typing import NewType, TypeAlias
from typing_extensions import TypedDict

# NewType - 创建语义化类型
UserId = NewType("UserId", str)
MessageId = NewType("MessageId", int)

def get_user(user_id: UserId) -> User:
    ...

# TypeAlias - 复杂类型别名
JsonDict: TypeAlias = dict[str, object]
HandlerFunc: TypeAlias = Callable[[Message], Awaitable[Response]]

# TypedDict - 字典结构
class UserInfo(TypedDict):
    id: str
    name: str
    is_admin: bool
```

### 3.3 泛型

```python
from typing import Generic, TypeVar

T = TypeVar("T")

class Repository(Generic[T]):
    """通用仓库基类。"""

    def __init__(self, items: list[T] | None = None) -> None:
        self._items: list[T] = items or []

    def add(self, item: T) -> None:
        self._items.append(item)

    def get_all(self) -> list[T]:
        return self._items.copy()
```

### 3.4 Protocol 和 ABC

```python
from typing import Protocol
from abc import ABC, abstractmethod

# Protocol - 结构化子类型
class MessageHandler(Protocol):
    """消息处理器协议。"""

    async def handle(self, message: Message) -> Response:
        ...

# ABC - 抽象基类
class BasePlatform(ABC):
    """平台适配器基类。"""

    @abstractmethod
    async def send_message(self, target: str, content: str) -> bool:
        """发送消息。"""
        ...

    @abstractmethod
    async def receive_messages(self) -> AsyncIterator[Message]:
        """接收消息流。"""
        ...
```

### 3.5 类型守卫

```python
from typing import TypeGuard

def is_valid_message(data: object) -> TypeGuard[dict[str, object]]:
    """检查数据是否为有效的消息格式。"""
    return (
        isinstance(data, dict)
        and "content" in data
        and isinstance(data["content"], str)
    )

def process(data: object) -> None:
    if is_valid_message(data):
        # 这里 data 被推断为 dict[str, object]
        print(data["content"])
```

### 3.6 TYPE_CHECKING 块

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from nahida_bot.core import Bot

def create_bot() -> Bot:  # 避免循环导入
    from nahida_bot.core import Bot
    return Bot()
```

---

## 4. 代码风格规范

### 4.1 命名约定

| 类型 | 风格 | 示例 |
|------|------|------|
| 模块 | snake_case | `message_parser.py` |
| 类 | PascalCase | `MessageHandler` |
| 函数 | snake_case | `parse_message()` |
| 变量 | snake_case | `user_count` |
| 常量 | UPPER_SNAKE_CASE | `MAX_RETRY_COUNT` |
| 私有属性 | _leading_underscore | `_internal_state` |
| 保护属性 | _leading_underscore | `_cache` |

### 4.2 导入顺序

```python
# 1. 标准库
import asyncio
import json
from pathlib import Path
from typing import TypeAlias

# 2. 第三方库
import httpx
from pydantic import BaseModel

# 3. 本地模块
from nahida_bot.core import Bot
from nahida_bot.utils import logger
```

### 4.3 文档字符串

使用 Google 风格：

```python
def send_message(
    target: str,
    content: str,
    *,
    retry: int = 3,
) -> bool:
    """发送消息到指定目标。

    Args:
        target: 目标标识符（用户ID或群组ID）。
        content: 消息内容。
        retry: 发送失败时的重试次数。

    Returns:
        发送成功返回 True，否则返回 False。

    Raises:
        ConnectionError: 无法连接到服务器时抛出。
        ValueError: content 为空时抛出。

    Example:
        >>> success = send_message("user_123", "Hello!")
        >>> print(success)
        True
    """
    ...
```

### 4.4 错误处理

```python
# 定义自定义异常
class BotError(Exception):
    """机器人基础异常。"""
    pass

class ConnectionError(BotError):
    """连接错误。"""
    pass

class MessageParseError(BotError):
    """消息解析错误。"""
    pass

# 使用异常链
async def process_message(raw: str) -> Message:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MessageParseError(f"Invalid JSON: {raw}") from e

    return Message.from_dict(data)
```

### 4.5 日志规范

```python
from loguru import logger

# 使用结构化日志
logger.info(
    "Message processed",
    extra={
        "user_id": user_id,
        "message_type": msg_type,
        "duration_ms": duration,
    }
)

# 错误日志包含上下文
try:
    await risky_operation()
except Exception as e:
    logger.exception(
        "Operation failed",
        extra={"operation": "risky_operation", "error_type": type(e).__name__}
    )
```

---

## 5. 项目结构规范

```
nahida-bot/
├── nahida_bot/              # 主包
│   ├── __init__.py
│   ├── __main__.py          # 入口点
│   ├── core/                # 核心功能
│   │   ├── __init__.py
│   │   ├── bot.py
│   │   ├── config.py
│   │   └── events.py
│   ├── adapters/            # 平台适配器
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── qq.py
│   ├── handlers/            # 消息处理器
│   │   ├── __init__.py
│   │   └── message.py
│   ├── utils/               # 工具函数
│   │   ├── __init__.py
│   │   └── logger.py
│   └── models/              # 数据模型
│       ├── __init__.py
│       └── message.py
├── tests/                   # 测试
│   ├── conftest.py
│   ├── unit/
│   └── integration/
├── docs/                    # 文档
├── pyproject.toml           # 项目配置
├── README.md
└── LICENSE
```

---

## 6. 文档规范

### 6.1 README 结构

```markdown
# Project Name

简短描述

## 功能特性

- 功能 1
- 功能 2

## 快速开始

### 安装

\`\`\`bash
pip install project-name
\`\`\`

### 使用

\`\`\`python
from project import main
main()
\`\`\`

## 配置

配置说明...

## 开发

开发指南链接...

## 许可证

MIT License
```

### 6.2 API 文档

所有公共 API 应有文档字符串，并使用 Type Hints。

---

## 7. Git 规范

### 7.1 Commit 消息格式

使用 Conventional Commits：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**类型：**

- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `style`: 代码格式（不影响逻辑）
- `refactor`: 重构
- `test`: 测试相关
- `chore`: 构建/工具变更

**示例：**

```
feat(adapter): add Telegram platform adapter

- Implement message sending
- Add webhook handler
- Support inline keyboards

Closes #123
```

### 7.2 分支策略

- `main`: 稳定发布版本
- `develop`: 开发分支
- `feature/*`: 功能分支
- `fix/*`: 修复分支
- `release/*`: 发布准备分支

### 7.3 Pre-commit Hooks

创建 `.pre-commit-config.yaml`：

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files

  - repo: local
    hooks:
      - id: pyright
        name: pyright
        entry: pyright
        language: system
        types: [python]
        pass_filenames: false

      - id: pytest
        name: pytest
        entry: pytest -x
        language: system
        types: [python]
        pass_filenames: false
```

---

## 检查清单

### 提交前检查

- [ ] 代码通过 `ruff check` 检查
- [ ] 代码通过 `ruff format` 格式化
- [ ] 类型检查 `pyright` 无错误
- [ ] 所有测试通过 `pytest`
- [ ] 测试覆盖率达标
- [ ] 文档已更新（如需要）

### CI/CD 检查

```yaml
# .github/workflows/ci.yml
name: CI

on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv run ruff check
      - run: uv run ruff format --check

  type-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv run pyright

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv run pytest --cov=nahida_bot --cov-fail-under=80
```

---

## 参考资料

- [PEP 8 -- Style Guide for Python Code](https://peps.python.org/pep-0008/)
- [PEP 484 -- Type Hints](https://peps.python.org/pep-0484/)
- [Pyright Documentation](https://github.com/microsoft/pyright)
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [Pytest Documentation](https://docs.pytest.org/)
- [Conventional Commits](https://www.conventionalcommits.org/)
