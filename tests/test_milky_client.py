"""Tests for Milky HTTP API client."""

from __future__ import annotations

import json

import httpx
import pytest

from nahida_bot.channels.milky.client import (
    MilkyAPIError,
    MilkyAuthError,
    MilkyClient,
    MilkyClientClosedError,
    MilkyHTTPStatusError,
    MilkyNetworkError,
    MilkyResponseError,
)
from nahida_bot.channels.milky.config import parse_milky_config
from nahida_bot.channels.milky.segments import (
    OutgoingFileUpload,
    OutgoingImageSegment,
    OutgoingTextSegment,
)


def _client(
    handler: httpx.MockTransport,
    *,
    token: str = "secret",
) -> MilkyClient:
    config = parse_milky_config(
        {"base_url": "http://milky.local", "access_token": token}
    )
    return MilkyClient(config, http_client=httpx.AsyncClient(transport=handler))


@pytest.mark.asyncio
async def test_post_api_sends_bearer_token_and_returns_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://milky.local/api/get_login_info"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(
            200, json={"status": "ok", "retcode": 0, "data": {"uin": 12345}}
        )

    client = _client(httpx.MockTransport(handler))

    assert await client.get_login_info() == {"uin": 12345}


@pytest.mark.asyncio
async def test_send_private_message_serializes_segments_in_order() -> None:
    seen_payload: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200, json={"status": "ok", "retcode": 0, "data": {"message_seq": 9}}
        )

    client = _client(httpx.MockTransport(handler))
    result = await client.send_private_message(
        10001,
        [
            OutgoingTextSegment("hello"),
            {"type": "reply", "data": {"message_seq": 8}},
            OutgoingImageSegment(uri="file:///tmp/a.png"),
        ],
    )

    assert result == {"message_seq": 9}
    assert seen_payload["user_id"] == 10001
    assert seen_payload["message"] == [
        {"type": "text", "data": {"text": "hello"}},
        {"type": "reply", "data": {"message_seq": 8}},
        {
            "type": "image",
            "data": {"uri": "file:///tmp/a.png", "sub_type": "normal"},
        },
    ]


@pytest.mark.asyncio
async def test_client_maps_http_401_to_auth_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad token"})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(MilkyAuthError):
        await client.get_login_info()


@pytest.mark.asyncio
async def test_client_maps_non_200_to_http_status_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(MilkyHTTPStatusError):
        await client.get_login_info()


@pytest.mark.asyncio
async def test_client_maps_failed_api_envelope() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "failed",
                "retcode": 1001,
                "message": "bad request",
                "data": {},
            },
        )

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(MilkyAPIError) as exc:
        await client.get_login_info()
    assert exc.value.retcode == 1001


@pytest.mark.asyncio
async def test_client_maps_invalid_json_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(MilkyResponseError):
        await client.get_login_info()


@pytest.mark.asyncio
async def test_send_retries_retryable_network_error() -> None:
    attempts = 0
    sleep_delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("connect failed", request=request)
        return httpx.Response(
            200, json={"status": "ok", "retcode": 0, "data": {"message_seq": 1}}
        )

    async def sleep(delay: float) -> None:
        sleep_delays.append(delay)

    config = parse_milky_config(
        {
            "base_url": "http://milky.local",
            "send_retry_attempts": 2,
            "send_retry_backoff": 0.5,
        }
    )
    client = MilkyClient(
        config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sleep=sleep,
    )

    assert await client.send_group_message(20001, [OutgoingTextSegment("hi")]) == {
        "message_seq": 1
    }
    assert attempts == 2
    assert sleep_delays == [0.5]


@pytest.mark.asyncio
async def test_post_api_does_not_retry_generic_read_timeout() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    config = parse_milky_config(
        {"base_url": "http://milky.local", "send_retry_attempts": 3}
    )
    client = MilkyClient(
        config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(MilkyNetworkError):
        await client.send_group_message(20001, [OutgoingTextSegment("hi")])
    assert attempts == 1


@pytest.mark.asyncio
async def test_forward_and_file_api_helpers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/get_forwarded_messages"):
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "messages": [
                            {
                                "message_seq": 1,
                                "sender_name": "Alice",
                                "segments": [
                                    {"type": "text", "data": {"text": "hello"}}
                                ],
                            }
                        ]
                    },
                },
            )
        return httpx.Response(
            200, json={"status": "ok", "retcode": 0, "data": {"ok": True}}
        )

    client = _client(httpx.MockTransport(handler))

    messages = await client.get_forwarded_messages("forward-1")
    assert messages[0].sender_name == "Alice"
    assert await client.upload_group_file(
        20001,
        OutgoingFileUpload(
            file_uri="file:///tmp/report.pdf",
            file_name="report.pdf",
            parent_folder_id="/",
        ),
    ) == {"ok": True}


@pytest.mark.asyncio
async def test_client_rejects_use_after_close() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "ok", "retcode": 0, "data": {"uin": 12345}}
        )

    client = _client(httpx.MockTransport(handler))

    await client.close()

    with pytest.raises(MilkyClientClosedError):
        await client.get_login_info()
