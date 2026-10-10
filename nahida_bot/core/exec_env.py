"""Dedicated Python environment for exec'd commands.

Model-authored scripts (exec tool, cron external commands) must not share
the bot's own virtualenv: a ``pip install`` from spawned code would mutate
the production runtime. This module owns a small process-global venv
location, bootstraps the venv once at startup, and builds the subprocess
environment for both execution entry points.

This is environment separation only, not a security boundary: spawned code
still runs as the same OS user and can read bot-readable files. See
``docs/design/linux-execution-isolation.md`` for the full isolation design.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_configured_path: Path | None = None


def configure_exec_venv(path: str | None) -> Path | None:
    """Set the exec venv location. ``None``/empty disables separation."""
    global _configured_path
    if path and path.strip():
        # Child commands change cwd to their workspace. Keep PATH entries
        # anchored to the bot's startup directory rather than that child cwd.
        _configured_path = Path(path).expanduser().resolve()
    else:
        _configured_path = None
    return _configured_path


def exec_venv_path() -> Path | None:
    """Return the configured exec venv path, or ``None`` when disabled."""
    return _configured_path


def _venv_bin_dir(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def ensure_exec_venv() -> Path | None:
    """Create the exec venv on first use; idempotent afterwards.

    Uses the same interpreter that runs the bot, so the Python version
    matches while site-packages stay independent in both directions.
    Failures propagate: a configured-but-unusable venv must stop startup
    instead of silently degrading back to the shared bot environment.
    """
    venv = _configured_path
    if venv is None:
        return None
    python_name = "python.exe" if os.name == "nt" else "python"
    if not (_venv_bin_dir(venv) / python_name).exists():
        venv.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Failed to bootstrap exec venv at {venv}: {detail}")
    return venv


def build_exec_env() -> dict[str, str] | None:
    """Build the environment for exec'd commands.

    ``None`` means the feature is disabled and callers should inherit the
    bot environment unchanged (legacy behaviour). When enabled, the venv's
    bin directory is prepended to ``PATH`` so ``python``/``pip`` resolve to
    the dedicated environment, and ``VIRTUAL_ENV`` points at it instead of
    the bot's venv. Everything else (proxy settings included) is inherited:
    spawned code needs the network for package installs.
    """
    venv = _configured_path
    if venv is None:
        return None
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(_venv_bin_dir(venv)), env.get("PATH", "")])
    env["VIRTUAL_ENV"] = str(venv)
    return env
