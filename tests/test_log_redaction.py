"""Tests for WebUI log normalization and conservative redaction."""

from nahida_bot.gateway.services.log_redaction import REDACTED, to_log_entry


def test_log_entry_redacts_exact_sensitive_fields_and_known_value_patterns() -> None:
    entry = to_log_entry(
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "level": "INFO",
            "logger": "nahida.test",
            "event": "request sent with Authorization: Bearer abcdefghijklmnop",
            "api_key": "plain-api-key",
            "nested": {
                "password": "pw",
                "message": "provider key sk-1234567890abcdef",
            },
        }
    )

    assert entry.event == f"request sent with Authorization: Bearer {REDACTED}"
    assert entry.fields["api_key"] == REDACTED
    assert entry.fields["nested"]["password"] == REDACTED
    assert entry.fields["nested"]["message"] == f"provider key {REDACTED}"


def test_log_entry_avoids_broad_token_and_key_substring_redaction() -> None:
    entry = to_log_entry(
        {
            "level": "DEBUG",
            "event": "usage updated",
            "input_tokens": 100,
            "output_tokens": 20,
            "token_usage": {"input_tokens": 100, "output_tokens": 20},
            "session_key": "telegram:private:123",
            "cache_key": "normal-cache-key",
            "openai_api_key": "left-visible-unless-marked-sensitive",
        }
    )

    assert entry.fields["input_tokens"] == 100
    assert entry.fields["output_tokens"] == 20
    assert entry.fields["token_usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert entry.fields["session_key"] == "telegram:private:123"
    assert entry.fields["cache_key"] == "normal-cache-key"
    assert entry.fields["openai_api_key"] == "left-visible-unless-marked-sensitive"


def test_log_entry_always_has_frontend_fields_shape() -> None:
    entry = to_log_entry({"level": "WARNING", "event": "hello"})
    payload = entry.model_dump(mode="json")

    assert payload == {
        "timestamp": "",
        "level": "warning",
        "logger": "",
        "event": "hello",
        "fields": {},
    }
