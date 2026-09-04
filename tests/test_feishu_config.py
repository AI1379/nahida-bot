"""Tests for Feishu channel configuration parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nahida_bot.channels.feishu.config import parse_feishu_config


def test_defaults() -> None:
    config = parse_feishu_config(None)

    assert config.domain == "https://open.feishu.cn"
    assert config.group_trigger_mode == "mention"
    assert config.group_context_capture is False
    assert config.markdown_enabled is True
    assert config.outbound_mentions_enabled is True
    assert config.api_base == "https://open.feishu.cn/open-apis"
    assert config.is_international is False


def test_domain_normalization() -> None:
    assert (
        parse_feishu_config({"domain": "open.feishu.cn/"}).domain
        == "https://open.feishu.cn"
    )
    international = parse_feishu_config({"domain": "https://open.larksuite.com/"})
    assert international.is_international is True
    assert international.api_base == "https://open.larksuite.com/open-apis"


def test_allowed_id_lists_coerce_scalars_and_strip() -> None:
    config = parse_feishu_config(
        {"allowed_chats": [" oc_1 ", "oc_2"], "allowed_users": "ou_9"}
    )

    assert config.allowed_chats == ["oc_1", "oc_2"]
    assert config.allowed_users == ["ou_9"]


def test_invalid_values_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_feishu_config({"group_trigger_mode": "whenever"})
    with pytest.raises(ValidationError):
        parse_feishu_config({"max_text_length": 10})
    with pytest.raises(ValidationError):
        parse_feishu_config({"command_prefix": ""})
