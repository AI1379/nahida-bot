"""Feishu outbound conversion (OutboundMessage → send items + target resolution)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from nahida_bot.channels.feishu.config import FeishuPluginConfig
from nahida_bot.channels.feishu.markdown_post import (
    looks_like_markdown,
    markdown_to_post_content,
    split_markdown,
)
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.outbound_mentions import parse_outbound_parts
from nahida_bot.plugins.base import Attachment, OutboundMessage


class FeishuTargetError(ValueError):
    """Raised when a Feishu send target cannot be resolved."""


@dataclass(slots=True)
class FeishuSendItem:
    """One platform-level message to send."""

    kind: str  # "text" | "post" | "image" | "file"
    content: str = ""  # JSON content string for text/post items
    path: str = ""  # local file path for image/file items
    file_name: str = ""
    file_type: str = "stream"  # im/v1/files file_type for file items
    duration_ms: int = 0
    caption: str = ""


@dataclass(slots=True)
class FeishuOutboundPayload:
    """Converted payload: ordered send items plus routing context."""

    items: list[FeishuSendItem] = field(default_factory=list)
    reply_to: str = ""


_FILE_TYPE_BY_SUFFIX: dict[str, str] = {
    ".mp4": "mp4",
    ".opus": "opus",
    ".pdf": "pdf",
    ".doc": "doc",
    ".xls": "xls",
    ".ppt": "ppt",
}


class FeishuOutboundConverter:
    """Convert ``OutboundMessage`` into ordered Feishu send items."""

    def __init__(self, config: FeishuPluginConfig) -> None:
        self._config = config

    def to_payload(self, message: OutboundMessage) -> FeishuOutboundPayload:
        text = self._inject_mentions(message)
        items: list[FeishuSendItem] = []

        use_post = (
            self._config.markdown_enabled
            and bool(text.strip())
            and looks_like_markdown(text)
        )
        if use_post:
            for chunk in split_markdown(text, limit=self._config.max_text_length):
                items.append(
                    FeishuSendItem(kind="post", content=markdown_to_post_content(chunk))
                )
        elif text.strip():
            for chunk in split_markdown(text, limit=self._config.max_text_length):
                items.append(
                    FeishuSendItem(
                        kind="text",
                        content=json.dumps({"text": chunk}, ensure_ascii=False),
                    )
                )

        for attachment in message.attachments:
            item = self._attachment_to_item(attachment)
            if item is not None:
                items.append(item)

        return FeishuOutboundPayload(items=items, reply_to=message.reply_to.strip())

    # ── mentions ──────────────────────────────────────────────────

    def _inject_mentions(self, message: OutboundMessage) -> str:
        """Rewrite validated mention tokens into inline ``<at>`` tags.

        Tokens that failed group-membership validation stay as literal text so
        a hallucinated id never breaks the send. The ``<at ...>`` string works
        natively in text messages and is parsed into ``at`` elements by the
        post converter.
        """
        validated = _validated_mention_ids(message.extra)
        if not any(part.is_mention for part in parse_outbound_parts(message.text)):
            return message.text

        pieces: list[str] = []
        for part in parse_outbound_parts(message.text):
            if part.is_mention and part.user_id in validated:
                pieces.append(f'<at user_id="{part.user_id}"></at>')
            else:
                pieces.append(part.raw if part.is_mention else part.text)
        return "".join(pieces)

    # ── attachments ───────────────────────────────────────────────

    def _attachment_to_item(self, attachment: Attachment) -> FeishuSendItem | None:
        path = attachment.path
        if not path:
            return None
        file_name = attachment.filename or Path(path).name
        suffix = Path(file_name).suffix.lower() or Path(path).suffix.lower()

        if attachment.type in {"photo", "image"}:
            return FeishuSendItem(
                kind="image", path=path, file_name=file_name, caption=attachment.caption
            )
        if attachment.type in {"audio", "voice", "record"}:
            # Feishu audio messages require OPUS; anything else ships as a
            # plain file to avoid an upload rejection.
            file_type = (
                "opus"
                if suffix == ".opus"
                else _FILE_TYPE_BY_SUFFIX.get(suffix, "stream")
            )
            return FeishuSendItem(
                kind="file",
                path=path,
                file_name=file_name,
                file_type=file_type,
                caption=attachment.caption,
            )
        # video / document / everything else
        return FeishuSendItem(
            kind="file",
            path=path,
            file_name=file_name,
            file_type=_FILE_TYPE_BY_SUFFIX.get(suffix, "stream"),
            caption=attachment.caption,
        )


def resolve_target(
    target: str,
    message: OutboundMessage,
) -> tuple[str, str]:
    """Resolve ``(receive_id_type, receive_id)`` for one outbound send.

    Priority: ``extra["chat_address"]`` (cron/proactive path) → typed address
    target (``feishu:group:oc_…``) → bare id target classified by prefix
    (``ou_`` → open_id, ``oc_`` → chat_id).
    """
    extra_address = _chat_address_from_extra(message.extra.get("chat_address"))
    if extra_address is not None:
        return _receive_id_for_address(extra_address)

    if target.count(":") >= 2:
        try:
            target_address = ChatAddress.parse(target)
        except ValueError as exc:
            raise FeishuTargetError(
                f"Invalid Feishu target address: {target!r}"
            ) from exc
        if target_address.channel != "feishu":
            raise FeishuTargetError(
                f"Feishu cannot send to channel {target_address.channel!r}"
            )
        if target_address.is_typed:
            return _receive_id_for_address(target_address)

    if not target:
        raise FeishuTargetError("Feishu target is empty")

    if target.startswith("ou_"):
        return "open_id", target
    if target.startswith("on_"):
        return "union_id", target
    # oc_ chat ids cover groups and p2p chats alike; default to chat_id for
    # any other shape received from inbound events.
    return "chat_id", target


def message_id_from_send_result(result: dict[str, object]) -> str:
    """Extract the platform message id from a send/reply API result."""
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if isinstance(data, dict):
        value = data.get("message_id")
        if value is not None:
            return str(value)
    return ""


def _receive_id_for_address(address: ChatAddress) -> tuple[str, str]:
    if address.channel != "feishu":
        raise FeishuTargetError(f"Feishu cannot send to channel {address.channel!r}")
    target_id = address.target_id
    if target_id.startswith("ou_"):
        return "open_id", target_id
    if target_id.startswith("on_"):
        return "union_id", target_id
    return "chat_id", target_id


def _chat_address_from_extra(raw: object) -> ChatAddress | None:
    if raw is None:
        return None
    if isinstance(raw, ChatAddress):
        return raw
    if isinstance(raw, str):
        if not raw:
            return None
        try:
            return ChatAddress.parse(raw)
        except ValueError as exc:
            raise FeishuTargetError(f"Invalid chat_address metadata: {raw!r}") from exc
    return None


def _validated_mention_ids(extra: dict[str, object]) -> frozenset[str]:
    """Read mention open_ids the plugin validated for this message, if any."""
    raw = extra.get("feishu_mention_ids")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(item) for item in raw if str(item))
