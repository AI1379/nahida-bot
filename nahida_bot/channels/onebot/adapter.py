"""OneBot protocol adapter — version detection and dispatch."""

from __future__ import annotations

from typing import Any

from nahida_bot.channels.onebot.config import OneBotPluginConfig
from nahida_bot.channels.onebot.protocol import (
    NormalizedEvent,
    OneBotProtocol,
    OneBotResponse,
)


class OneBotAdapter:
    """Detect protocol version and dispatch to v11/v12 protocol handlers.

    Wraps a concrete protocol implementation and provides a stable interface
    for the plugin layer.
    """

    def __init__(self, config: OneBotPluginConfig) -> None:
        self._config = config
        self._protocol: OneBotProtocol | None = None
        self._detected_version: str | None = None

    @property
    def protocol(self) -> OneBotProtocol | None:
        return self._protocol

    @property
    def detected_version(self) -> str | None:
        return self._detected_version

    def detect_version(self, raw: dict[str, Any]) -> str | None:
        """Detect protocol version from a raw event or response frame.

        Respects explicit config; otherwise auto-detects.
        """
        if self._config.protocol_version != "auto":
            return self._config.protocol_version

        detected = OneBotProtocol.detect_version(raw)
        if detected and self._detected_version is None:
            self._detected_version = detected
        return detected

    def ensure_protocol(self, version: str) -> OneBotProtocol:
        """Return (creating if needed) the protocol handler for a version."""
        if self._protocol is not None and self._protocol.version == version:
            return self._protocol

        if version == "v11":
            from nahida_bot.channels.onebot.v11.action import (
                decode_v11_response,
                encode_v11_action,
                is_v11_api_response,
            )
            from nahida_bot.channels.onebot.v11.event import normalize_v11_event

            self._protocol = _V11ProtocolAdapter(
                normalize_event_fn=normalize_v11_event,
                encode_action_fn=encode_v11_action,
                decode_response_fn=decode_v11_response,
                is_api_response_fn=is_v11_api_response,
            )
        elif version == "v12":
            raise NotImplementedError("OneBot v12 support is not yet implemented")
        else:
            raise ValueError(f"Unsupported OneBot protocol version: {version!r}")

        return self._protocol


class _V11ProtocolAdapter(OneBotProtocol):
    """Thin adapter that wraps v11 stateless functions as a protocol instance."""

    def __init__(
        self,
        *,
        normalize_event_fn: Any,
        encode_action_fn: Any,
        decode_response_fn: Any,
        is_api_response_fn: Any,
    ) -> None:
        self._normalize_event_fn = normalize_event_fn
        self._encode_action_fn = encode_action_fn
        self._decode_response_fn = decode_response_fn
        self._is_api_response_fn = is_api_response_fn

    @property
    def version(self) -> str:
        return "v11"

    def detect_event_type(self, raw: dict[str, Any]) -> str | None:
        post_type = raw.get("post_type", "")
        if post_type == "message":
            message_type = raw.get("message_type", "")
            return f"message.{message_type}" if message_type else "message"
        if post_type:
            return post_type
        return None

    def normalize_event(self, raw: dict[str, Any]) -> NormalizedEvent:
        return self._normalize_event_fn(raw)

    def encode_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._encode_action_fn(action, params)

    def decode_response(self, raw: dict[str, Any]) -> OneBotResponse:
        return self._decode_response_fn(raw)

    def is_api_response(self, raw: dict[str, Any]) -> bool:
        return self._is_api_response_fn(raw)

    def get_echo(self, raw: dict[str, Any]) -> str | None:
        echo = raw.get("echo")
        return str(echo) if echo is not None else None
