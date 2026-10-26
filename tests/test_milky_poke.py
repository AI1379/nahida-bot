"""Outbound pokes must stay in the current chat and never repeat on failure."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from nahida_bot.channels.milky.client import MilkyClient, MilkyNetworkError
from nahida_bot.channels.milky.config import MilkyPluginConfig
from nahida_bot.channels.milky.poke import MilkyPokeTool
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext, current_session

from .helpers import RecordingMockBotAPI


def _context(address: str = "milky:group:20001", *, user_id: str = "10001"):
    chat = ChatAddress.parse(address)
    return SessionContext(
        platform=chat.channel,
        chat_id=chat.target_id,
        session_id=chat.chat_key,
        chat_address=chat,
        user_id=user_id,
    )


@pytest.fixture
def poke():
    client = AsyncMock(spec=MilkyClient)
    membership = AsyncMock(return_value=True)
    api = RecordingMockBotAPI()
    tool = MilkyPokeTool(
        api=api,
        channel_id="milky",
        config=MilkyPluginConfig(enable_poke_tool=True),
        get_client=lambda: client,
        get_self_id=lambda: 999,
        is_group_member=membership,
    )
    token = current_session.set(_context())
    yield tool, client, membership, api
    current_session.reset(token)


@pytest.mark.asyncio
async def test_group_poke_defaults_to_sender_and_reserves_before_await(poke):
    tool, client, membership, _api = poke
    results = await asyncio.gather(tool.send(), tool.send("10002"))
    assert sum(result.startswith("Poked") for result in results) == 1
    assert any("cooldown" in result for result in results)
    client.send_group_nudge.assert_awaited_once_with(group_id=20001, user_id=10001)
    assert membership.await_count == 2


@pytest.mark.asyncio
async def test_private_poke_only_current_peer(poke):
    tool, client, membership, _api = poke
    current_session.set(_context("milky:private:10001"))
    assert "current peer" in await tool.send("10002")
    assert (await tool.send()).startswith("Poked")
    client.send_friend_nudge.assert_awaited_once_with(user_id=10001)
    membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_unverified_group_member_never_sends(poke):
    tool, client, membership, _api = poke
    membership.return_value = False
    assert "Could not verify" in await tool.send()
    client.send_group_nudge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["999", "0", "-1", "all", "12.5", "9" * 20])
async def test_invalid_or_self_target_never_sends(poke, user_id):
    tool, client, membership, _api = poke
    assert (await tool.send(user_id)).startswith("Error:")
    membership.assert_not_awaited()
    client.send_group_nudge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["telegram:group:20001", "milky:channel:20001"])
async def test_wrong_channel_or_chat_type_never_sends(poke, address):
    tool, client, membership, _api = poke
    current_session.set(_context(address))
    assert (await tool.send()).startswith("Error:")
    membership.assert_not_awaited()
    client.send_group_nudge.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_session_and_no_typed_address_never_send(poke):
    tool, client, _membership, _api = poke
    current_session.set(None)
    assert "active Milky" in await tool.send()
    current_session.set(SessionContext("milky", "20001", "milky:20001"))
    assert "typed address" in await tool.send()
    client.send_group_nudge.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_allowlist_and_disabled_tool_are_enforced(poke):
    tool, client, _membership, _api = poke
    tool._config.allowed_groups = ["20002"]
    assert "not allowed" in await tool.send()
    tool._config.enable_poke_tool = False
    assert "disabled" in await tool.send()
    client.send_group_nudge.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_send_retains_cooldown(poke):
    tool, client, _membership, _api = poke
    client.send_group_nudge.side_effect = MilkyNetworkError(
        "timeout", api_name="send_group_nudge", retryable=True
    )
    assert "uncertain" in await tool.send()
    assert "cooldown" in await tool.send()
    client.send_group_nudge.assert_awaited_once()


@pytest.mark.asyncio
async def test_poke_cooldown_is_scoped_by_typed_chat(poke):
    tool, client, _membership, _api = poke
    await tool.send()
    current_session.set(_context("milky:private:20001", user_id="20001"))
    assert (await tool.send()).startswith("Poked")
    client.send_friend_nudge.assert_awaited_once_with(user_id=20001)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "args", "expected"),
    [
        (
            "send_group_nudge",
            {"group_id": 20001, "user_id": 10001},
            {"group_id": 20001, "user_id": 10001},
        ),
        ("send_friend_nudge", {"user_id": 10001}, {"user_id": 10001, "is_self": False}),
    ],
)
async def test_nudge_protocol_payload_and_no_retry(method, args, expected):
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        assert request.url.path == f"/api/{method}"
        assert json.loads(request.content) == expected
        raise httpx.ReadTimeout("uncertain delivery")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MilkyClient(
            MilkyPluginConfig(base_url="http://milky.local", send_retry_attempts=3),
            http_client=http,
        )
        with pytest.raises(MilkyNetworkError):
            await getattr(client, method)(**args)
        assert len(requests) == 1
