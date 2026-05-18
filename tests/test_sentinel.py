"""Unit tests for nahida_bot.core.sentinel."""

from nahida_bot.core.sentinel import (
    SENTINEL_HEARTBEAT_OK,
    SENTINEL_NO_REPLY,
    SentinelResult,
    detect_sentinel,
)


class TestExactMatch:
    def test_no_reply_upper(self):
        r = detect_sentinel("NO_REPLY")
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="")

    def test_no_reply_lower(self):
        r = detect_sentinel("no_reply")
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="")

    def test_no_reply_mixed_case(self):
        r = detect_sentinel("No_Reply")
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="")

    def test_no_reply_with_whitespace(self):
        r = detect_sentinel("  NO_REPLY  ")
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="")

    def test_heartbeat_ok_upper(self):
        r = detect_sentinel("HEARTBEAT_OK")
        assert r == SentinelResult(action=SENTINEL_HEARTBEAT_OK, text="")

    def test_heartbeat_ok_lower(self):
        r = detect_sentinel("heartbeat_ok")
        assert r == SentinelResult(action=SENTINEL_HEARTBEAT_OK, text="")

    def test_heartbeat_ok_with_whitespace(self):
        r = detect_sentinel("  HEARTBEAT_OK  \n")
        assert r == SentinelResult(action=SENTINEL_HEARTBEAT_OK, text="")


class TestJsonEnvelope:
    def test_no_reply_json(self):
        r = detect_sentinel('{"action": "NO_REPLY"}')
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="")

    def test_no_reply_json_with_whitespace(self):
        r = detect_sentinel(' { "action" : "NO_REPLY" } ')
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="")

    def test_heartbeat_ok_json(self):
        r = detect_sentinel('{"action":"HEARTBEAT_OK"}')
        assert r == SentinelResult(action=SENTINEL_HEARTBEAT_OK, text="")

    def test_json_invalid_action(self):
        r = detect_sentinel('{"action":"OTHER"}')
        assert r.action is None

    def test_json_not_object(self):
        r = detect_sentinel('["NO_REPLY"]')
        assert r.action is None

    def test_json_missing_action_key(self):
        r = detect_sentinel('{"type":"NO_REPLY"}')
        assert r.action is None


class TestTrailingStrip:
    def test_trailing_no_reply(self):
        r = detect_sentinel("Some text\nNO_REPLY")
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="Some text")

    def test_trailing_no_reply_with_whitespace(self):
        r = detect_sentinel("Some text\n  NO_REPLY  ")
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="Some text")

    def test_trailing_heartbeat_ok(self):
        r = detect_sentinel("All good\nHEARTBEAT_OK")
        assert r == SentinelResult(action=SENTINEL_HEARTBEAT_OK, text="All good")

    def test_trailing_multiline_text(self):
        r = detect_sentinel("Line 1\nLine 2\nNO_REPLY")
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="Line 1\nLine 2")

    def test_trailing_only_sentinel_means_full_suppress(self):
        r = detect_sentinel("\nNO_REPLY")
        assert r == SentinelResult(action=SENTINEL_NO_REPLY, text="")

    def test_trailing_strip_remaining_is_exact(self):
        r = detect_sentinel("NO_REPLY\nNO_REPLY")
        assert r.action == SENTINEL_NO_REPLY
        assert r.text == "NO_REPLY"

    def test_sentinel_not_at_end_not_matched(self):
        r = detect_sentinel("NO_REPLY\nSome text")
        assert r.action is None


class TestNormalText:
    def test_plain_text(self):
        r = detect_sentinel("Hello, how are you?")
        assert r.action is None
        assert r.text == "Hello, how are you?"

    def test_sentinel_inline(self):
        r = detect_sentinel("NO_REPLY is a feature")
        assert r.action is None

    def test_sentinel_in_sentence(self):
        r = detect_sentinel("The token NO_REPLY suppresses replies")
        assert r.action is None

    def test_empty_string(self):
        r = detect_sentinel("")
        assert r.action is None
        assert r.text == ""

    def test_whitespace_only(self):
        r = detect_sentinel("   ")
        assert r.action is None

    def test_none_like_input(self):
        r = detect_sentinel("\n\n")
        assert r.action is None
