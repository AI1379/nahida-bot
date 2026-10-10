"""Tests for the dedicated exec venv (environment separation).

The exec tool and cron external commands must run ``python``/``pip`` in a
venv separate from the bot's own, so model-authored scripts can install
packages without ever mutating the bot runtime.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from nahida_bot.core.exec_env import (
    build_exec_env,
    configure_exec_venv,
    ensure_exec_venv,
)
from nahida_bot.scheduler.script_executor import execute_script
from tests.test_scheduler import _job


@pytest.fixture(autouse=True)
def _reset_exec_venv():
    yield
    configure_exec_venv(None)


def _venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def test_disabled_when_not_configured() -> None:
    assert configure_exec_venv(None) is None
    assert configure_exec_venv("   ") is None
    assert build_exec_env() is None
    assert ensure_exec_venv() is None


def test_build_exec_env_prepends_venv_bin(tmp_path: Path) -> None:
    venv = configure_exec_venv(str(tmp_path / "exec-venv"))
    assert venv is not None
    env = build_exec_env()
    assert env is not None
    assert env["PATH"].startswith(str(_venv_bin(venv)) + os.pathsep)
    assert env["VIRTUAL_ENV"] == str(venv)
    # Everything else (proxy config needed for pip installs, etc.) is
    # inherited from the bot environment unchanged.
    for key, value in os.environ.items():
        if key not in ("PATH", "VIRTUAL_ENV"):
            assert env.get(key) == value


def test_ensure_exec_venv_creates_and_is_idempotent(tmp_path: Path) -> None:
    venv = configure_exec_venv(str(tmp_path / "exec-venv"))
    assert venv is not None
    assert ensure_exec_venv() == venv
    python_name = "python.exe" if os.name == "nt" else "python"
    assert (_venv_bin(venv) / python_name).exists()
    assert ensure_exec_venv() == venv


@pytest.mark.asyncio
@pytest.mark.parametrize("relative_path", [False, True])
async def test_execute_script_runs_in_exec_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_path: bool
) -> None:
    """A workspace cwd must not change which exec venv supplies Python."""
    monkeypatch.chdir(tmp_path)
    configured_path = "exec-venv" if relative_path else str(tmp_path / "exec-venv")
    venv = configure_exec_venv(configured_path)
    assert venv is not None
    ensure_exec_venv()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Plain ``python`` (not sys.executable) so the assertion actually
    # exercises the PATH prepend inside build_exec_env().
    job = replace(
        _job(),
        executor_type="script_then_agent",
        script_command='python -c "import sys; print(sys.prefix)"',
        script_working_dir=str(workspace),
        script_timeout_seconds=60,
    )
    result = await execute_script(job)

    assert result.succeeded, result.stderr
    assert result.stdout.strip() == str(venv)


@pytest.mark.asyncio
async def test_execute_script_inherits_bot_env_when_disabled(tmp_path: Path) -> None:
    configure_exec_venv(None)
    job = replace(
        _job(),
        executor_type="script_then_agent",
        script_command=f'"{sys.executable}" -c "import sys; print(sys.prefix)"',
        script_timeout_seconds=60,
    )
    result = await execute_script(job)

    assert result.succeeded, result.stderr
    assert result.stdout.strip() == sys.prefix
