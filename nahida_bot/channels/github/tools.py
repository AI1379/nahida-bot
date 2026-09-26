"""LLM tools for GitHub issue management, registered by the GitHub channel."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from nahida_bot.channels.github.client import GitHubClient, GitHubClientError
from nahida_bot.channels.github.config import GitHubChannelConfig

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

ClientFactory = Callable[[], GitHubClient]

_ISSUE_SUMMARY_FIELDS = ("number", "title", "state", "html_url", "comments")


def register_issue_tools(
    api: Any,
    config: GitHubChannelConfig,
    client_factory: ClientFactory,
) -> None:
    """Register the github_* issue tools on one plugin API."""

    async def _list_issues(
        *,
        repo: str,
        state: str = "open",
        labels: str = "",
        limit: int = 10,
    ) -> str:
        parsed = _validate_repo(config, repo)
        if isinstance(parsed, str):
            return parsed
        owner, name = parsed
        label_items = [item.strip() for item in labels.split(",") if item.strip()]
        try:
            issues = await client_factory().list_issues(
                owner,
                name,
                state=state if state in {"open", "closed", "all"} else "open",
                labels=label_items,
                limit=limit,
            )
        except GitHubClientError as exc:
            return _error(f"list_issues failed: {exc}")
        return json.dumps(
            {
                "repo": f"{owner}/{name}",
                "count": len(issues),
                "issues": [_summarize_issue(item) for item in issues],
            },
            ensure_ascii=False,
        )

    async def _get_issue(
        *,
        repo: str,
        number: int,
        include_comments: bool = False,
        comment_limit: int = 10,
    ) -> str:
        parsed = _validate_repo(config, repo)
        if isinstance(parsed, str):
            return parsed
        owner, name = parsed
        try:
            issue = await client_factory().get_issue(owner, name, number)
            comments: list[dict[str, Any]] = []
            if include_comments:
                raw = await client_factory().list_comments(
                    owner, name, number, limit=max(comment_limit, 1) * 2
                )
                comments = [
                    {
                        "user": _nested(raw_item, "user", "login"),
                        "created_at": raw_item.get("created_at"),
                        "body": str(raw_item.get("body") or "")[:2000],
                    }
                    for raw_item in raw[-max(comment_limit, 1) :]
                ]
        except GitHubClientError as exc:
            return _error(f"get_issue failed: {exc}")
        return json.dumps(
            {
                **_summarize_issue(issue),
                "user": _nested(issue, "user", "login"),
                "body": str(issue.get("body") or "")[:4000],
                "is_pull_request": "pull_request" in issue,
                "comments": comments,
            },
            ensure_ascii=False,
        )

    async def _create_issue(
        *,
        repo: str,
        title: str,
        body: str = "",
        labels: Sequence[str] | None = None,
    ) -> str:
        parsed = _validate_repo(config, repo)
        if isinstance(parsed, str):
            return parsed
        owner, name = parsed
        if not title.strip():
            return _error("title must not be empty")
        try:
            issue = await client_factory().create_issue(
                owner, name, title=title.strip(), body=body, labels=labels or ()
            )
        except GitHubClientError as exc:
            return _error(f"create_issue failed: {exc}")
        return json.dumps(_summarize_issue(issue), ensure_ascii=False)

    async def _add_comment(*, repo: str, number: int, body: str) -> str:
        parsed = _validate_repo(config, repo)
        if isinstance(parsed, str):
            return parsed
        owner, name = parsed
        if not body.strip():
            return _error("body must not be empty")
        try:
            comment = await client_factory().add_comment(owner, name, number, body=body)
        except GitHubClientError as exc:
            return _error(f"add_comment failed: {exc}")
        return json.dumps(
            {
                "id": comment.get("id"),
                "html_url": comment.get("html_url"),
                "repo": f"{owner}/{name}",
                "number": number,
            },
            ensure_ascii=False,
        )

    async def _update_issue(
        *,
        repo: str,
        number: int,
        state: str,
        title: str = "",
        labels: Sequence[str] | None = None,
    ) -> str:
        parsed = _validate_repo(config, repo)
        if isinstance(parsed, str):
            return parsed
        owner, name = parsed
        if state not in {"open", "closed"}:
            return _error("state must be 'open' or 'closed'")
        try:
            issue = await client_factory().update_issue(
                owner,
                name,
                number,
                state=state,
                title=title,
                labels=labels,
            )
        except GitHubClientError as exc:
            return _error(f"update_issue failed: {exc}")
        return json.dumps(_summarize_issue(issue), ensure_ascii=False)

    api.register_tool(
        "github_list_issues",
        "List issues of a GitHub repository (newest activity first, pull "
        "requests excluded). Optionally filter by state and labels.",
        {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name form.",
                },
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "description": "Issue state filter. Defaults to open.",
                },
                "labels": {
                    "type": "string",
                    "description": "Comma-separated label filter, e.g. 'bug,ui'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum issues to return (1-100). Defaults to 10.",
                },
            },
            "required": ["repo"],
            "additionalProperties": False,
        },
        _list_issues,
    )
    api.register_tool(
        "github_get_issue",
        "Fetch one GitHub issue or pull request by number, optionally with "
        "its recent comments.",
        {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name form.",
                },
                "number": {"type": "integer", "description": "Issue number."},
                "include_comments": {
                    "type": "boolean",
                    "description": "Also return recent comments. Defaults to false.",
                },
                "comment_limit": {
                    "type": "integer",
                    "description": "How many recent comments to include. Defaults to 10.",
                },
            },
            "required": ["repo", "number"],
            "additionalProperties": False,
        },
        _get_issue,
    )
    api.register_tool(
        "github_create_issue",
        "Create a new issue in a GitHub repository.",
        {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name form.",
                },
                "title": {"type": "string", "description": "Issue title."},
                "body": {
                    "type": "string",
                    "description": "Issue body in Markdown. Defaults to empty.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels to apply (requires push access).",
                },
            },
            "required": ["repo", "title"],
            "additionalProperties": False,
        },
        _create_issue,
    )
    api.register_tool(
        "github_add_comment",
        "Add a comment to a GitHub issue or pull request. The body is "
        "Markdown and is posted publicly under the bot account.",
        {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name form.",
                },
                "number": {
                    "type": "integer",
                    "description": "Issue or pull request number.",
                },
                "body": {
                    "type": "string",
                    "description": "Comment body in Markdown.",
                },
            },
            "required": ["repo", "number", "body"],
            "additionalProperties": False,
        },
        _add_comment,
    )
    api.register_tool(
        "github_update_issue",
        "Update a GitHub issue or pull request: close, reopen, retitle, or "
        "replace labels. State changes are permanent and visible publicly.",
        {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name form.",
                },
                "number": {
                    "type": "integer",
                    "description": "Issue or pull request number.",
                },
                "state": {
                    "type": "string",
                    "enum": ["open", "closed"],
                    "description": "Target state: 'closed' closes, 'open' reopens.",
                },
                "title": {
                    "type": "string",
                    "description": "New title. Empty keeps the current title.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replacement label set. Omit to keep labels.",
                },
            },
            "required": ["repo", "number", "state"],
            "additionalProperties": False,
        },
        _update_issue,
        requires_admin=True,
    )


def _validate_repo(config: GitHubChannelConfig, repo: str) -> tuple[str, str] | str:
    """Return ``(owner, name)`` or an error JSON string."""
    value = repo.strip()
    if not _REPO_PATTERN.match(value):
        return _error(f"repo must be in owner/name form, got: {repo!r}")
    if not config.repo_allowed(value):
        return _error(
            f"repo {value} is not in the allowed_repos list for this deployment"
        )
    owner, name = value.split("/", 1)
    return owner, name


def _summarize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {field: issue.get(field) for field in _ISSUE_SUMMARY_FIELDS}


def _nested(raw: dict[str, Any], *keys: str) -> Any:
    current: Any = raw
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)
