"""Restricted Gateway service for actor-bound Desktop control."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nahida_bot.gateway.services.node_invoker import NodeInvoker
from nahida_bot.gateway.services.node_registry import NodeRegistry

DESKTOP_EXEC_CAPABILITY = "desktop.process.exec"
DESKTOP_FILE_READ_CAPABILITY = "desktop.fs.read_text"

MAX_DESKTOP_PROGRAM_CHARS = 1024
MAX_DESKTOP_ROOT_ID_CHARS = 128
MAX_DESKTOP_EXEC_ARGS = 64
MAX_DESKTOP_EXEC_ARG_CHARS = 4096
MAX_DESKTOP_EXEC_ARGS_CHARS = 16_384
MAX_DESKTOP_PATH_CHARS = 1024
MAX_DESKTOP_FILE_OFFSET = 2**63 - 1
MAX_DESKTOP_FILE_READ_BYTES = 1024 * 1024


@dataclass(slots=True, frozen=True)
class DesktopControlResult:
    ok: bool
    node_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""


class DesktopControlService:
    """Invoke fixed capabilities on the caller's uniquely actor-bound Desktop."""

    def __init__(self, registry: NodeRegistry, invoker: NodeInvoker) -> None:
        self._registry = registry
        self._invoker = invoker

    async def exec(
        self,
        *,
        program: str,
        args: list[str],
        cwd: str,
        conversation_id: str,
        actor_account_key: str,
        caller: str,
    ) -> DesktopControlResult:
        error = _validate_string("program", program, MAX_DESKTOP_PROGRAM_CHARS)
        if error is None:
            error = _validate_exec_args(args)
        if error is None:
            error = _validate_string(
                "cwd", cwd, MAX_DESKTOP_PATH_CHARS, allow_empty=True
            )
        if error is not None:
            return _invalid(error)

        return await self._invoke(
            capability=DESKTOP_EXEC_CAPABILITY,
            arguments={
                "program": program,
                "args": list(args),
                "cwd": cwd,
                "actorAccountKey": actor_account_key,
            },
            conversation_id=conversation_id,
            actor_account_key=actor_account_key,
            caller=caller,
        )

    async def file_read(
        self,
        *,
        path: str,
        root_id: str,
        offset: int,
        max_bytes: int,
        conversation_id: str,
        actor_account_key: str,
        caller: str,
    ) -> DesktopControlResult:
        error = _validate_string("path", path, MAX_DESKTOP_PATH_CHARS)
        if error is None:
            error = _validate_string(
                "root_id", root_id, MAX_DESKTOP_ROOT_ID_CHARS, allow_empty=True
            )
        if error is None and (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or offset > MAX_DESKTOP_FILE_OFFSET
        ):
            error = f"offset must be between 0 and {MAX_DESKTOP_FILE_OFFSET}"
        if error is None and (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or not 1 <= max_bytes <= MAX_DESKTOP_FILE_READ_BYTES
        ):
            error = f"max_bytes must be between 1 and {MAX_DESKTOP_FILE_READ_BYTES}"
        if error is not None:
            return _invalid(error)

        return await self._invoke(
            capability=DESKTOP_FILE_READ_CAPABILITY,
            arguments={
                "path": path,
                "rootId": root_id,
                "offset": offset,
                "maxBytes": max_bytes,
                "actorAccountKey": actor_account_key,
            },
            conversation_id=conversation_id,
            actor_account_key=actor_account_key,
            caller=caller,
        )

    async def _invoke(
        self,
        *,
        capability: str,
        arguments: dict[str, Any],
        conversation_id: str,
        actor_account_key: str,
        caller: str,
    ) -> DesktopControlResult:
        if not actor_account_key.strip():
            return DesktopControlResult(
                ok=False,
                error_code="actor_unavailable",
                error_message="trusted actor identity is unavailable",
            )

        candidates = self._registry.find_bound_capability_owners(
            capability=capability,
            conversation_id=conversation_id,
            actor_account_key=actor_account_key,
            node_type="desktop",
        )
        if not candidates:
            return DesktopControlResult(
                ok=False,
                error_code="desktop_unavailable",
                error_message=(
                    "no online Desktop with this capability is bound to the actor"
                ),
            )
        if len(candidates) > 1:
            return DesktopControlResult(
                ok=False,
                error_code="ambiguous_desktop",
                error_message="multiple online Desktops match this actor",
            )

        target = candidates[0]
        invoked = await self._invoker.invoke(
            capability=capability,
            arguments=arguments,
            caller=caller,
            node_id=target.node_id,
        )
        if not invoked.ok:
            return DesktopControlResult(
                ok=False,
                node_id=target.node_id,
                error_code=(
                    invoked.error.code if invoked.error is not None else "failed"
                ),
                error_message=(
                    invoked.error.message
                    if invoked.error is not None
                    else "Desktop rejected the request"
                ),
            )
        return DesktopControlResult(
            ok=True, node_id=target.node_id, payload=invoked.payload
        )


def _validate_string(
    name: str, value: object, max_chars: int, *, allow_empty: bool = False
) -> str | None:
    if not isinstance(value, str):
        return f"{name} must be a string"
    if not value and not allow_empty:
        return f"{name} must not be empty"
    if len(value) > max_chars:
        return f"{name} exceeds {max_chars} characters"
    if "\x00" in value:
        return f"{name} contains a NUL character"
    return None


def _validate_exec_args(args: object) -> str | None:
    if not isinstance(args, list):
        return "args must be a list of strings"
    if len(args) > MAX_DESKTOP_EXEC_ARGS:
        return f"args exceeds {MAX_DESKTOP_EXEC_ARGS} items"
    total = 0
    for value in args:
        if not isinstance(value, str):
            return "args must contain only strings"
        if len(value) > MAX_DESKTOP_EXEC_ARG_CHARS:
            return f"an arg exceeds {MAX_DESKTOP_EXEC_ARG_CHARS} characters"
        if "\x00" in value:
            return "an arg contains a NUL character"
        total += len(value)
    if total > MAX_DESKTOP_EXEC_ARGS_CHARS:
        return f"args exceeds {MAX_DESKTOP_EXEC_ARGS_CHARS} total characters"
    return None


def _invalid(message: str) -> DesktopControlResult:
    return DesktopControlResult(
        ok=False, error_code="invalid_arguments", error_message=message
    )


__all__ = [
    "DESKTOP_EXEC_CAPABILITY",
    "DESKTOP_FILE_READ_CAPABILITY",
    "MAX_DESKTOP_EXEC_ARGS",
    "MAX_DESKTOP_EXEC_ARG_CHARS",
    "MAX_DESKTOP_FILE_READ_BYTES",
    "MAX_DESKTOP_PATH_CHARS",
    "MAX_DESKTOP_PROGRAM_CHARS",
    "DesktopControlResult",
    "DesktopControlService",
]
