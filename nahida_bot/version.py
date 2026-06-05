"""Version resolution for Nahida Bot.

The version string is composed of:
  - A base version (e.g. ``0.1.0``) kept in this module.
  - An optional git short hash appended automatically when running from a
    source checkout.

Resolution order:
  1. If the environment variable ``NAHIDA_BOT_VERSION`` is set, use it as-is
     (useful for Docker / frozen builds where git info is injected at build time).
  2. Try ``git describe --always --dirty`` relative to the package directory.
  3. Fall back to the hardcoded ``BASE_VERSION``.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

# ── Single source of truth ──────────────────────────────────────────────────
BASE_VERSION = "0.1.0"

# Directory that contains this file — used to locate the git repo root.
_PACKAGE_DIR = Path(__file__).resolve().parent


def _git_describe() -> str | None:
    """Return a short git description (commit hash), or *None* on failure."""
    try:
        result = subprocess.run(
            [
                "git",
                "describe",
                "--always",
                "--dirty",
                "--abbrev=7",
            ],
            cwd=_PACKAGE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            tag: str = result.stdout.strip()
            return tag if tag else None
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return the full version string (e.g. ``0.1.0 (b379877)``)."""
    # 1) Explicit override (Docker, CI, frozen builds)
    env_version = os.environ.get("NAHIDA_BOT_VERSION")
    if env_version:
        return env_version

    # 2) git-based version
    git_tag = _git_describe()
    if git_tag:
        # ``git describe --always`` returns either a tag like ``v0.1.0`` or a
        # short hash like ``b379877`` / ``b379877-dirty``.  Only append when
        # it looks like a hash (doesn't start with the base version).
        if not git_tag.startswith("v") or git_tag not in (
            f"v{BASE_VERSION}",
            BASE_VERSION,
        ):
            return f"{BASE_VERSION} ({git_tag})"
        # Tag matches — just return the base version.
        return BASE_VERSION

    # 3) Fallback
    return BASE_VERSION


@lru_cache(maxsize=1)
def get_version_info() -> dict[str, str]:
    """Return a dict with structured version info for APIs / WebUI."""
    full = get_version()
    git_hash = _git_describe()
    return {
        "version": full,
        "base_version": BASE_VERSION,
        "git_hash": git_hash or "",
    }
