"""Tests for ChatAddress and SessionKey parsing/construction."""

from __future__ import annotations

import pytest

from nahida_bot.core.chat_address import (
    ChatAddress,
    SessionKey,
    classify_session_key,
)


# ── ChatAddress construction ────────────────────────────────────────


class TestChatAddressConstruction:
    def test_typed_address_str(self) -> None:
        addr = ChatAddress(channel="milky", target_type="group", target_id="20001")
        assert str(addr) == "milky:group:20001"

    def test_typed_address_with_thread(self) -> None:
        addr = ChatAddress(
            channel="telegram", target_type="group", target_id="-100", thread_id="42"
        )
        assert str(addr) == "telegram:group:-100:42"

    def test_unknown_address_str(self) -> None:
        addr = ChatAddress(channel="milky", target_type="unknown", target_id="10001")
        assert str(addr) == "milky:unknown:10001"

    def test_is_typed_true(self) -> None:
        for tt in ("private", "group", "channel", "thread"):
            addr = ChatAddress(channel="x", target_type=tt, target_id="1")
            assert addr.is_typed

    def test_is_typed_false(self) -> None:
        addr = ChatAddress(channel="x", target_type="unknown", target_id="1")
        assert not addr.is_typed

    def test_chat_key_returns_str(self) -> None:
        addr = ChatAddress(channel="milky", target_type="group", target_id="20001")
        assert addr.chat_key == "milky:group:20001"

    def test_legacy_key(self) -> None:
        addr = ChatAddress(channel="milky", target_type="group", target_id="20001")
        assert addr.legacy_key == "milky:20001"

    def test_empty_channel_raises(self) -> None:
        with pytest.raises(ValueError, match="channel"):
            ChatAddress(channel="", target_type="group", target_id="1")

    def test_empty_target_id_raises(self) -> None:
        with pytest.raises(ValueError, match="target_id"):
            ChatAddress(channel="milky", target_type="group", target_id="")

    def test_frozen(self) -> None:
        addr = ChatAddress(channel="milky", target_type="group", target_id="1")
        with pytest.raises(AttributeError):
            addr.channel = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ChatAddress(channel="milky", target_type="group", target_id="1")
        b = ChatAddress(channel="milky", target_type="group", target_id="1")
        assert a == b

    def test_inequality(self) -> None:
        a = ChatAddress(channel="milky", target_type="group", target_id="1")
        b = ChatAddress(channel="milky", target_type="private", target_id="1")
        assert a != b


# ── ChatAddress.parse ───────────────────────────────────────────────


class TestChatAddressParse:
    def test_parse_typed_3seg(self) -> None:
        addr = ChatAddress.parse("milky:group:20001")
        assert addr.channel == "milky"
        assert addr.target_type == "group"
        assert addr.target_id == "20001"
        assert addr.thread_id == ""
        assert addr.is_typed

    def test_parse_typed_4seg_with_thread(self) -> None:
        addr = ChatAddress.parse("telegram:group:-100:42")
        assert addr.target_type == "group"
        assert addr.target_id == "-100"
        assert addr.thread_id == "42"

    def test_parse_legacy_2seg(self) -> None:
        addr = ChatAddress.parse("milky:10001")
        assert addr.channel == "milky"
        assert addr.target_type == "unknown"
        assert addr.target_id == "10001"
        assert not addr.is_typed

    def test_parse_canonical_unknown(self) -> None:
        addr = ChatAddress.parse("milky:unknown:10001")
        assert addr.channel == "milky"
        assert addr.target_type == "unknown"
        assert addr.target_id == "10001"

    def test_unknown_round_trips(self) -> None:
        addr = ChatAddress(channel="milky", target_type="unknown", target_id="10001")
        assert ChatAddress.parse(str(addr)) == addr

    def test_parse_legacy_3seg_not_type(self) -> None:
        addr = ChatAddress.parse("milky:10001:abcd1234")
        assert addr.target_type == "unknown"
        assert addr.target_id == "10001"

    def test_parse_private(self) -> None:
        addr = ChatAddress.parse("telegram:private:123456")
        assert addr.target_type == "private"
        assert addr.is_typed

    def test_parse_channel_type(self) -> None:
        addr = ChatAddress.parse("telegram:channel:news")
        assert addr.target_type == "channel"

    def test_parse_thread_type(self) -> None:
        addr = ChatAddress.parse("discord:thread:99")
        assert addr.target_type == "thread"

    def test_parse_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            ChatAddress.parse("")

    def test_parse_single_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="too few"):
            ChatAddress.parse("onlyone")

    def test_parse_empty_channel_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty channel"):
            ChatAddress.parse(":10001")


# ── ChatAddress.from_inbound ────────────────────────────────────────


