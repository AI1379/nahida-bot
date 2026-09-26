"""Async REST client for GitHub (Bearer PAT + v2022-11-28 endpoints).

Mirrors :class:`nahida_bot.channels.feishu.client.FeishuClient`: injectable
``http_client`` for tests, retry with ``Retry-After`` awareness, and typed
errors so callers can degrade instead of guessing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias

import httpx

from nahida_bot.channels.github.config import GitHubChannelConfig

JsonDict: TypeAlias = dict[str, Any]

_API_VERSION = "2022-11-28"
_USER_AGENT = "nahida-bot-github-channel"


class GitHubClientError(Exception):
    """Base class for GitHub client failures."""


class GitHubClientClosedError(GitHubClientError):
    """The client was used after being closed."""


class GitHubNetworkError(GitHubClientError):
    """Network or timeout failure while calling GitHub."""

    def __init__(self, message: str, *, api: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.api = api
        self.retryable = retryable


class GitHubHTTPStatusError(GitHubClientError):
    """Non-2xx HTTP status from the REST API."""

    def __init__(
        self, message: str, *, api: str, status_code: int, body: str = ""
    ) -> None:
        super().__init__(message)
        self.api = api
        self.status_code = status_code
        self.body = body
        self._retry_after_seconds = 0.0

    @property
    def retryable(self) -> bool:
        return self.status_code == 429 or 500 <= self.status_code < 600

    @property
    def retry_after(self) -> float:
        """Delay parsed from Retry-After (seconds), or 0 when absent."""
        return self._retry_after_seconds

    @retry_after.setter
    def retry_after(self, value: float) -> None:
        self._retry_after_seconds = max(0.0, value)


class GitHubAuthError(GitHubClientError):
    """GitHub rejected the configured token."""


class GitHubResponseError(GitHubClientError):
    """Malformed response body."""


class GitHubClient:
    """Small async HTTP client for the GitHub REST API."""

    def __init__(
        self,
        config: GitHubChannelConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None
        self._sleep = sleep
        self._closed = False

    @property
    def config(self) -> GitHubChannelConfig:
        """Client configuration."""
        return self._config

    async def close(self) -> None:
        """Close the underlying connection pool when owned by this client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None
        self._closed = True

    # ── public API surface ────────────────────────────────────────

    async def get_authenticated_user(self) -> JsonDict:
        """Fetch the token identity (``GET /user``)."""
        return await self._call_api("GET", "/user", api="get_user")

    async def get_issue(
        self, owner: str, repo: str, number: int, *, api: str = "get_issue"
    ) -> JsonDict:
        """Fetch one issue or PR (``GET /repos/:o/:r/issues/:n``)."""
        return await self._call_api(
            "GET", f"/repos/{owner}/{repo}/issues/{number}", api=api
        )

    async def list_issues(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        labels: Sequence[str] = (),
        limit: int = 20,
        include_pulls: bool = False,
    ) -> list[JsonDict]:
        """List repository issues sorted by last update (newest first)."""
        params: dict[str, str] = {
            "state": state,
            "sort": "updated",
            "direction": "desc",
            "per_page": str(max(1, min(limit, 100))),
        }
        if labels:
            params["labels"] = ",".join(labels)
        items = await self._call_api_list(
            "GET", f"/repos/{owner}/{repo}/issues", api="list_issues", params=params
        )
        if include_pulls:
            return items
        return [item for item in items if "pull_request" not in item]

    async def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str = "",
        labels: Sequence[str] = (),
    ) -> JsonDict:
        """Create one issue (``POST /repos/:o/:r/issues``)."""
        json_body: JsonDict = {"title": title}
        if body:
            json_body["body"] = body
        if labels:
            json_body["labels"] = list(labels)
        return await self._call_api(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            api="create_issue",
            json_body=json_body,
        )

    async def update_issue(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        state: str = "",
        title: str = "",
        body: str = "",
        labels: Sequence[str] | None = None,
    ) -> JsonDict:
        """Patch one issue (``PATCH /repos/:o/:r/issues/:n``)."""
        json_body: JsonDict = {}
        if state:
            json_body["state"] = state
        if title:
            json_body["title"] = title
        if body:
            json_body["body"] = body
        if labels is not None:
            json_body["labels"] = list(labels)
        if not json_body:
            raise GitHubClientError("update_issue requires at least one field")
        return await self._call_api(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{number}",
            api="update_issue",
            json_body=json_body,
        )

    async def add_comment(
        self, owner: str, repo: str, number: int, *, body: str
    ) -> JsonDict:
        """Comment on one issue/PR (``POST /repos/:o/:r/issues/:n/comments``)."""
        if not body.strip():
            raise GitHubClientError("comment body must not be empty")
        return await self._call_api(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            api="add_comment",
            json_body={"body": body},
        )

    async def list_comments(
        self, owner: str, repo: str, number: int, *, limit: int = 20
    ) -> list[JsonDict]:
        """List comments on one issue/PR, oldest first."""
        return await self._call_api_list(
            "GET",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            api="list_comments",
            params={"per_page": str(max(1, min(limit, 100)))},
        )

    async def check_org_membership(self, org: str, username: str) -> bool:
        """Check org membership (``GET /orgs/:org/members/:user``).

        Returns ``True`` on 204 (member) and ``False`` on 404 (not a
        member). Any other status (auth failure, concealed membership
        without owner permission, rate limit) raises so the caller can
        fail closed instead of guessing.
        """
        status = await self._probe_status_once(
            "GET", f"/orgs/{org}/members/{username}", api="check_org_membership"
        )
        if status == 204:
            return True
        if status == 404:
            return False
        raise GitHubHTTPStatusError(
            f"GitHub API check_org_membership returned HTTP {status}",
            api="check_org_membership",
            status_code=status,
        )

    # ── request plumbing ──────────────────────────────────────────

    async def _call_api(
        self,
        method: str,
        path: str,
        *,
        api: str,
        params: Mapping[str, str] | None = None,
        json_body: JsonDict | None = None,
    ) -> JsonDict:
        result = await self._call_api_with_retry(
            method, path, api=api, params=params, json_body=json_body
        )
        return result if isinstance(result, dict) else {}

    async def _call_api_list(
        self,
        method: str,
        path: str,
        *,
        api: str,
        params: Mapping[str, str] | None = None,
    ) -> list[JsonDict]:
        result = await self._call_api_with_retry(method, path, api=api, params=params)
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    async def _call_api_with_retry(
        self,
        method: str,
        path: str,
        *,
        api: str,
        params: Mapping[str, str] | None = None,
        json_body: JsonDict | None = None,
    ) -> Any:
        attempts = self._config.send_retry_attempts
        delay = self._config.send_retry_backoff
        last_error: GitHubClientError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._call_api_once(
                    method, path, api=api, params=params, json_body=json_body
                )
            except GitHubHTTPStatusError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
                delay = max(delay, exc.retry_after)
            except GitHubNetworkError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
            await self._sleep(delay)
            delay = delay * 2
        assert last_error is not None
        raise last_error

    async def _call_api_once(
        self,
        method: str,
        path: str,
        *,
        api: str,
        params: Mapping[str, str] | None = None,
        json_body: JsonDict | None = None,
    ) -> Any:
        client = self._ensure_client()
        url = f"{self._config.api_base_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": _USER_AGENT,
        }
        if self._config.token:
            headers["Authorization"] = f"Bearer {self._config.token}"

        request_kwargs: dict[str, Any] = {
            "params": dict(params) if params else None,
            "headers": headers,
            "timeout": httpx.Timeout(self._config.request_timeout),
        }
        if json_body is not None:
            request_kwargs["json"] = json_body

        try:
            response = await client.request(method, url, **request_kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise GitHubNetworkError(
                f"GitHub API {api} network failure: {exc}", api=api, retryable=True
            ) from exc
        except httpx.TimeoutException as exc:
            raise GitHubNetworkError(
                f"GitHub API {api} timed out: {exc}", api=api
            ) from exc
        except httpx.RequestError as exc:
            raise GitHubNetworkError(
                f"GitHub API {api} request failed: {exc}", api=api
            ) from exc

        if response.status_code in (200, 201):
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise GitHubResponseError(
                    f"GitHub API {api} returned invalid JSON"
                ) from exc

        if response.status_code in (401, 403):
            message = _error_message(response) or f"HTTP {response.status_code}"
            if response.status_code == 401:
                raise GitHubAuthError(f"GitHub token rejected ({api}): {message}")
            # 403 also covers secondary rate limits; treat as non-retryable
            # status failure and let callers decide.
            raise GitHubHTTPStatusError(
                f"GitHub API {api} returned HTTP 403: {message}",
                api=api,
                status_code=403,
                body=message,
            )

        retry_after = _parse_retry_after(response.headers.get("retry-after"))
        error = GitHubHTTPStatusError(
            f"GitHub API {api} returned HTTP {response.status_code}: "
            f"{_error_message(response)}",
            api=api,
            status_code=response.status_code,
            body=response.text[:2000],
        )
        error.retry_after = retry_after
        raise error

    async def _probe_status_once(self, method: str, path: str, *, api: str) -> int:
        """Issue one request and return the raw status code (no retry)."""
        client = self._ensure_client()
        url = f"{self._config.api_base_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": _USER_AGENT,
        }
        if self._config.token:
            headers["Authorization"] = f"Bearer {self._config.token}"
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                timeout=httpx.Timeout(self._config.request_timeout),
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise GitHubNetworkError(
                f"GitHub API {api} network failure: {exc}", api=api, retryable=True
            ) from exc
        except httpx.TimeoutException as exc:
            raise GitHubNetworkError(
                f"GitHub API {api} timed out: {exc}", api=api
            ) from exc
        except httpx.RequestError as exc:
            raise GitHubNetworkError(
                f"GitHub API {api} request failed: {exc}", api=api
            ) from exc
        return response.status_code

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise GitHubClientClosedError("GitHubClient has been closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.request_timeout)
            )
            self._owns_client = True
        return self._client


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        return str(body.get("message") or "")
    return ""


def _parse_retry_after(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0
