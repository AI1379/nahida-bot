"""Tests for the GitHub channel configuration model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nahida_bot.channels.github.config import GitHubChannelConfig, parse_github_config


def test_defaults() -> None:
    config = GitHubChannelConfig()

    assert config.token == ""
    assert config.webhook_path == "github"
    assert config.api_base_url == "https://api.github.com"
    assert config.bot_login == ""
    assert config.group_trigger_mode == "mention"
    assert config.group_context_capture is False
    assert config.include_reasoning is False
    assert config.lifecycle_as_context is True
    assert config.enable_issue_tools is True
    assert config.max_comment_length == 65536


def test_parse_from_raw_mapping() -> None:
    config = parse_github_config(
        {
            "token": " ghp_test ",
            "webhook_secret": " s3cret ",
            "webhook_path": "github-test",
            "allowed_repos": ["AI1379/nahida-bot", " other/org ", ""],
            "allowed_senders": ["Arendellian13"],
        }
    )

    assert config.token == "ghp_test"
    assert config.webhook_secret == "s3cret"
    assert config.webhook_path == "github-test"
    assert config.allowed_repos == ["AI1379/nahida-bot", "other/org"]
    assert config.allowed_senders == ["Arendellian13"]


def test_parse_empty_raw_returns_defaults() -> None:
    config = parse_github_config(None)

    assert config.api_base_url == "https://api.github.com"


def test_api_base_url_normalized() -> None:
    trailing = GitHubChannelConfig(api_base_url="https://ghe.example.com/api/v3/")
    bare = GitHubChannelConfig(api_base_url="")
    host_only = GitHubChannelConfig(api_base_url="ghe.example.com")

    assert trailing.api_base_url == "https://ghe.example.com/api/v3"
    assert bare.api_base_url == "https://api.github.com"
    assert host_only.api_base_url == "https://ghe.example.com"


def test_allow_lists_match_case_insensitive() -> None:
    config = GitHubChannelConfig(
        allowed_repos=["AI1379/nahida-bot"],
        allowed_senders=["Arendellian13"],
    )

    assert config.repo_allowed("ai1379/nahida-bot")
    assert config.repo_allowed("ai1379/NAHIDA-BOT")
    assert not config.repo_allowed("ai1379/other")
    assert config.sender_allowed("arendellian13")
    assert not config.sender_allowed("someone-else")


def test_empty_allow_lists_allow_everything() -> None:
    config = GitHubChannelConfig()

    assert config.repo_allowed("anything/anything")
    assert config.sender_allowed("anyone")


def test_repo_org_wildcard_matching() -> None:
    config = GitHubChannelConfig(allowed_repos=["AI1379/*", "other/repo"])

    assert config.repo_allowed("AI1379/nahida-bot")
    assert config.repo_allowed("ai1379/anything")
    assert config.repo_allowed("other/repo")
    assert not config.repo_allowed("AI1379X/repo")  # 前缀不得误命中
    assert not config.repo_allowed("someone/repo")
    assert not config.repo_allowed("AI1379")


def test_allowed_orgs_coerced_and_lowercased() -> None:
    config = GitHubChannelConfig(allowed_orgs=[" AI1379 ", "", "Other-Org"])

    assert config.allowed_orgs == ["AI1379", "Other-Org"]
    assert config.allowed_org_keys == {"ai1379", "other-org"}


def test_coerce_id_list_accepts_scalar() -> None:
    config = GitHubChannelConfig(allowed_repos="ai1379/nahida-bot")

    assert config.allowed_repos == ["ai1379/nahida-bot"]


def test_invalid_trigger_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        GitHubChannelConfig(group_trigger_mode="sometimes")


def test_invalid_max_comment_length_rejected() -> None:
    with pytest.raises(ValidationError):
        GitHubChannelConfig(max_comment_length=10)
