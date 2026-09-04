"""Tests for outbound mention token parsing."""

from __future__ import annotations

from nahida_bot.core.outbound_mentions import (
    extract_mention_ids,
    parse_outbound_parts,
)


def test_parses_cq_at_token_mid_sentence() -> None:
    parts = parse_outbound_parts("[CQ:at,qq=123456] 你说得对")

    assert parts[0].is_mention
    assert parts[0].user_id == "123456"
    assert parts[0].raw == "[CQ:at,qq=123456]"
    assert parts[1].text == " 你说得对"
    assert not parts[1].is_mention


def test_parses_alias_token_forms() -> None:
    assert parse_outbound_parts("hi @[qq=42]")[1].user_id == "42"
    assert parse_outbound_parts("hi @[user_id=42]")[1].user_id == "42"


def test_parses_multiple_mixed_tokens_in_order() -> None:
    parts = parse_outbound_parts(
        "@[qq=1] 先说这个，然后 [CQ:at,qq=2] 那个 @[user_id=1]"
    )

    assert [part.user_id for part in parts if part.is_mention] == [
        "1",
        "2",
        "1",
    ]
    assert parts[0].text == ""
    assert parts[1].text == " 先说这个，然后 "


def test_text_without_tokens_is_single_part() -> None:
    parts = parse_outbound_parts("plain message")

    assert len(parts) == 1
    assert parts[0].text == "plain message"
    assert not parts[0].is_mention


def test_non_numeric_targets_stay_literal() -> None:
    parts = parse_outbound_parts("[CQ:at,qq=all] 大家看过来")

    assert len(parts) == 1
    assert parts[0].text == "[CQ:at,qq=all] 大家看过来"


def test_empty_text_yields_no_parts() -> None:
    assert parse_outbound_parts("") == []


def test_extract_ids_dedupes_and_caps_in_order() -> None:
    text = "@[qq=3] [CQ:at,qq=1] @[qq=3] [CQ:at,qq=2] [CQ:at,qq=9]"

    assert extract_mention_ids(text, limit=3) == ["3", "1", "2"]
    assert extract_mention_ids(text, limit=10) == ["3", "1", "2", "9"]


def test_extract_ids_empty_when_no_tokens() -> None:
    assert extract_mention_ids("no tokens here", limit=3) == []


def test_parses_feishu_open_id_tokens() -> None:
    open_id = "ou_84aad35d084aa403a838cf73ee18467"
    parts = parse_outbound_parts(f"[CQ:at,qq={open_id}] 看这里")

    assert parts[0].is_mention
    assert parts[0].user_id == open_id
    assert parts[1].text == " 看这里"

    assert parse_outbound_parts(f"hi @[user_id={open_id}]")[1].user_id == open_id
    assert extract_mention_ids(f"@[qq={open_id}] [CQ:at,qq=7]", limit=5) == [
        open_id,
        "7",
    ]


def test_malformed_open_id_prefixes_stay_literal() -> None:
    # ou_ must be followed by alphanumerics; anything else stays literal.
    parts = parse_outbound_parts("[CQ:at,qq=ou_] and [CQ:at,qq=ox_123]")

    assert len(parts) == 1
    assert parts[0].text == "[CQ:at,qq=ou_] and [CQ:at,qq=ox_123]"
