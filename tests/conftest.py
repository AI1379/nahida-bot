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
