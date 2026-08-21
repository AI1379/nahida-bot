"""Restricted Gateway service for actor-bound Desktop control."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nahida_bot.gateway.services.node_invoker import NodeInvoker
from nahida_bot.gateway.services.node_registry import NodeRegistry

DESKTOP_EXEC_CAPABILITY = "desktop.process.exec"
DESKTOP_FILE_READ_CAPABILITY = "desktop.fs.read_text"
DESKTOP_SCREENSHOT_CAPABILITY = "desktop.computer.screenshot"
DESKTOP_INPUT_CAPABILITY = "desktop.computer.input"

MAX_DESKTOP_PROGRAM_CHARS = 1024
MAX_DESKTOP_ROOT_ID_CHARS = 128
MAX_DESKTOP_EXEC_ARGS = 64
MAX_DESKTOP_EXEC_ARG_CHARS = 4096
MAX_DESKTOP_EXEC_ARGS_CHARS = 16_384
MAX_DESKTOP_PATH_CHARS = 1024
MAX_DESKTOP_FILE_OFFSET = 2**63 - 1
MAX_DESKTOP_FILE_READ_BYTES = 1024 * 1024
MAX_DESKTOP_TYPED_CHARS = 2000
MAX_DESKTOP_HOTKEY_KEYS = 8
MAX_DESKTOP_SCROLL_STEPS = 10
DESKTOP_INPUT_ACTIONS = frozenset({"move", "click", "scroll", "type", "key"})
DESKTOP_MOUSE_BUTTONS = frozenset({"left", "right", "middle"})


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

    async def screenshot(
        self,
        *,
        conversation_id: str,
        actor_account_key: str,
        caller: str,
    ) -> DesktopControlResult:
        return await self._invoke(
            capability=DESKTOP_SCREENSHOT_CAPABILITY,
            arguments={"actorAccountKey": actor_account_key},
            conversation_id=conversation_id,
            actor_account_key=actor_account_key,
            caller=caller,
        )

    async def input(
        self,
        *,
        action: str,
        x: int | None,
        y: int | None,
        button: str,
        clicks: int,
        scroll_steps: int,
        text: str,
        keys: list[str],
        conversation_id: str,
        actor_account_key: str,
        caller: str,
    ) -> DesktopControlResult:
        arguments: dict[str, Any] = {
            "action": action,
            "button": button,
            "clicks": clicks,
            "scrollSteps": scroll_steps,
            "text": text,
            "keys": list(keys),
            "actorAccountKey": actor_account_key,
        }
        if x is not None:
            arguments["x"] = x
        if y is not None:
            arguments["y"] = y
        error = _validate_input_args(arguments)
        if error is not None:
            return _invalid(error)
        return await self._invoke(
            capability=DESKTOP_INPUT_CAPABILITY,
            arguments=arguments,
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


def _validate_input_args(arguments: dict[str, Any]) -> str | None:
    action = arguments.get("action")
    if not isinstance(action, str) or action not in DESKTOP_INPUT_ACTIONS:
        return "action must be move, click, scroll, type, or key"

    x = arguments.get("x")
    y = arguments.get("y")
    if (x is None) != (y is None):
        return "x and y must be supplied together"
    if action in {"move", "click"} and (x is None or y is None):
        return "x and y are required for this action"
    for name, coordinate in (("x", x), ("y", y)):
        if coordinate is not None and (
            not isinstance(coordinate, int)
            or isinstance(coordinate, bool)
            or not 0 <= coordinate <= 1000
        ):
            return f"{name} must be between 0 and 1000"

    button = arguments.get("button")
    clicks = arguments.get("clicks")
    scroll_steps = arguments.get("scrollSteps")
    text = arguments.get("text")
    keys = arguments.get("keys")
    if action == "click":
        if button not in DESKTOP_MOUSE_BUTTONS:
            return "button must be left, right, or middle"
        if (
            not isinstance(clicks, int)
            or isinstance(clicks, bool)
            or not 1 <= clicks <= 2
        ):
            return "clicks must be 1 or 2"
    if action == "scroll" and (
        not isinstance(scroll_steps, int)
        or isinstance(scroll_steps, bool)
        or scroll_steps == 0
        or abs(scroll_steps) > MAX_DESKTOP_SCROLL_STEPS
    ):
        return "scroll_steps must be between -10 and 10, excluding 0"
    if action == "type" and (
        not isinstance(text, str)
        or not text
        or len(text.encode("utf-16-le")) // 2 > MAX_DESKTOP_TYPED_CHARS
    ):
        return f"text must contain between 1 and {MAX_DESKTOP_TYPED_CHARS} UTF-16 units"
    if action == "key":
        if (
            not isinstance(keys, list)
            or not 1 <= len(keys) <= MAX_DESKTOP_HOTKEY_KEYS
            or not all(isinstance(key, str) and key for key in keys)
        ):
            return f"keys must contain between 1 and {MAX_DESKTOP_HOTKEY_KEYS} strings"
    return None


def _invalid(message: str) -> DesktopControlResult:
    return DesktopControlResult(
        ok=False, error_code="invalid_arguments", error_message=message
    )


__all__ = [
    "DESKTOP_EXEC_CAPABILITY",
    "DESKTOP_FILE_READ_CAPABILITY",
    "DESKTOP_INPUT_CAPABILITY",
    "DESKTOP_SCREENSHOT_CAPABILITY",
    "DESKTOP_INPUT_ACTIONS",
    "MAX_DESKTOP_EXEC_ARGS",
    "MAX_DESKTOP_EXEC_ARG_CHARS",
    "MAX_DESKTOP_FILE_READ_BYTES",
    "MAX_DESKTOP_HOTKEY_KEYS",
    "MAX_DESKTOP_PATH_CHARS",
    "MAX_DESKTOP_PROGRAM_CHARS",
    "MAX_DESKTOP_SCROLL_STEPS",
    "MAX_DESKTOP_TYPED_CHARS",
    "DesktopControlResult",
    "DesktopControlService",
]
