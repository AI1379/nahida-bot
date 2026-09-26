"""Tests for the GitHub REST client (auth headers, errors, retry)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nahida_bot.channels.github.client import (
    GitHubAuthError,
    GitHubClient,
    GitHubClientError,
    GitHubHTTPStatusError,
    GitHubNetworkError,
)
from nahida_bot.channels.github.config import GitHubChannelConfig

pytestmark = pytest.mark.asyncio


class _Recorder:
    """MockTransport handler that replays scripted responses per path."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.scripts: dict[str, list[httpx.Response]] = {}
        self.counts: dict[str, int] = {}

    def script(self, path_part: str, responses: list[httpx.Response]) -> None:
        self.scripts[path_part] = list(responses)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for part, responses in self.scripts.items():
            if part in request.url.path:
                self.counts[part] = self.counts.get(part, 0) + 1
                response = responses.pop(0)
                return response
        return httpx.Response(404, json={"message": "not found"})


async def _no_sleep(_delay: float) -> None:
    return None


def _client(recorder: _Recorder, **config_kwargs: Any) -> GitHubClient:
    config = GitHubChannelConfig(
        token="ghp_test-token", send_retry_backoff=0.001, **config_kwargs
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    return GitHubClient(config, http_client=http_client, sleep=_no_sleep)


def _issue(number: int = 1, *, with_pull: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "number": number,
        "title": f"Issue {number}",
        "state": "open",
        "id": 1000 + number,
        "html_url": f"https://github.com/o/r/issues/{number}",
    }
    if with_pull:
        item["pull_request"] = {"url": "https://api.github.com/repos/o/r/pulls/9"}
    return item


async def test_get_authenticated_user_sends_headers() -> None:
    recorder = _Recorder()
    recorder.script("/user", [httpx.Response(200, json={"login": "nahida-bot"})])
    client = _client(recorder)

    info = await client.get_authenticated_user()

    assert info["login"] == "nahida-bot"
    request = recorder.requests[0]
    assert request.headers["Authorization"] == "Bearer ghp_test-token"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["User-Agent"]
    await client.close()


async def test_tokenless_client_omits_authorization() -> None:
    recorder = _Recorder()
    recorder.script("/user", [httpx.Response(200, json={"login": "anon"})])
    config = GitHubChannelConfig(token="")
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    client = GitHubClient(config, http_client=http_client, sleep=_no_sleep)

    await client.get_authenticated_user()

    assert "Authorization" not in recorder.requests[0].headers
    await client.close()


async def test_401_raises_auth_error() -> None:
    recorder = _Recorder()
    recorder.script("/user", [httpx.Response(401, json={"message": "Bad credentials"})])
    client = _client(recorder)

    with pytest.raises(GitHubAuthError):
        await client.get_authenticated_user()
    await client.close()


async def test_404_raises_status_error_without_retry() -> None:
    recorder = _Recorder()
    recorder.script(
        "/repos/o/r/issues/42",
        [httpx.Response(404, json={"message": "Not Found"})],
    )
    client = _client(recorder)

    with pytest.raises(GitHubHTTPStatusError) as exc_info:
        await client.get_issue("o", "r", 42)
    assert exc_info.value.status_code == 404
    assert recorder.counts["/repos/o/r/issues/42"] == 1
    await client.close()


async def test_500_retried_then_succeeds() -> None:
    recorder = _Recorder()
    recorder.script(
        "/repos/o/r/issues/1/comments",
        [
            httpx.Response(500, text="boom"),
            httpx.Response(201, json={"id": 9001, "html_url": "u"}),
        ],
    )
    client = _client(recorder)

    result = await client.add_comment("o", "r", 1, body="hello")

    assert result["id"] == 9001
    assert recorder.counts["/repos/o/r/issues/1/comments"] == 2
    body = json.loads(recorder.requests[0].content)
    assert body == {"body": "hello"}
    await client.close()


async def test_429_respects_retry_after_header() -> None:
    recorder = _Recorder()
    recorder.script(
        "/user",
        [
            httpx.Response(
                429,
                headers={"retry-after": "3"},
                json={"message": "rate limited"},
            ),
            httpx.Response(200, json={"login": "nahida-bot"}),
        ],
    )
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    config = GitHubChannelConfig(token="t", send_retry_backoff=0.001)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    client = GitHubClient(config, http_client=http_client, sleep=_sleep)

    info = await client.get_authenticated_user()

    assert info["login"] == "nahida-bot"
    assert delays and delays[0] >= 3.0
    await client.close()


async def test_list_issues_filters_pull_requests() -> None:
    recorder = _Recorder()
    recorder.script(
        "/repos/o/r/issues",
        [
            httpx.Response(
                200,
                json=[_issue(1), _issue(2, with_pull=True), _issue(3)],
            )
        ],
    )
    client = _client(recorder)

    issues = await client.list_issues("o", "r")

    assert [item["number"] for item in issues] == [1, 3]
    params = recorder.requests[0].url.params
    assert params["state"] == "open"
    assert params["sort"] == "updated"
    assert params["per_page"] == "20"
    await client.close()


async def test_create_issue_posts_body_and_labels() -> None:
    recorder = _Recorder()
    recorder.script(
        "/repos/o/r/issues",
        [httpx.Response(201, json=_issue(7))],
    )
    client = _client(recorder)

    await client.create_issue("o", "r", title="New bug", body="Details", labels=["bug"])

    body = json.loads(recorder.requests[0].content)
    assert body == {"title": "New bug", "body": "Details", "labels": ["bug"]}
    await client.close()


async def test_update_issue_requires_a_field() -> None:
    recorder = _Recorder()
    client = _client(recorder)

    with pytest.raises(GitHubClientError):
        await client.update_issue("o", "r", 1)
    await client.close()


async def test_network_error_wrapped() -> None:
    recorder = _Recorder()
    recorder.script("/user", [httpx.Response(200)])  # placeholder, unused
    config = GitHubChannelConfig(token="t", send_retry_attempts=1)
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("down"))
        )
    )
    client = GitHubClient(config, http_client=http_client, sleep=_no_sleep)

    with pytest.raises(GitHubNetworkError):
        await client.get_authenticated_user()
    await client.close()


async def test_closed_client_rejected() -> None:
    recorder = _Recorder()
    client = _client(recorder)
    await client.close()

    with pytest.raises(GitHubClientError):
        await client.get_authenticated_user()


async def test_check_org_membership_member_and_non_member() -> None:
    recorder = _Recorder()
    recorder.script("/orgs/AI1379/members/alice", [httpx.Response(204)])
    recorder.script(
        "/orgs/AI1379/members/bob", [httpx.Response(404, json={"message": "Not Found"})]
    )
    client = _client(recorder)

    assert await client.check_org_membership("AI1379", "alice") is True
    assert await client.check_org_membership("AI1379", "bob") is False
    await client.close()


async def test_check_org_membership_concealed_raises_for_fail_closed() -> None:
    recorder = _Recorder()
    recorder.script(
        "/orgs/AI1379/members/hidden",
        [httpx.Response(403, json={"message": "concealed membership"})],
    )
    client = _client(recorder)

    with pytest.raises(GitHubHTTPStatusError) as exc_info:
        await client.check_org_membership("AI1379", "hidden")
    assert exc_info.value.status_code == 403
    await client.close()
