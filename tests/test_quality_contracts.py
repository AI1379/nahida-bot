"""Cross-module contracts identified during the September quality review."""

from collections import deque
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nahida_bot.agent.retrieval.adapters import _turn_record_to_retrieval
from nahida_bot.agent.storage.models import SearchResult
from nahida_bot.channels.milky.plugin import MilkyPlugin
from nahida_bot.core.config import KBAutoRecallConfig, PendingMessagesConfig
from nahida_bot.core.router import PendingMessage, RouterConfig
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.gateway.routes.sessions import _chat_address_from_session_id
from nahida_bot.identity.authorization import chat_key_from_session_id
from nahida_bot.plugins.base import MessageContext
from nahida_bot.plugins.builtin.tools.history import HistoryTools
from nahida_bot.plugins.knowledge_base.plugin import KnowledgeBasePlugin
from nahida_bot.plugins.registry import PromptSupplementEntry, PromptSupplementRegistry
from nahida_bot_sdk.chat_address import chat_key_from_session_id as sdk_chat_key

from .helpers import RecordingMockBotAPI
from .test_feishu_plugin import _plugin as feishu_plugin
from .test_knowledge_base_plugin import (
    _Manager,
    _StrictRecordingAPI,
    _kb_manifest_with_config,
)
from .test_message_router import _inbound, _make_router
from .test_milky_plugin import _FakeClient, _manifest


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("milky:group:123", "milky:group:123"),
        ("milky:group:123:new", "milky:group:123"),
        ("milky:group:123:cron:job:fire:1", "milky:group:123"),
        ("milky:unknown:123:new", "milky:unknown:123"),
        ("milky:123", "milky:123"),
        ("milky:123:new", "milky:123"),
        ("milky:123:cron:job", "milky:123"),
        ("internal-session", "internal-session"),
        ("", ""),
    ],
)
def test_chat_ownership_agrees_across_consumers(session_id, expected):
    assert sdk_chat_key(session_id) == expected
    assert chat_key_from_session_id(session_id) == expected
    assert _chat_address_from_session_id(session_id) == expected
    assert HistoryTools.base_chat_key(session_id) == expected
    result = _turn_record_to_retrieval(SimpleNamespace(session_id=session_id))
    assert result.provenance.scope_id == expected


@pytest.mark.parametrize("channel", ["milky", "feishu"])
@pytest.mark.parametrize("enabled,limit", [(False, 1), (True, 1), (True, 5)])
async def test_channel_mention_prompt_follows_configuration(channel, enabled, limit):
    config = dict(outbound_mentions_enabled=enabled, max_mentions_per_message=limit)
    if channel == "feishu":
        plugin, api, _ = feishu_plugin(**config)
    else:
        api = RecordingMockBotAPI()
        plugin = MilkyPlugin(api=api, manifest=_manifest(**config))
        plugin._client = _FakeClient()
    await plugin.on_load()

    registry = PromptSupplementRegistry()
    for key, entry in api.registered_prompt_supplements.items():
        registry.register(PromptSupplementEntry(key=key, plugin_id=channel, **entry))
    runner = SessionRunner(supplement_registry=registry)
    group = MessageContext(channel=channel, chat_type="group", chat_id="123")
    prompt = runner._build_system_prompt("base", group)
    assert ("[CQ:at,qq=" in prompt) is enabled
    if enabled:
        assert f"at most {limit} distinct mention targets" in prompt
        assert ("ou_" in prompt) is (channel == "feishu")
    private = runner._build_system_prompt("base", replace(group, chat_type="private"))
    assert "[CQ:at,qq=" not in private


async def test_kb_retains_neighbor_ranking_through_plugin_and_auto_recall():
    manager = _Manager()
    store = await manager.get_or_create("docs")
    store.search_results = [
        SearchResult(
            doc_id="hit",
            title="Hit",
            content="main text",
            score=10,
            source_id="source",
            chunk_index=1,
        )
    ]
    store.get_neighbors = AsyncMock(
        return_value=[
            SearchResult(
                doc_id="neighbor",
                title="Neighbor",
                content="adjacent text",
                score=5,
                source_id="source",
                chunk_index=2,
            )
        ]
    )
    plugin = KnowledgeBasePlugin(
        api=_StrictRecordingAPI(manager),
        manifest=_kb_manifest_with_config({"retrieval": {"expand_neighbors": True}}),
    )
    await plugin.on_load()
    results = await plugin.retrieve_documents("docs", "query")
    neighbor = results[1]
    assert neighbor.mode == "fts"
    assert neighbor.score == 4.0
    assert neighbor.metadata["neighbor_of"] == "hit"
    public_results = await plugin.search_documents("docs", "query")
    assert public_results[1].score == neighbor.score
    assert public_results[1].metadata["neighbor_of"] == "hit"

    runner = SessionRunner(
        document_store_manager=manager,
        kb_plugin_resolver=lambda: plugin,
        kb_auto_recall_config=KBAutoRecallConfig(
            enabled=True, max_items=2, min_score=4.5
        ),
    )
    context = await runner._load_relevant_knowledge("query")
    assert context is not None
    assert context.metadata["kb_backend"] == "fts"
    assert "main text" in context.content
    assert "adjacent text" not in context.content


async def test_pending_queue_rejects_newest_and_preserves_accepted_input():
    router, _, _, _ = _make_router(
        config=RouterConfig(
            pending_messages=PendingMessagesConfig(max_messages=1, ttl_seconds=60)
        )
    )
    router._runner = SimpleNamespace(
        has_agent=True, run_tracker=SimpleNamespace(is_active=lambda _: True)
    )
    router._send_outbound = AsyncMock()
    await router._dispatch_message(_inbound("accepted"), "test:private:c1", "workspace")
    await router._dispatch_message(_inbound("rejected"), "test:private:c1", "workspace")
    assert len(router._pending["test:private:c1"]) == 1
    assert router._pending["test:private:c1"][0].inbound.text == "accepted"
    router._send_outbound.assert_awaited_once()
    assert "queue is full" in router._send_outbound.call_args.args[2].text


async def test_pending_expiry_keeps_fresh_request_fields():
    router, _, _, _ = _make_router()
    fresh = PendingMessage(
        inbound=_inbound("fresh"),
        workspace_id="workspace",
        source_tag="proactive_join",
        agent_instruction="instruction",
        reply_to_override="reply",
        proactive_context="context",
        attention_episode_id="episode",
    )
    stale = replace(fresh, queued_at=fresh.queued_at - 10000)
    router._pending["test:private:c1"] = deque([stale, fresh])
    router._dispatch_message = AsyncMock()
    await router._drain_pending("test:private:c1")
    router._dispatch_message.assert_awaited_once_with(
        fresh.inbound,
        "test:private:c1",
        "workspace",
        source_tag="proactive_join",
        agent_instruction="instruction",
        reply_to_override="reply",
        proactive_context="context",
        attention_episode_id="episode",
    )
    assert "test:private:c1" not in router._pending
