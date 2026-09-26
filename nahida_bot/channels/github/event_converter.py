"""Convert GitHub webhook payloads into normalized inbound messages."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from nahida_bot.channels.github.config import GitHubChannelConfig
from nahida_bot.core.message_context import (
    chat_context_from_values,
    context_from_inbound,
    sender_context_from_values,
)
from nahida_bot.plugins.base import InboundMessage

ConvertedKind = Literal["message", "context"]

OrgMemberChecker = Callable[[str], Awaitable[bool]]

# (event, action) pairs that carry user-written content and may trigger a reply.
_TRIGGER_EVENTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("issue_comment", "created"),
        ("issues", "opened"),
        ("pull_request", "opened"),
        ("pull_request_review", "submitted"),
        ("pull_request_review_comment", "created"),
    }
)
# Lifecycle pairs published as observed context only (lifecycle_as_context).
_CONTEXT_EVENTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("issues", "closed"),
        ("issues", "reopened"),
        ("pull_request", "closed"),
        ("pull_request", "reopened"),
    }
)

_TITLE_DISPLAY_LIMIT = 120


@dataclass(slots=True, frozen=True)
class GitHubConvertedEvent:
    """One converted webhook event."""

    inbound: InboundMessage
    kind: ConvertedKind  # "message" runs the group policy; "context" is observe-only


class GitHubEventConverter:
    """Normalize GitHub webhook payloads (already signature-verified)."""

    def __init__(
        self,
        config: GitHubChannelConfig,
        *,
        bot_login: str = "",
        org_member_checker: OrgMemberChecker | None = None,
    ) -> None:
        self._config = config
        self._bot_login = bot_login.strip()
        self._org_member_checker = org_member_checker

    @property
    def bot_login(self) -> str:
        """Bot account login used for mention/self detection."""
        return self._bot_login

    def with_bot_login(self, bot_login: str) -> "GitHubEventConverter":
        """Return a copy with an updated bot login (after identity discovery)."""
        return GitHubEventConverter(
            self._config,
            bot_login=bot_login,
            org_member_checker=self._org_member_checker,
        )

    async def to_inbound(
        self, event_name: str, payload: dict[str, Any]
    ) -> GitHubConvertedEvent | None:
        """Convert one payload; returns None when filtered or unsupported."""
        action = _as_str(payload.get("action"))
        kind = self._classify(event_name, action)
        if kind is None:
            return None

        repo_full = _nested_str(payload, "repository", "full_name")
        if not repo_full or not self._config.repo_allowed(repo_full):
            return None

        sender_login, sender_is_bot = _sender(payload)
        if not sender_login:
            return None
        if sender_is_bot or self._is_self(sender_login):
            # Loop prevention: never react to bot accounts (ours included) —
            # every comment we post comes back as issue_comment.created.
            return None
        if not await self._sender_passes(sender_login):
            return None

        if kind == "message":
            inbound = self._to_message_inbound(
                event_name, payload, repo_full, sender_login
            )
        else:
            inbound = self._to_context_inbound(
                event_name, action, payload, repo_full, sender_login
            )
        if inbound is None:
            return None
        return GitHubConvertedEvent(inbound=inbound, kind=kind)

    # ── classification & filtering ────────────────────────────────

    def _classify(self, event_name: str, action: str) -> ConvertedKind | None:
        if (event_name, action) in _TRIGGER_EVENTS:
            return "message"
        if (event_name, action) in _CONTEXT_EVENTS:
            return "context" if self._config.lifecycle_as_context else None
        return None

    def _is_self(self, login: str) -> bool:
        return bool(self._bot_login) and login.lower() == self._bot_login.lower()

    async def _sender_passes(self, login: str) -> bool:
        """Combined sender filter: static allow-list OR org membership.

        With both ``allowed_senders`` and ``allowed_orgs`` empty every human
        sender passes and no membership probe is issued. Otherwise a sender
        passes by being in the static list, or — when orgs are configured —
        by being a member of any allowed organization. A configured org
        list without a working checker denies non-static senders
        (fail-closed).
        """
        config = self._config
        if login.lower() in config.allowed_sender_keys:
            return True
        if not config.allowed_sender_keys and not config.allowed_org_keys:
            return True
        if config.allowed_org_keys and self._org_member_checker is not None:
            return await self._org_member_checker(login)
        return False

    def _mentions_bot(self, text: str) -> bool:
        if not self._bot_login or not text:
            return False
        pattern = rf"(?<![\w-])@{re.escape(self._bot_login)}\b"
        return re.search(pattern, text, re.IGNORECASE) is not None

    # ── inbound builders ──────────────────────────────────────────

    def _to_message_inbound(
        self,
        event_name: str,
        payload: dict[str, Any],
        repo_full: str,
        sender_login: str,
    ) -> InboundMessage | None:
        if event_name == "issue_comment":
            issue = _as_mapping(payload.get("issue"))
            comment = _as_mapping(payload.get("comment"))
            body = _as_str(comment.get("body"))
            if not body.strip():
                return None
            return self._build(
                repo_full=repo_full,
                number=_as_int(issue.get("number")),
                title=_as_str(issue.get("title")),
                is_pr="pull_request" in issue,
                sender_login=sender_login,
                text=body,
                message_id=str(_as_int(comment.get("id"))),
                created_at=_as_str(comment.get("created_at")),
            )

        if event_name == "issues":
            issue = _as_mapping(payload.get("issue"))
            body = _as_str(issue.get("body"))
            text = body.strip() or "(_no description_)"
            return self._build(
                repo_full=repo_full,
                number=_as_int(issue.get("number")),
                title=_as_str(issue.get("title")),
                is_pr="pull_request" in issue,
                sender_login=sender_login,
                text=text,
                message_id=f"issues:opened:{_as_int(issue.get('id'))}",
                created_at=_as_str(issue.get("created_at")),
            )

        if event_name == "pull_request":
            pull = _as_mapping(payload.get("pull_request"))
            body = _as_str(pull.get("body"))
            text = body.strip() or "(_no description_)"
            return self._build(
                repo_full=repo_full,
                number=_as_int(pull.get("number")),
                title=_as_str(pull.get("title")),
                is_pr=True,
                sender_login=sender_login,
                text=text,
                message_id=f"pull_request:opened:{_as_int(pull.get('id'))}",
                created_at=_as_str(pull.get("created_at")),
            )

        if event_name == "pull_request_review":
            pull = _as_mapping(payload.get("pull_request"))
            review = _as_mapping(payload.get("review"))
            body = _as_str(review.get("body")).strip()
            state = _as_str(review.get("state")) or "commented"
            if not body:
                body = f"(_review: {state}_)"
            return self._build(
                repo_full=repo_full,
                number=_as_int(pull.get("number")),
                title=_as_str(pull.get("title")),
                is_pr=True,
                sender_login=sender_login,
                text=body,
                message_id=str(_as_int(review.get("id"))),
                created_at=_as_str(review.get("submitted_at")),
            )

        if event_name == "pull_request_review_comment":
            pull = _as_mapping(payload.get("pull_request"))
            comment = _as_mapping(payload.get("comment"))
            body = _as_str(comment.get("body"))
            if not body.strip():
                return None
            return self._build(
                repo_full=repo_full,
                number=_as_int(pull.get("number")),
                title=_as_str(pull.get("title")),
                is_pr=True,
                sender_login=sender_login,
                text=body,
                message_id=str(_as_int(comment.get("id"))),
                created_at=_as_str(comment.get("created_at")),
            )
        return None

    def _to_context_inbound(
        self,
        event_name: str,
        action: str,
        payload: dict[str, Any],
        repo_full: str,
        sender_login: str,
    ) -> InboundMessage | None:
        if event_name == "issues":
            issue = _as_mapping(payload.get("issue"))
            return self._build(
                repo_full=repo_full,
                number=_as_int(issue.get("number")),
                title=_as_str(issue.get("title")),
                is_pr="pull_request" in issue,
                sender_login=sender_login,
                text=f"[_lifecycle_] {action} this issue",
                message_id=f"issues:{action}:{_as_int(issue.get('id'))}",
                created_at="",
            )
        pull = _as_mapping(payload.get("pull_request"))
        merged = _as_bool(_nested_get(payload, "pull_request", "merged"))
        suffix = " (merged)" if merged and action == "closed" else ""
        return self._build(
            repo_full=repo_full,
            number=_as_int(pull.get("number")),
            title=_as_str(pull.get("title")),
            is_pr=True,
            sender_login=sender_login,
            text=f"[_lifecycle_] {action} this pull request{suffix}",
            message_id=f"pull_request:{action}:{_as_int(pull.get('id'))}",
            created_at="",
        )

    def _build(
        self,
        *,
        repo_full: str,
        number: int,
        title: str,
        is_pr: bool,
        sender_login: str,
        text: str,
        message_id: str,
        created_at: str,
    ) -> InboundMessage | None:
        if number <= 0 or not message_id or message_id in {"0", "issues:opened:0"}:
            return None
        chat_id = f"{repo_full}#{number}"
        display_title = title.strip()[:_TITLE_DISPLAY_LIMIT]
        prefix = "PR " if is_pr else ""
        display_name = (
            f"{chat_id} · {prefix}{display_title}" if display_title else chat_id
        )
        inbound = InboundMessage(
            message_id=message_id,
            platform="github",
            chat_id=chat_id,
            user_id=sender_login,
            text=text,
            raw_event={"chat_id": chat_id, "is_pr": is_pr},
            is_group=True,
            reply_to="",
            timestamp=_iso_to_epoch(created_at),
            command_prefix=self._config.command_prefix,
            sender_context=sender_context_from_values(
                display_name=sender_login,
                platform_user_id=sender_login,
            ),
            chat_context=chat_context_from_values(
                platform="github",
                chat_type="group",
                platform_chat_id=chat_id,
                display_name=display_name,
            ),
            mentions_bot=self._mentions_bot(text),
        )
        return _with_context(inbound)


# ── payload helpers ──────────────────────────────────────────────


def _with_context(inbound: InboundMessage) -> InboundMessage:
    from dataclasses import replace

    return replace(inbound, message_context=context_from_inbound(inbound))


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_str(value: Any) -> str:
    return str(value) if isinstance(value, (str, int, float)) else ""


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _as_bool(value: Any) -> bool:
    return value is True or value == "true"


def _sender(payload: dict[str, Any]) -> tuple[str, bool]:
    sender = payload.get("sender")
    if not isinstance(sender, dict):
        return "", False
    login = _as_str(sender.get("login"))
    is_bot = _as_str(sender.get("type")).lower() == "bot"
    return login, is_bot


def _nested_get(raw: dict[str, Any], *keys: str) -> Any:
    current: Any = raw
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _nested_str(raw: dict[str, Any], *keys: str) -> str:
    return _as_str(_nested_get(raw, *keys))


def _iso_to_epoch(value: str) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()
