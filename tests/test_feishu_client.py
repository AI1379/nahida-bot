"""Tests for the Feishu OpenAPI client (token, envelope, retry)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nahida_bot.channels.feishu.client import (
    FeishuAPIError,
    FeishuClient,
    FeishuNetworkError,
)
from nahida_bot.channels.feishu.config import FeishuPluginConfig

pytestmark = pytest.mark.asyncio


class _Recorder:
    """MockTransport handler that replays scripted responses per request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.scripts: dict[str, list[Any]] = {}
        self.counts: dict[str, int] = {}

    def script(self, path_prefix: str, responses: list[Any]) -> None:
        self.scripts[path_prefix] = list(responses)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for prefix, responses in self.scripts.items():
            if prefix in request.url.path:
                self.counts[prefix] = self.counts.get(prefix, 0) + 1
                response = responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {}})


def _ok(data: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "msg": "success", "data": data or {}})


def _client(recorder: _Recorder, **config_kwargs: Any) -> FeishuClient:
    config = FeishuPluginConfig(
        app_id="cli_test",
        app_secret="secret",
        send_retry_backoff=0.001,
        **config_kwargs,
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    return FeishuClient(config, http_client=http_client, sleep=_no_sleep)


async def _no_sleep(_delay: float) -> None:
    return None


async def test_token_fetched_once_and_attached() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 7200, "tenant_access_token": "t-1"}
            )
        ],
    )
    recorder.script(
        "/im/v1/messages", [_ok({"message_id": "om_1"}), _ok({"message_id": "om_1b"})]
    )
    client = _client(recorder)

    await client.send_message(
        receive_id_type="chat_id", receive_id="oc_1", msg_type="text", content="{}"
    )
    await client.send_message(
        receive_id_type="chat_id", receive_id="oc_1", msg_type="text", content="{}"
    )

    assert recorder.counts["/auth/v3/tenant_access_token/internal"] == 1
    send_requests = [
        r for r in recorder.requests if r.url.path.endswith("/im/v1/messages")
    ]
    assert all(r.headers.get("Authorization") == "Bearer t-1" for r in send_requests)
    body = json.loads(send_requests[0].content)
    assert body["receive_id"] == "oc_1"
    assert send_requests[0].url.params["receive_id_type"] == "chat_id"
    await client.close()


async def test_api_error_raised_with_code() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t"}
            )
        ],
    )
    recorder.script(
        "/im/v1/messages",
        [httpx.Response(200, json={"code": 230013, "msg": "bot not in chat"})],
    )
    client = _client(recorder)

    with pytest.raises(FeishuAPIError) as exc_info:
        await client.send_message(
            receive_id_type="chat_id", receive_id="oc_1", msg_type="text", content="{}"
        )
    assert exc_info.value.code == 230013
    await client.close()


async def test_frequency_limit_retried_then_succeeds() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t"}
            )
        ],
    )
    recorder.script(
        "/im/v1/messages",
        [
            httpx.Response(200, json={"code": 230020, "msg": "frequency limit"}),
            _ok({"message_id": "om_2"}),
        ],
    )
    client = _client(recorder)

    result = await client.send_message(
        receive_id_type="chat_id", receive_id="oc_1", msg_type="text", content="{}"
    )
    assert result["message_id"] == "om_2"
    assert recorder.counts["/im/v1/messages"] == 2
    await client.close()


async def test_expired_token_refreshed_and_retried() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t-old"}
            ),
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t-new"}
            ),
        ],
    )
    recorder.script(
        "/im/v1/messages",
        [
            httpx.Response(200, json={"code": 99991663, "msg": "token expired"}),
            _ok({"message_id": "om_3"}),
        ],
    )
    client = _client(recorder)

    result = await client.send_message(
        receive_id_type="chat_id", receive_id="oc_1", msg_type="text", content="{}"
    )
    assert result["message_id"] == "om_3"
    assert recorder.counts["/auth/v3/tenant_access_token/internal"] == 2
    await client.close()


async def test_non_retryable_error_not_retried() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t"}
            )
        ],
    )
    recorder.script(
        "/im/v1/messages",
        [httpx.Response(200, json={"code": 230025, "msg": "too large"})],
    )
    client = _client(recorder)

    with pytest.raises(FeishuAPIError):
        await client.send_message(
            receive_id_type="chat_id", receive_id="oc_1", msg_type="text", content="{}"
        )
    assert recorder.counts["/im/v1/messages"] == 1
    await client.close()


async def test_upload_image_returns_image_key() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t"}
            )
        ],
    )
    recorder.script("/im/v1/images", [_ok({"image_key": "img_v2_x"})])
    client = _client(recorder)

    image_key = await client.upload_image(data=b"png", file_name="pic.png")
    assert image_key == "img_v2_x"
    upload_request = next(
        r for r in recorder.requests if r.url.path.endswith("/im/v1/images")
    )
    assert "multipart/form-data" in upload_request.headers["content-type"]
    await client.close()


async def test_get_chat_members_walks_pages() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t"}
            )
        ],
    )
    recorder.script(
        "/im/v1/chats",
        [
            _ok(
                {
                    "items": [{"member_id": "ou_1", "name": "甲"}],
                    "has_more": True,
                    "page_token": "tok2",
                }
            ),
            _ok({"items": [{"member_id": "ou_2", "name": "乙"}], "has_more": False}),
        ],
    )
    client = _client(recorder)

    members = await client.get_chat_members("oc_g")
    assert [m["member_id"] for m in members] == ["ou_1", "ou_2"]
    members_requests = [r for r in recorder.requests if "/members" in r.url.path]
    assert members_requests[1].url.params["page_token"] == "tok2"
    assert members_requests[0].url.params["member_id_type"] == "open_id"
    await client.close()


async def test_network_error_not_retried_when_not_retryable() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t"}
            )
        ],
    )
    recorder.script("/im/v1/messages", [httpx.ReadTimeout("timeout")])
    client = _client(recorder)

    with pytest.raises(FeishuNetworkError):
        await client.send_message(
            receive_id_type="chat_id", receive_id="oc_1", msg_type="text", content="{}"
        )
    assert recorder.counts["/im/v1/messages"] == 1
    await client.close()


async def test_reply_message_uses_reply_path() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t"}
            )
        ],
    )
    recorder.script("/reply", [_ok({"message_id": "om_r"})])
    client = _client(recorder)

    result = await client.reply_message(
        message_id="om_9", msg_type="text", content="{}", uuid="u-1"
    )
    assert result["message_id"] == "om_r"
    reply_request = next(r for r in recorder.requests if r.url.path.endswith("/reply"))
    body = json.loads(reply_request.content)
    assert body["uuid"] == "u-1"
    await client.close()


async def test_bot_info_tolerates_direct_object_shape() -> None:
    recorder = _Recorder()
    recorder.script(
        "/auth/v3/tenant_access_token/internal",
        [
            httpx.Response(
                200, json={"code": 0, "expire": 100, "tenant_access_token": "t"}
            )
        ],
    )
    recorder.script(
        "/bot/v3/info",
        [httpx.Response(200, json={"open_id": "ou_bot", "activate_status": 1})],
    )
    client = _client(recorder)

    info = await client.get_bot_info()
    assert info["open_id"] == "ou_bot"
    await client.close()
