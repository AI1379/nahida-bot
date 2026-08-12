"""Cross-session messaging and local attachment tools."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, cast

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import current_session
from nahida_bot.plugins.builtin.tools.context import (
    typed_address_from_session_context,
)
from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot_sdk.api import BotAPI
from nahida_bot_sdk.messaging import Attachment, OutboundMessage


_ATTACHMENT_TYPES = frozenset({"auto", "photo", "document", "audio", "video"})
_ATTACHMENT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to the file. Relative to workspace, or absolute if allowed "
                "by config."
            ),
        },
        "type": {
            "type": "string",
            "enum": ["auto", "photo", "document", "audio", "video"],
            "description": "Attachment type. 'auto' infers from file MIME type.",
        },
        "caption": {
            "type": "string",
            "description": "Optional caption for the attachment.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}
_MESSAGE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": (
                "Delivery target as 'platform:type:id' (e.g. "
                "'milky:group:20001', 'telegram:private:123456')."
            ),
        },
        "text": {"type": "string", "description": "Message text to send."},
        "delivery": {
            "type": "string",
            "enum": ["notify", "record"],
            "description": (
                "Delivery mode. 'notify' (default) sends without affecting the "
                "target session's history. 'record' also writes into the target "
                "session's history so the agent there sees it in context."
            ),
        },
        "attachments": {
            "type": "array",
            "items": _ATTACHMENT_ITEM_SCHEMA,
            "description": "Optional files to send alongside the message.",
        },
    },
    "required": ["target", "text"],
    "additionalProperties": False,
}
_LOCAL_ATTACHMENT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to the local file. By default this must be relative to the "
                "active workspace. Absolute paths require the builtin-commands "
                "allow_external_attachment_paths config."
            ),
        },
        "attachment_type": {
            "type": "string",
            "enum": ["auto", "photo", "document", "audio", "video"],
            "description": "Attachment type. Use auto to infer from the file MIME type.",
        },
        "caption": {
            "type": "string",
            "description": "Optional caption sent with the attachment.",
        },
        "filename": {
            "type": "string",
            "description": "Optional filename shown by the platform.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


class AttachmentResolver:
    """Resolve workspace and policy-approved external attachment paths."""

    def __init__(self, api: BotAPI, config: Mapping[str, Any]) -> None:
        self._api = api
        self._config = config

    def resolve(self, path: str) -> Path:
        """Resolve one attachment path under the configured path policy."""
        raw_path = Path(path).expanduser()
        if raw_path.is_absolute():
            if not self.allows_external_paths():
                raise ValueError(
                    "Absolute attachment paths are disabled. Use a "
                    "workspace-relative path or enable builtin-commands."
                    "allow_external_attachment_paths."
                )
            resolved = raw_path.resolve(strict=False)
            self.validate_external_path(resolved)
            return resolved

        workspace_path = self._api.resolve_workspace_path(path)
        if not workspace_path:
            raise ValueError("Workspace is not available.")
        return Path(workspace_path)

    def allows_external_paths(self) -> bool:
        """Return whether absolute attachment paths are enabled."""
        return bool(self._config.get("allow_external_attachment_paths", False))

    def validate_external_path(self, path: Path) -> None:
        """Ensure an absolute path is inside a configured external root."""
        raw_roots = self._config.get("external_attachment_roots", [])
        if not raw_roots:
            return
        if not isinstance(raw_roots, list):
            raise ValueError("external_attachment_roots must be a list of paths.")

        allowed_roots = [
            Path(str(root)).expanduser().resolve(strict=False)
            for root in raw_roots
            if str(root).strip()
        ]
        if not allowed_roots:
            return
        if any(self._is_relative_to(path, root) for root in allowed_roots):
            return
        roots = ", ".join(str(root) for root in allowed_roots)
        raise ValueError(f"Attachment path is outside allowed external roots: {roots}")

    @staticmethod
    def infer_type(path: Path) -> str:
        """Infer the platform attachment category from a file extension."""
        mime_type, _encoding = mimetypes.guess_type(str(path))
        if mime_type:
            if mime_type.startswith("image/"):
                return "photo"
            if mime_type.startswith("audio/"):
                return "audio"
            if mime_type.startswith("video/"):
                return "video"
        return "document"

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True


class MessageTools:
    """Own message delivery, audit recording, and attachment preparation."""

    def __init__(self, api: BotAPI, config: Mapping[str, Any]) -> None:
        self._api = api
        self.attachments = AttachmentResolver(api, config)

    def message_definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return the privileged cross-session message tool."""
        return (
            PluginToolDefinition(
                name="message",
                description=(
                    "Send a message to a chat on any registered platform. Use "
                    "'notify' delivery for one-time notifications that do not affect "
                    "the target session's history. Use 'record' delivery to also "
                    "write the message into the target session's conversation "
                    "history, so the agent there can see it in context next time."
                    "Note that your output text will be sent to the current session "
                    "as well, so DO NOT use this tool to reply to the current "
                    "session's message. Only use it to send messages to other sessions."
                ),
                parameters=_MESSAGE_PARAMETERS,
                handler=self.send,
                requires_admin=True,
            ),
        )

    def attachment_definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return the current-chat local attachment tool."""
        return (
            PluginToolDefinition(
                name="send_local_attachment",
                description=(
                    "Send a local workspace file to the current chat as an "
                    "attachment. Use this for images, documents, audio, or video "
                    "files that already exist in the active workspace."
                ),
                parameters=_LOCAL_ATTACHMENT_PARAMETERS,
                handler=self.send_local_attachment,
            ),
        )

    async def send(
        self,
        text: str,
        target: str = "",
        delivery: str = "notify",
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        """Send a message to a typed chat and optionally record it in history."""
        context = current_session.get()
        if context is None:
            return "Error: No active session context."
        if delivery not in {"notify", "record"}:
            return "Error: delivery must be 'notify' or 'record'."

        parsed_address = self._parse_target(target)
        if isinstance(parsed_address, str):
            return parsed_address
        resolved = self._resolve_attachments(attachments or [])
        if isinstance(resolved, str):
            return resolved

        message_id = await self._api.send_message(
            parsed_address.target_id,
            OutboundMessage(
                text=text,
                extra={"chat_address": parsed_address.chat_key},
                attachments=resolved,
            ),
            channel=parsed_address.channel,
        )
        metadata = self._delivery_metadata(context, resolved)
        await self._record_delivery(
            address=parsed_address,
            text=text,
            delivery=delivery,
            message_id=message_id,
            metadata=metadata,
        )
        if delivery == "record":
            await self._api.record_session_event(
                parsed_address.chat_key,
                text,
                source="cross_session_message",
                metadata=dict(metadata),
            )
        return self._format_delivery(parsed_address, delivery, message_id)

    async def send_local_attachment(
        self,
        path: str,
        attachment_type: str = "auto",
        caption: str = "",
        filename: str = "",
    ) -> str:
        """Send one local file to the current chat."""
        context = current_session.get()
        if context is None:
            return "Error: No active session context."
        if attachment_type not in _ATTACHMENT_TYPES:
            return (
                "Error: attachment_type must be one of: "
                "auto, photo, document, audio, video."
            )
        try:
            file_path = self.attachments.resolve(path)
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: Invalid attachment path: {exc}"
        if not file_path.is_file():
            return f"Error: File does not exist: {path}"

        selected_type = (
            self.attachments.infer_type(file_path)
            if attachment_type == "auto"
            else attachment_type
        )
        extra: dict[str, Any] = {}
        address = typed_address_from_session_context(context)
        if address is not None:
            extra["chat_address"] = address.chat_key
        message_id = await self._api.send_message(
            context.chat_id,
            OutboundMessage(
                text="",
                extra=extra,
                attachments=[
                    Attachment(
                        type=selected_type,
                        path=str(file_path),
                        filename=filename or file_path.name,
                        caption=caption,
                    )
                ],
            ),
            channel=context.platform,
        )
        return f"Attachment sent: {message_id}" if message_id else "Attachment sent."

    def _resolve_attachments(
        self,
        items: list[dict[str, Any]],
    ) -> list[Attachment] | str:
        resolved: list[Attachment] = []
        for item in items:
            raw_path = item.get("path", "")
            if not raw_path:
                return "Error: Each attachment must have a 'path'."
            try:
                file_path = self.attachments.resolve(raw_path)
            except ValueError as exc:
                return f"Error: {exc}"
            if not file_path.is_file():
                return f"Error: File does not exist: {raw_path}"

            attachment_type = item.get("type", "auto")
            if attachment_type not in _ATTACHMENT_TYPES:
                return (
                    "Error: attachment type must be one of: "
                    "auto, photo, document, audio, video."
                )
            selected_type = (
                self.attachments.infer_type(file_path)
                if attachment_type == "auto"
                else attachment_type
            )
            resolved.append(
                Attachment(
                    type=selected_type,
                    path=str(file_path),
                    caption=item.get("caption", ""),
                )
            )
        return resolved

    async def _record_delivery(
        self,
        *,
        address: ChatAddress,
        text: str,
        delivery: str,
        message_id: str,
        metadata: dict[str, Any],
    ) -> None:
        recorder = cast(
            Callable[..., Awaitable[Any]],
            getattr(self._api, "record_message_delivery", None),
        )
        if callable(recorder):
            await recorder(
                target=address,
                text=text,
                source="message_tool",
                delivery_mode=delivery,
                status="sent",
                message_id=message_id,
                metadata=metadata,
            )

    @staticmethod
    def _parse_target(target: str) -> ChatAddress | str:
        if not target:
            return "Error: Provide a typed 'target' such as 'milky:group:20001'."
        try:
            address = ChatAddress.parse(target)
        except ValueError as exc:
            return f"Error: Invalid target format: {exc}"
        if not address.is_typed:
            return "Error: target must include a chat type, such as private or group."
        return address

    @staticmethod
    def _delivery_metadata(
        context: Any, attachments: list[Attachment]
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "from_session": context.session_id,
            "from_platform": context.platform,
            "from_chat_id": context.chat_id,
            "from_user_id": context.user_id,
        }
        if context.chat_address is not None:
            metadata["from_chat_address"] = context.chat_address.chat_key
        if attachments:
            metadata["attachment_count"] = len(attachments)
        return metadata

    @staticmethod
    def _format_delivery(
        address: ChatAddress,
        delivery: str,
        message_id: str,
    ) -> str:
        parts = [f"Message sent to {address.chat_key}"]
        if delivery == "record":
            parts.append("(recorded in target session history)")
        if message_id:
            parts[0] += f" (id: {message_id})"
        return ", ".join(parts)
