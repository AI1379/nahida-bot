"""Configuration model for the GitHub channel plugin."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

GroupTriggerMode = Literal["none", "mention", "command", "always"]

_DEFAULT_API_BASE = "https://api.github.com"


class GitHubChannelConfig(BaseModel):
    """Runtime configuration for the GitHub channel.

    GitHub pushes repository events to a plugin-owned webhook endpoint and
    the channel talks back through the REST API under ``api_base_url``.
    """

    token: str = Field(
        default="",
        description="Personal access token (Bearer) for REST calls and self-identity.",
    )
    webhook_secret: str = Field(
        default="",
        description="Shared secret for x-hub-signature-256 webhook verification.",
    )
    webhook_path: str = Field(
        default="github",
        description="Webhook path under /webhooks/ that GitHub posts events to.",
    )
    api_base_url: str = Field(
        default=_DEFAULT_API_BASE,
        description="REST API base URL; override for GitHub Enterprise Server.",
    )
    bot_login: str = Field(
        default="",
        description=(
            "Bot account login for @mention detection. Empty = discovered at "
            "runtime via GET /user with the configured token."
        ),
    )
    allowed_repos: list[str] = Field(
        default_factory=list,
        description=(
            "Optional owner/name allow-list for webhook events and issue "
            "tools; 'org/*' wildcards cover every repository of an "
            "organization. Empty = every repository the token can access."
        ),
    )
    allowed_senders: list[str] = Field(
        default_factory=list,
        description=(
            "Optional login allow-list for webhook senders. Empty = not "
            "restricted by explicit list (see allowed_orgs). "
            "Case-insensitive; the bot's own login is always skipped."
        ),
    )
    allowed_orgs: list[str] = Field(
        default_factory=list,
        description=(
            "Optional organization allow-list: members of any listed org "
            "pass the sender filter automatically (checked via "
            "GET /orgs/:org/members/:user with a TTL cache). Empty = no "
            "org-based filtering."
        ),
    )

    command_prefix: str = Field(default="/", min_length=1)
    group_trigger_mode: GroupTriggerMode = Field(
        default="mention",
        description=(
            "How issue/PR messages trigger the bot: 'mention' responds only "
            "when the body @-mentions the bot login, 'command' to /commands."
        ),
    )
    group_context_capture: bool = Field(
        default=False,
        description="Publish non-triggering events as observed context instead of dropping them.",
    )
    reply_to_inbound: bool | None = Field(
        default=None,
        description="Optional override for the router's reply-to-inbound default.",
    )
    include_reasoning: bool = Field(
        default=False,
        description=(
            "Prepend the reasoning block to outbound comments. Off by default "
            "so model reasoning is never leaked onto a public repository."
        ),
    )
    lifecycle_as_context: bool = Field(
        default=True,
        description="Publish issue/PR closed/reopened events as observed context.",
    )
    enable_issue_tools: bool = Field(
        default=True,
        description="Register github_* issue-management LLM tools.",
    )
    max_comment_length: int = Field(
        default=65536,
        ge=256,
        description="Outbound comment length cap; GitHub rejects bodies beyond 65536 chars.",
    )

    request_timeout: float = Field(default=20.0, gt=0)
    send_retry_attempts: int = Field(default=3, ge=1)
    send_retry_backoff: float = Field(default=1.5, gt=0)

    @field_validator("token", "webhook_secret", "bot_login")
    @classmethod
    def _strip_secret(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_base_url")
    @classmethod
    def _normalize_api_base(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return _DEFAULT_API_BASE
        if not value.startswith(("https://", "http://")):
            value = f"https://{value}"
        return value

    @field_validator("allowed_repos", "allowed_senders", "allowed_orgs", mode="before")
    @classmethod
    def _coerce_id_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int)):
            return [str(value)]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise TypeError("allowed id lists must be a string, integer, or list")

    @property
    def allowed_sender_keys(self) -> frozenset[str]:
        """Lower-cased login set used for sender matching."""
        return frozenset(key.lower() for key in self.allowed_senders)

    @property
    def allowed_org_keys(self) -> frozenset[str]:
        """Lower-cased organization set used for org membership matching."""
        return frozenset(key.lower() for key in self.allowed_orgs)

    def repo_allowed(self, full_name: str) -> bool:
        """Whether a repository full name passes the allow-list.

        Entries match exactly (``owner/name``) or as organization wildcards
        (``org/*`` covers every repository under the organization).
        """
        entries = self.allowed_repos
        if not entries:
            return True
        value = full_name.strip().lower()
        for entry in entries:
            normalized = entry.lower()
            if normalized == value:
                return True
            if normalized.endswith("/*") and value.startswith(normalized[:-2] + "/"):
                return True
        return False

    def sender_allowed(self, login: str) -> bool:
        """Whether a sender login passes the static allow-list.

        Organization-based filtering is not consulted here; it is resolved
        asynchronously by the converter (``allowed_orgs`` participates at
        that layer). With both lists empty every sender passes.
        """
        if not self.allowed_senders:
            return True
        return login.strip().lower() in self.allowed_sender_keys


def parse_github_config(raw: dict[str, Any] | None) -> GitHubChannelConfig:
    """Parse a plugin manifest config mapping into ``GitHubChannelConfig``."""
    return GitHubChannelConfig.model_validate(raw or {})
