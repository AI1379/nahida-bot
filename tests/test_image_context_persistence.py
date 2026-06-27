"""Tests for image context across the inbound boundary and across turns (#28).

Covers:
- Layer 2: ``_build_vision_parts`` never drops an image silently — an
  unresolvable image becomes an explicit "image unavailable" notice.
- Layer 4: image references survive transcript persistence and are rebuilt on
  replay, so a user image seen on turn 1 is still present in the context when
  turn 2 fires (the cross-turn loss half of #28).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.agent.media.cache import MediaCache
from nahida_bot.agent.media.resolver import MediaPolicy, MediaResolver
from nahida_bot.core.session_runner import (
    SessionRunner,
    _IMAGE_UNAVAILABLE_NOTICE,
)
from nahida_bot.plugins.base import InboundAttachment

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _runner(tmp_path: Path) -> SessionRunner:
    return SessionRunner(
        media_resolver=MediaResolver(
            cache=MediaCache(tmp_path / "media_cache"),
            policy=MediaPolicy(),
        )
    )


# ---------------------------------------------------------------------------
# Layer 2 — no silent image drop in _build_vision_parts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_vision_parts_unresolvable_image_becomes_notice(
    tmp_path: Path,
) -> None:
    """An image with no payload must surface a notice, not vanish (#28)."""
    runner = _runner(tmp_path)
    attachment = InboundAttachment(kind="image", platform_id="ghost")

    with patch("nahida_bot.core.session_runner.logger") as mock_logger:
        parts = await runner._build_vision_parts(
            "hello",
            [attachment],
            max_count=4,
            max_bytes=10 * 1024 * 1024,
            supported=("image/png", "image/jpeg", "image/webp"),
        )

    # Text part for the user message + the unavailable notice.
    notice = [p for p in parts if p.type == "image_description"]
    assert len(notice) == 1
    assert notice[0].text == _IMAGE_UNAVAILABLE_NOTICE
    assert notice[0].media_id == "ghost"
    mock_logger.warning.assert_any_call(
        "session_runner.image_dropped",
        media_id="ghost",
        source="description_only",
        reason="no_payload",
    )


@pytest.mark.asyncio
async def test_build_vision_parts_keeps_real_image(tmp_path: Path) -> None:
    """Regression guard: a resolvable image still yields a real image part."""
    img_path = tmp_path / "real.png"
    img_path.write_bytes(_PNG_1X1)
    runner = _runner(tmp_path)
    attachment = InboundAttachment(
        kind="image",
        platform_id="real_1",
        path=str(img_path),
        mime_type="image/png",
    )

    parts = await runner._build_vision_parts(
        "hello",
        [attachment],
        max_count=4,
        max_bytes=10 * 1024 * 1024,
        supported=("image/png", "image/jpeg", "image/webp"),
    )

    assert any(p.type == "image_base64" and p.data for p in parts)


# ---------------------------------------------------------------------------
# Layer 4 (persist side) — _attach_image_refs_to_transcript
# ---------------------------------------------------------------------------


def _user_msg(content: str = "hi") -> ContextMessage:
    return ContextMessage(
        role="user",
        source="user_input",
        content=content,
        parts=[ContextPart(type="text", text=content)],
    )


def test_attach_image_refs_stamps_first_user_message() -> None:
    refs = [
        {
            "kind": "image",
            "platform_id": "p1",
            "path": "/x.png",
            "mime_type": "image/png",
        }
    ]
    ordered = [
        _user_msg(),
        ContextMessage(role="assistant", source="assistant", content="ok"),
    ]
    out = SessionRunner._attach_image_refs_to_transcript(ordered, refs)

    assert out[0].metadata["attachments"] == refs
    # Only the first user message is stamped; the rest pass through untouched.
    assert out[1].metadata is None
    # Original list is not mutated.
    assert ordered[0].metadata is None


def test_attach_image_refs_filters_non_image_kinds() -> None:
    refs = [
        {"kind": "file", "platform_id": "f1"},
        {"kind": "image", "platform_id": "p1", "path": "/x.png"},
    ]
    out = SessionRunner._attach_image_refs_to_transcript([_user_msg()], refs)
    assert [r["platform_id"] for r in out[0].metadata["attachments"]] == ["p1"]


def test_attach_image_refs_noop_without_refs() -> None:
    ordered = [_user_msg()]
    assert SessionRunner._attach_image_refs_to_transcript(ordered, None) is ordered
    assert SessionRunner._attach_image_refs_to_transcript(ordered, []) is ordered


# ---------------------------------------------------------------------------
# Layer 4 (replay side) — _merge_replay_image_parts / _reconstruct_parts_for_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_replay_image_parts_rebuilds_image(tmp_path: Path) -> None:
    """A replayed user turn with an image ref rehydrates a real image part (#28)."""
    img_path = tmp_path / "hist.png"
    img_path.write_bytes(_PNG_1X1)
    runner = _runner(tmp_path)
    metadata: dict[str, Any] = {
        "attachments": [
            {
                "kind": "image",
                "platform_id": "hist_1",
                "path": str(img_path),
                "mime_type": "image/png",
            }
        ]
    }
    messages = [
        ContextMessage(
            role="user",
            source="user_input",
            content="see this",
            parts=[ContextPart(type="text", text="see this")],
            metadata=metadata,
        )
    ]

    out = await runner._merge_replay_image_parts(messages)

    image_parts = [p for p in out[0].parts if p.type == "image_base64"]
    assert len(image_parts) == 1
    assert image_parts[0].data
    # Original text part is preserved alongside the rebuilt image.
    assert any(p.type == "text" for p in out[0].parts)


@pytest.mark.asyncio
async def test_merge_replay_image_parts_skips_non_user_and_empty(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    assistant = ContextMessage(
        role="assistant", source="assistant", content="ok", parts=[]
    )
    user_no_imgs = _user_msg("hey")  # no metadata → nothing to rebuild

    out = await runner._merge_replay_image_parts([assistant, user_no_imgs])

    assert out[0] is assistant
    assert out[1].parts == [_user_msg("hey").parts[0]]


@pytest.mark.asyncio
async def test_reconstruct_parts_for_history_unavailable_notice(tmp_path: Path) -> None:
    """An historical image with no durable path/url becomes the unavailable notice."""
    runner = _runner(tmp_path)
    metadata = {"attachments": [{"kind": "image", "platform_id": "lost_1"}]}
    parts = await runner._reconstruct_parts_for_history(metadata)
    assert len(parts) == 1
    assert parts[0].type == "image_description"
    assert parts[0].text == _IMAGE_UNAVAILABLE_NOTICE
