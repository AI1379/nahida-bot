"""
Nahida Bot 测试配置

提供共享的 pytest fixtures 和测试工具。
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    pass

# 将项目根目录添加到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent


# 设置测试环境变量
os.environ.setdefault("TESTING", "true")


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE entries from a dotenv-like file if present."""
    if not path.exists() or not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


# 可选加载测试环境配置，便于本地 live 集成测试。
_load_env_file(PROJECT_ROOT / ".env")
_load_env_file(PROJECT_ROOT / ".env.test")


# ============================================================
# 测试收集和排序
# ============================================================


def pytest_collection_modifyitems(session, config, items):  # noqa: ARG001
    """重新排序测试：单元测试优先，集成测试在后。"""
    unit_tests = []
    integration_tests = []

    for item in items:
        item_path = Path(str(item.path))
        is_integration = "integration" in item_path.parts

        if is_integration:
            if item.get_closest_marker("integration") is None:
                item.add_marker(pytest.mark.integration)
            integration_tests.append(item)
        else:
            if item.get_closest_marker("unit") is None:
                item.add_marker(pytest.mark.unit)
            unit_tests.append(item)

    # 单元测试 -> 集成测试
    items[:] = unit_tests + integration_tests


def pytest_configure(config):
    """注册自定义标记。"""
    config.addinivalue_line("markers", "unit: 单元测试")
    config.addinivalue_line("markers", "integration: 集成测试")
    config.addinivalue_line("markers", "slow: 慢速测试")
    config.addinivalue_line("markers", "network: 需要网络连接的测试")
    config.addinivalue_line("markers", "live: 需要真实后端配置的测试")


# ============================================================
# 临时目录和文件 Fixtures
# ============================================================


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """创建临时目录用于测试。"""
    return tmp_path


@pytest.fixture
def temp_data_dir(temp_dir: Path) -> Path:
    """创建模拟的 data 目录结构。"""
    data_dir = temp_dir / "data"
    data_dir.mkdir()

    # 创建必要的子目录
    (data_dir / "config").mkdir()
    (data_dir / "plugins").mkdir()
    (data_dir / "temp").mkdir()
    (data_dir / "logs").mkdir()

    return data_dir


# ============================================================
# Mock Fixtures
# ============================================================


@pytest.fixture
def mock_config(temp_data_dir: Path) -> dict[str, object]:
    """创建测试配置。"""
    return {
        "data_dir": str(temp_data_dir),
        "log_level": "DEBUG",
    }


@pytest.fixture
def mock_http_client():
    """创建模拟的 HTTP 客户端。"""
    client = MagicMock()
    client.get = AsyncMock(return_value={"status": "ok"})
    client.post = AsyncMock(return_value={"status": "ok"})
    return client


# ============================================================
# Application Fixtures
# ============================================================


@pytest.fixture
def test_settings():
    """创建测试用的应用设置。"""
    from nahida_bot.core.config import Settings

    return Settings(
        app_name="Test Bot",
        debug=True,
        host="127.0.0.1",
        port=6666,
        db_path=":memory:",
    )


@pytest.fixture
async def app(test_settings):
    """创建并初始化测试用的应用实例。"""
    from nahida_bot.core.app import Application

    application = Application(settings=test_settings)
    await application.initialize()
    yield application
    # 清理：如果应用已启动，则停止
    if application.is_started:
        await application.stop()


@pytest.fixture
def live_llm_config() -> dict[str, str] | None:
    """Return live LLM config from env variables, or None when incomplete.

    Supported env keys:
    - NAHIDA_LIVE_OPENAI_BASE_URL
    - NAHIDA_LIVE_OPENAI_API_KEY (or OPENAI_API_KEY)
    - NAHIDA_LIVE_OPENAI_MODEL
    """
    base_url = os.getenv("NAHIDA_LIVE_OPENAI_BASE_URL")
    api_key = os.getenv("NAHIDA_LIVE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("NAHIDA_LIVE_OPENAI_MODEL")

    if not base_url or not api_key or not model:
        return None

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }
