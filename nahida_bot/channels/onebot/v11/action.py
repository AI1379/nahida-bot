"""OneBot v11 action encoding and response decoding."""

from __future__ import annotations

from typing import Any

from nahida_bot.channels.onebot.protocol import OneBotResponse


def encode_v11_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build a v11 action payload from normalized action name and params."""
    return {
        "action": action,
        "params": dict(params),
    }


def decode_v11_response(raw: dict[str, Any]) -> OneBotResponse:
    """Parse a v11 action response into a stable result object."""
    return OneBotResponse(
        status=str(raw.get("status", "")),
        retcode=int(raw.get("retcode", -1)),
        data=raw.get("data"),
        echo=str(raw.get("echo", "")),
        message=str(raw.get("message", raw.get("msg", raw.get("wording", "")))),
    )


def is_v11_api_response(raw: dict[str, Any]) -> bool:
    """Check whether a raw dict is a v11 API response."""
    return "retcode" in raw and "status" in raw


def get_v11_echo(raw: dict[str, Any]) -> str | None:
    """Extract echo field from a v11 raw message."""
    echo = raw.get("echo")
    return str(echo) if echo is not None else None


# ── Action name mapping: normalized → v11 ──────────────

_ACTION_MAP: dict[str, str] = {
    "send_message": "send_msg",
    "get_message": "get_msg",
    "get_forwarded_messages": "get_forward_msg",
    "get_self_info": "get_login_info",
    "get_group_info": "get_group_info",
    "get_group_list": "get_group_list",
    "get_friend_list": "get_friend_list",
    "get_group_member_info": "get_group_member_info",
    "get_group_member_list": "get_group_member_list",
    "delete_message": "delete_msg",
    "get_file": "get_file_url",
    "get_file_url": "get_file_url",
}


def resolve_v11_action(normalized_action: str) -> str:
    """Map a normalized action name to its v11 equivalent.

    If the action is already a v11 name, return it as-is.
    """
    return _ACTION_MAP.get(normalized_action, normalized_action)
