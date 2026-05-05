"""Tests for built-in image_understand tool wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from nahida_bot.agent.context import ContextBuilder, ContextMessage
from nahida_bot.agent.media.cache import MediaCache
from nahida_bot.agent.media.resolver import MediaPolicy, MediaResolver
from nahida_bot.agent.providers.base import (
    ChatProvider,
    ModelCapabilities,
    ProviderResponse,
    ToolDefinition,
)
from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
from nahida_bot.agent.tokenization import Tokenizer
from nahida_bot.core.config import MultimodalConfig
from nahida_bot.core.context import current_attachments, current_session, SessionContext
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.plugins.base import InboundAttachment, MediaDownloadResult
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry


_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _VisionProvider(ChatProvider):
    name = "vision"

    def __init__(self) -> None:
        self.messages: list[ContextMessage] = []

    @property
    def tokenizer(self) -> Tokenizer | None:
        return None

    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        self.messages = messages
        return ProviderResponse(content="vision description")


class _MemoryRecord:
    def __init__(self, turn: Any) -> None:
        self.turn = turn


class _Turn:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.role = "user"
        self.content = "image"
        self.source = "user_input"
        self.metadata = metadata


class _Memory:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata

    async def get_recent(self, session_id: str, limit: int) -> list[_MemoryRecord]:
        return [_MemoryRecord(_Turn(self.metadata))]


class _DownloadChannel:
    channel_id = "telegram"

    def __init__(self, path: str) -> None:
        self.path = path
        self.calls: list[str] = []

    async def download_media(
        self, file_id: str, destination: str | None = None
    ) -> MediaDownloadResult:
        self.calls.append(file_id)
        return MediaDownloadResult(
            path=self.path,
            mime_type="image/png",
            file_size=Path(self.path).stat().st_size,
        )


class _ChannelRegistry:
    def __init__(self, channel: _DownloadChannel) -> None:
        self.channel = channel

    def get(self, platform: str) -> _DownloadChannel | None:
        return self.channel if platform == self.channel.channel_id else None


def _runner(tmp_path: Path, provider: _VisionProvider) -> SessionRunner:
    slot = ProviderSlot(
        id="vision",
        provider=provider,
        context_builder=ContextBuilder(),
        default_model="vision-model",
        capabilities_by_model={
            "vision-model": ModelCapabilities(image_input=True),
        },
    )
    return SessionRunner(
        provider_manager=ProviderManager([slot], default_id="vision"),
        multimodal_config=MultimodalConfig(
            image_fallback_mode="tool",
            image_fallback_provider="vision",
        ),
        media_resolver=MediaResolver(
            cache=MediaCache(tmp_path / "media_cache"),
            policy=MediaPolicy(),
        ),
    )


@pytest.mark.asyncio
async def test_image_understand_uses_current_turn_attachment(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    image_path.write_bytes(_PNG_1X1)
    provider = _VisionProvider()
    runner = _runner(tmp_path, provider)

    token = current_attachments.set(
        (
            InboundAttachment(
                kind="image",
                platform_id="img_current",
                path=str(image_path),
                mime_type="image/png",
            ),
        )
    )
    try:
        result = await runner.handle_image_understand_tool(media_id="latest")
    finally:
        current_attachments.reset(token)

    assert result == "vision description"
    assert provider.messages[0].parts[1].type == "image_base64"
    assert provider.messages[0].parts[1].media_id == "img_current"


@pytest.mark.asyncio
async def test_image_understand_finds_history_attachment(tmp_path: Path) -> None:
    image_path = tmp_path / "history.png"
    image_path.write_bytes(_PNG_1X1)
    provider = _VisionProvider()
    metadata = {
        "attachments": [
            {
                "kind": "image",
                "platform_id": "img_history",
                "path": str(image_path),
                "mime_type": "image/png",
            }
        ]
    }
    runner = _runner(tmp_path, provider)
    runner.memory = cast(Any, _Memory(metadata))

    session_token = current_session.set(
        SessionContext(platform="telegram", chat_id="c1", session_id="s1")
    )
    try:
        result = await runner.handle_image_understand_tool(media_id="img_history")
    finally:
        current_session.reset(session_token)

    assert result == "vision description"
    assert provider.messages[0].parts[1].media_id == "img_history"


def test_collect_tools_does_not_duplicate_registered_image_tool() -> None:
    async def handler(*, media_id: str, question: str = "") -> str:
        return media_id + question

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="image_understand",
            description="Analyze image",
            parameters={"type": "object"},
            handler=handler,
            plugin_id="builtin",
        )
    )
    runner = SessionRunner(
        tool_registry=registry,
        multimodal_config=MultimodalConfig(image_fallback_mode="tool"),
    )

    tools = runner._collect_tools(
        None, capabilities=ModelCapabilities(image_input=False)
    )

    assert [tool.name for tool in tools].count("image_understand") == 1


@pytest.mark.asyncio
async def test_platform_download_materializes_opaque_telegram_image(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "telegram.png"
    image_path.write_bytes(_PNG_1X1)
    channel = _DownloadChannel(str(image_path))
    runner = SessionRunner(
        media_resolver=MediaResolver(
            cache=MediaCache(tmp_path / "media_cache"),
            policy=MediaPolicy(),
        ),
        channel_registry=cast(Any, _ChannelRegistry(channel)),
    )

    session_token = current_session.set(
        SessionContext(platform="telegram", chat_id="c1", session_id="s1")
    )
    try:
        parts = await runner._build_user_parts(
            "what is this",
            [InboundAttachment(kind="image", platform_id="telegram_file")],
            capabilities=ModelCapabilities(image_input=True, max_image_count=1),
        )
    finally:
        current_session.reset(session_token)

    assert channel.calls == ["telegram_file"]
    assert [part.type for part in parts] == ["text", "image_base64"]


@pytest.mark.asyncio
async def test_nonvision_fallback_handles_image_after_other_attachment() -> None:
    runner = SessionRunner(
        multimodal_config=MultimodalConfig(image_fallback_mode="auto")
    )

    parts = await runner._build_user_parts(
        "describe",
        [
            InboundAttachment(kind="file", platform_id="doc"),
            InboundAttachment(
                kind="image",
                platform_id="img",
                alt_text="fallback description",
            ),
        ],
        capabilities=ModelCapabilities(image_input=False),
    )

    assert [part.type for part in parts] == ["text", "image_description"]
    assert parts[1].text == "fallback description"


@pytest.mark.asyncio
async def test_model_max_image_bytes_degrades_oversized_image(tmp_path: Path) -> None:
    image_path = tmp_path / "large.png"
    image_path.write_bytes(_PNG_1X1)
    runner = SessionRunner(
        media_resolver=MediaResolver(
            cache=MediaCache(tmp_path / "media_cache"),
            policy=MediaPolicy(max_image_bytes=1024),
        )
    )

    parts = await runner._build_user_parts(
        "describe",
        [
            InboundAttachment(
                kind="image",
                platform_id="img",
                path=str(image_path),
                mime_type="image/png",
                alt_text="too large",
            )
        ],
        capabilities=ModelCapabilities(
            image_input=True,
            max_image_count=1,
            max_image_bytes=1,
        ),
    )

    assert [part.type for part in parts] == ["text", "image_description"]
    assert parts[1].text == "too large"
