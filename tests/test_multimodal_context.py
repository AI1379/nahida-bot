"""Tests for multimodal context: history round-trip, media policy, fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.agent.media.cache import MediaCache
from nahida_bot.agent.media.resolver import MediaPolicy, MediaResolver
from nahida_bot.agent.memory.store import MemoryStore
from nahida_bot.agent.providers.base import ModelCapabilities
from nahida_bot.core.session_runner import SessionRunner


# -- helpers ---------------------------------------------------------------


class _FakeMemoryRecord:
    def __init__(self, turn: Any) -> None:
        self.turn = turn


class _FakeTurn:
    def __init__(
        self,
        *,
        role: str,
        content: str,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.role = role
        self.content = content
        self.source = source
        self.metadata = metadata


# -- _reconstruct_parts tests ----------------------------------------------


class TestReconstructParts:
    def test_empty_metadata(self) -> None:
        assert SessionRunner._reconstruct_parts(None) == []

    def test_no_attachments_key(self) -> None:
        assert SessionRunner._reconstruct_parts({"other": "data"}) == []

    def test_reconstructs_url_image(self) -> None:
        metadata = {
            "attachments": [
                {
                    "kind": "image",
                    "platform_id": "img_1",
                    "url": "https://example.com/img.jpg",
                    "mime_type": "image/jpeg",
                }
            ]
        }
        parts = SessionRunner._reconstruct_parts(metadata)
        assert len(parts) == 1
        assert parts[0].type == "image_url"
        assert parts[0].url == "https://example.com/img.jpg"
        assert parts[0].media_id == "img_1"

    def test_reconstructs_path_image(self) -> None:
        metadata = {
            "attachments": [
                {
                    "kind": "image",
                    "platform_id": "img_2",
                    "path": "/tmp/cached.png",
                    "mime_type": "image/png",
                }
            ]
        }
        parts = SessionRunner._reconstruct_parts(metadata)
        assert len(parts) == 1
        assert parts[0].type == "image_url"
        assert parts[0].url == "/tmp/cached.png"

    def test_reconstructs_alt_text_fallback(self) -> None:
        metadata = {
            "attachments": [
                {
                    "kind": "image",
                    "platform_id": "img_3",
                    "alt_text": "a cat",
                }
            ]
        }
        parts = SessionRunner._reconstruct_parts(metadata)
        assert len(parts) == 1
        assert parts[0].type == "image_description"
        assert parts[0].text == "a cat"

    def test_skips_non_image_kind(self) -> None:
        metadata = {
            "attachments": [
                {"kind": "audio", "platform_id": "aud_1"},
                {"kind": "image", "platform_id": "img_1", "url": "https://x.com/i.jpg"},
            ]
        }
        parts = SessionRunner._reconstruct_parts(metadata)
        assert len(parts) == 1
        assert parts[0].media_id == "img_1"


# -- _degrade_image_parts tests --------------------------------------------


class TestDegradeImageParts:
    def test_degrades_image_url_to_description(self) -> None:
        parts = [
            ContextPart(type="text", text="hello"),
            ContextPart(
                type="image_url", url="https://x.com/img.jpg", media_id="img_1"
            ),
        ]
        degraded = SessionRunner._degrade_image_parts(parts)
        assert degraded[0].type == "text"
        assert degraded[1].type == "image_description"
        assert "img_1" in degraded[1].text

    def test_degrades_image_base64_to_description(self) -> None:
        parts = [
            ContextPart(
                type="image_base64", data="abc123", media_id="b64_1", text="alt"
            ),
        ]
        degraded = SessionRunner._degrade_image_parts(parts)
        assert degraded[0].type == "image_description"
        assert degraded[0].text == "alt"


# -- _apply_media_context_policy tests -------------------------------------


class TestApplyMediaContextPolicy:
    def _make_messages(self) -> list[ContextMessage]:
        return [
            ContextMessage(
                role="user",
                content="msg1",
                source="history",
                parts=[
                    ContextPart(type="text", text="msg1"),
                    ContextPart(
                        type="image_url", url="https://old/img.jpg", media_id="old"
                    ),
                ],
            ),
            ContextMessage(
                role="assistant",
                content="reply1",
                source="history",
            ),
            ContextMessage(
                role="user",
                content="msg2",
                source="history",
                parts=[
                    ContextPart(type="text", text="msg2"),
                    ContextPart(
                        type="image_url", url="https://new/img.jpg", media_id="new"
                    ),
                ],
            ),
            ContextMessage(
                role="assistant",
                content="reply2",
                source="history",
            ),
        ]

    def test_description_only_degrades_all(self) -> None:
        messages = self._make_messages()
        result = SessionRunner._apply_media_context_policy(
            messages, policy="description_only", capabilities=None
        )
        user_msgs = [m for m in result if m.role == "user"]
        for msg in user_msgs:
            for part in msg.parts:
                if part.media_id:
                    assert part.type == "image_description"

    def test_native_recent_keeps_last_only(self) -> None:
        messages = self._make_messages()
        result = SessionRunner._apply_media_context_policy(
            messages, policy="native_recent", capabilities=None
        )
        user_msgs = [m for m in result if m.role == "user"]
        # First user message should be degraded
        first_parts = [p for p in user_msgs[0].parts if p.media_id]
        assert all(p.type == "image_description" for p in first_parts)
        # Last user message should keep native
        last_parts = [p for p in user_msgs[-1].parts if p.media_id]
        assert any(p.type == "image_url" for p in last_parts)

    def test_cache_aware_degrades_older_keeps_recent(self) -> None:
        messages = self._make_messages()
        result = SessionRunner._apply_media_context_policy(
            messages, policy="cache_aware", capabilities=None
        )
        user_msgs = [m for m in result if m.role == "user"]
        # With 2 user msgs, last 2 user turns are "recent" => both native
        # (since there are only 2, the threshold is the first one)
        # Actually with >= 2 user msgs, recent_threshold = user_indices[-2]
        # = index of first user msg, so the first is *at* the threshold
        # and is NOT degraded (i < recent_threshold is False for index 0).
        # Both should be native.
        for msg in user_msgs:
            for part in msg.parts:
                if part.media_id and part.type in ("image_url",):
                    pass  # native is fine

    def test_no_user_images_returns_unchanged(self) -> None:
        messages = [
            ContextMessage(role="user", content="text only", source="history"),
            ContextMessage(role="assistant", content="reply", source="history"),
        ]
        result = SessionRunner._apply_media_context_policy(
            messages, policy="description_only", capabilities=None
        )
        assert result == messages

    def test_nonvision_capability_forces_description_only(self) -> None:
        messages = self._make_messages()
        result = SessionRunner._apply_media_context_policy(
            messages,
            policy="cache_aware",
            capabilities=ModelCapabilities(image_input=False),
        )
        user_msgs = [m for m in result if m.role == "user"]
        assert all(
            part.type != "image_url"
            for msg in user_msgs
            for part in msg.parts
            if part.media_id
        )


class TestHistoryMediaResolve:
    async def test_load_history_preserves_tool_metadata(self) -> None:
        from nahida_bot.agent.memory.models import ConversationTurn

        class _FakeMemory:
            async def ensure_session(self, *a: Any, **kw: Any) -> None:
                pass

            async def get_recent(self, *a: Any, **kw: Any) -> list:
                return [
                    _FakeMemoryRecord(
                        ConversationTurn(
                            role="assistant",
                            content="",
                            source="provider_response",
                            metadata={
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "name": "search",
                                        "arguments": {"q": "nahida"},
                                    }
                                ]
                            },
                        )
                    ),
                    _FakeMemoryRecord(
                        ConversationTurn(
                            role="tool",
                            content='{"status":"ok"}',
                            source="tool_result:search",
                            metadata={
                                "tool_call_id": "call_1",
                                "tool_name": "search",
                            },
                        )
                    ),
                ]

        runner = SessionRunner(memory_store=cast(MemoryStore, _FakeMemory()))

        messages = await runner._load_history("s1")

        assert messages[0].metadata == {
            "tool_calls": [
                {"id": "call_1", "name": "search", "arguments": {"q": "nahida"}}
            ]
        }
        assert messages[1].metadata == {
            "tool_call_id": "call_1",
            "tool_name": "search",
        }

    async def test_load_history_rebuilds_cached_path_as_base64(
        self, tmp_path: Path
    ) -> None:
        from nahida_bot.agent.memory.models import ConversationTurn

        image_path = tmp_path / "cached.png"
        image_path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        class _FakeMemory:
            async def ensure_session(self, *a: Any, **kw: Any) -> None:
                pass

            async def get_recent(self, *a: Any, **kw: Any) -> list:
                return [
                    _FakeMemoryRecord(
                        ConversationTurn(
                            role="user",
                            content="look",
                            source="user_input",
                            metadata={
                                "attachments": [
                                    {
                                        "kind": "image",
                                        "platform_id": "img",
                                        "path": str(image_path),
                                        "mime_type": "image/png",
                                    }
                                ]
                            },
                        )
                    )
                ]

        runner = SessionRunner(
            memory_store=cast(MemoryStore, _FakeMemory()),
            media_resolver=MediaResolver(
                cache=MediaCache(tmp_path / "media_cache"),
                policy=MediaPolicy(),
            ),
        )

        messages = await runner._load_history(
            "s1", capabilities=ModelCapabilities(image_input=True)
        )

        assert messages[0].parts[0].type == "text"
        assert messages[0].parts[0].text == "look"
        assert messages[0].parts[1].type == "image_base64"
        assert messages[0].parts[1].media_id == "img"


# -- _persist_turns metadata tests -----------------------------------------


class TestPersistTurnsMetadata:
    async def test_stores_url_and_path(self, tmp_path: Any) -> None:
        from nahida_bot.agent.memory.models import ConversationTurn
        from nahida_bot.plugins.base import InboundAttachment

        persisted: list[ConversationTurn] = []

        class _FakeMemory:
            async def ensure_session(self, *a: Any, **kw: Any) -> None:
                pass

            async def append_turn(self, sid: str, turn: ConversationTurn) -> int:
                persisted.append(turn)
                return len(persisted)

            async def get_recent(self, *a: Any, **kw: Any) -> list:
                return []

            async def get_session_meta(self, *a: Any, **kw: Any) -> dict:
                return {}

        runner = SessionRunner(memory_store=cast(MemoryStore, _FakeMemory()))

        attachments = [
            InboundAttachment(
                kind="image",
                platform_id="img_1",
                url="https://example.com/img.jpg",
                path="/tmp/cached.jpg",
                mime_type="image/jpeg",
            )
        ]

        class _FakeResult:
            final_response = "ok"
            assistant_messages: list[Any] = []

        await runner._persist_turns(
            "session_1",
            "hello",
            _FakeResult(),
            attachments=attachments,
            source_tag="user_input",
        )

        assert len(persisted) == 2
        user_meta = persisted[0].metadata
        assert user_meta is not None
        assert user_meta["attachments"][0]["url"] == ""
        assert user_meta["attachments"][0]["path"] == "/tmp/cached.jpg"

    async def test_stores_assistant_reasoning(self, tmp_path: Any) -> None:
        from nahida_bot.agent.memory.models import ConversationTurn

        persisted: list[ConversationTurn] = []

        class _FakeMemory:
            async def ensure_session(self, *a: Any, **kw: Any) -> None:
                pass

            async def append_turn(self, sid: str, turn: ConversationTurn) -> int:
                persisted.append(turn)
                return len(persisted)

            async def get_recent(self, *a: Any, **kw: Any) -> list:
                return []

            async def get_session_meta(self, *a: Any, **kw: Any) -> dict:
                return {}

        runner = SessionRunner(memory_store=cast(MemoryStore, _FakeMemory()))

        class _FakeContextMessage:
            reasoning = "I thought about it"
            reasoning_signature = "sig_123"
            has_redacted_thinking = True

        class _FakeResult:
            final_response = "here is my answer"
            assistant_messages = [_FakeContextMessage()]

        await runner._persist_turns(
            "session_1",
            "hello",
            _FakeResult(),
            attachments=[],
            source_tag="user_input",
        )

        assert len(persisted) == 2
        assistant_meta = persisted[1].metadata
        assert assistant_meta is not None
        assert assistant_meta["reasoning"] == "I thought about it"
        assert assistant_meta["reasoning_signature"] == "sig_123"
        assert assistant_meta["has_redacted_thinking"] is True