class TestChatAddressFromInbound:
    def test_explicit_chat_type_group(self) -> None:
        addr = ChatAddress.from_inbound("milky", "20001", chat_type="group")
        assert addr.target_type == "group"
        assert addr.is_typed

    def test_explicit_chat_type_private(self) -> None:
        addr = ChatAddress.from_inbound("milky", "10001", chat_type="private")
        assert addr.target_type == "private"

    def test_is_group_heuristic(self) -> None:
        addr = ChatAddress.from_inbound("milky", "20001", is_group=True)
        assert addr.target_type == "group"

    def test_unknown_when_no_info(self) -> None:
        addr = ChatAddress.from_inbound("milky", "10001")
        assert addr.target_type == "unknown"
        assert not addr.is_typed

    def test_explicit_chat_type_overrides_is_group(self) -> None:
        addr = ChatAddress.from_inbound(
            "milky", "20001", is_group=True, chat_type="private"
        )
        assert addr.target_type == "private"

    def test_explicit_unknown_respected(self) -> None:
        addr = ChatAddress.from_inbound("milky", "10001", chat_type="unknown")
        assert addr.target_type == "unknown"


# ── SessionKey ──────────────────────────────────────────────────────


class TestSessionKey:
    def test_base_key_no_suffix(self) -> None:
        addr = ChatAddress(channel="milky", target_type="group", target_id="20001")
        sk = SessionKey(address=addr)
        assert str(sk) == "milky:group:20001"

    def test_suffix_appended(self) -> None:
        addr = ChatAddress(channel="milky", target_type="group", target_id="20001")
        sk = SessionKey(address=addr, suffix="abcd1234")
        assert str(sk) == "milky:group:20001:abcd1234"

    def test_multi_segment_suffix(self) -> None:
        addr = ChatAddress(channel="milky", target_type="group", target_id="20001")
        sk = SessionKey(address=addr, suffix="cron:abc123")
        assert str(sk) == "milky:group:20001:cron:abc123"

    def test_is_derived(self) -> None:
        addr = ChatAddress(channel="milky", target_type="group", target_id="20001")
        assert not SessionKey(address=addr).is_derived
        assert SessionKey(address=addr, suffix="abc").is_derived

    def test_parse_typed_base(self) -> None:
        sk = SessionKey.parse("milky:group:20001")
        assert sk.address.target_type == "group"
        assert sk.address.target_id == "20001"
        assert sk.suffix == ""

    def test_parse_typed_with_suffix(self) -> None:
        sk = SessionKey.parse("milky:group:20001:abcd1234")
        assert sk.address.target_type == "group"
        assert sk.address.target_id == "20001"
        assert sk.suffix == "abcd1234"

    def test_parse_typed_with_multi_suffix(self) -> None:
        sk = SessionKey.parse("milky:group:20001:cron:abc123")
        assert sk.address.target_type == "group"
        assert sk.suffix == "cron:abc123"

    def test_parse_legacy_base(self) -> None:
        sk = SessionKey.parse("milky:10001")
        assert sk.address.target_type == "unknown"
        assert sk.address.target_id == "10001"
        assert sk.suffix == ""

    def test_parse_canonical_unknown_base(self) -> None:
        sk = SessionKey.parse("milky:unknown:10001")
        assert sk.address.target_type == "unknown"
        assert sk.address.target_id == "10001"
        assert sk.suffix == ""

    def test_parse_canonical_unknown_with_suffix(self) -> None:
        sk = SessionKey.parse("milky:unknown:10001:abcd1234")
        assert sk.address.target_type == "unknown"
        assert sk.address.target_id == "10001"
        assert sk.suffix == "abcd1234"

    def test_parse_legacy_with_suffix(self) -> None:
        sk = SessionKey.parse("milky:10001:abcd1234")
        assert sk.address.target_type == "unknown"
        assert sk.address.target_id == "10001"
        assert sk.suffix == "abcd1234"

    def test_parse_legacy_with_multi_suffix(self) -> None:
        sk = SessionKey.parse("milky:10001:cron:job42")
        assert sk.address.target_type == "unknown"
        assert sk.suffix == "cron:job42"

    def test_parse_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            SessionKey.parse("")

    def test_parse_single_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="too few"):
            SessionKey.parse("onlyone")


class TestClassifySessionKey:
    def test_typed_base(self) -> None:
        assert classify_session_key("milky:group:10001") == "typed"

    def test_typed_derived(self) -> None:
        assert classify_session_key("milky:group:10001:abcd1234") == "typed-derived"

    def test_legacy_base(self) -> None:
        assert classify_session_key("milky:10001") == "legacy"

    def test_legacy_derived(self) -> None:
        assert classify_session_key("milky:10001:abcd1234") == "legacy-derived"

    def test_invalid(self) -> None:
        assert classify_session_key("onlyone") == "invalid"
