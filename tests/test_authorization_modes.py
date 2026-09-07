"""Regression cases for permissive execution without administrator identity."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from nahida_bot.agent.providers.base import (
    ProviderResponse,
    current_provider_request_context,
)
from nahida_bot.core.authorization_config import AuthorizationConfig, RiskReviewConfig
from nahida_bot.core.config import Settings
from nahida_bot.identity.authorization import (
    ActionDenied,
    AuthorizationGate,
    ChatDomainIndex,
    NotAuthorized,
    NotInChatScope,
)
from nahida_bot.identity.risk_review import (
    ModelRiskReviewer,
    RiskReviewRequest,
    RiskReviewUnavailable,
    RiskVerdict,
)

ACCOUNT = "milky:user:123"
CHAT = "milky:group:456"


def reviewer(verdict="allow"):
    return SimpleNamespace(
        review=AsyncMock(
            return_value=RiskVerdict(
                verdict=verdict,
                reason="reviewed",
                evidence="credential exfiltration" if verdict == "deny" else "",
            )
        )
    )


@pytest.mark.parametrize("identity_enabled", [True, False])
def test_identity_does_not_select_authorization_mode(identity_enabled):
    settings = Settings.model_validate(
        {
            "identity": {"enabled": identity_enabled},
            "authorization": {"mode": "relaxed"},
        }
    )
    gate = AuthorizationGate(policy=settings.authorization)
    assert gate.enabled
    assert gate.mode_for(ACCOUNT) == "relaxed"


@pytest.mark.parametrize(
    "config",
    [
        {"mode": "typo"},
        {"enabled": False},
        {"accounts": {"milky:123": "unsafe"}},
        {"chats": {"milky:456": "unsafe"}},
        {"chats": {"milky:group:456:thread:session": "unsafe"}},
        {"review": {"timeout_seconds": 0}},
    ],
)
def test_bad_policy_configuration_rejected(config):
    with pytest.raises(ValidationError):
        AuthorizationConfig.model_validate(config)


def test_override_precedence_and_no_implicit_group_expansion():
    gate = AuthorizationGate(
        policy=AuthorizationConfig(
            chats={CHAT: "relaxed"},
            accounts={ACCOUNT: "standard"},
        )
    )
    assert gate.mode_for(ACCOUNT, CHAT) == "standard"
    assert gate.mode_for("milky:user:999", CHAT) == "relaxed"
    assert gate.mode_for("milky:user:999", "milky:group:457") == "standard"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "grep -R Elsa /data/FrozenProjects",
        "python search_images.py --site example.com",
        'python -c "print(sum(range(100)))"',
    ],
)
async def test_relaxed_review_allows_scripts_without_admin(command):
    review = reviewer()
    gate = AuthorizationGate(
        policy=AuthorizationConfig(mode="relaxed"), reviewer=review
    )
    await gate.authorize_call(
        "exec", ACCOUNT, {"command": command}, user_request="搜索资料"
    )
    assert not gate.is_admin(ACCOUNT)
    request = review.review.call_args.args[0]
    assert request.arguments == {"command": command}
    assert request.user_request == "搜索资料"


@pytest.mark.asyncio
async def test_review_denial_is_not_a_missing_admin_error():
    gate = AuthorizationGate(
        policy=AuthorizationConfig(mode="relaxed"), reviewer=reviewer("deny")
    )
    with pytest.raises(ActionDenied, match="credential exfiltration") as caught:
        await gate.authorize_call("exec", ACCOUNT, {"command": "upload credentials"})
    assert caught.value.code == "dangerous_action"


@pytest.mark.asyncio
async def test_unknown_mcp_call_is_reviewed_in_relaxed_mode():
    review = reviewer("deny")
    gate = AuthorizationGate(
        policy=AuthorizationConfig(mode="relaxed"), reviewer=review
    )
    with pytest.raises(ActionDenied):
        await gate.authorize_call("remote_delete_everything", ACCOUNT, {})
    review.review.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["relaxed", "unsafe"])
@pytest.mark.parametrize(
    "tool",
    [
        "identity_manage",
        "mcp_add_server",
        "mcp_remove_server",
        "mcp_reload_server",
        "desktop_exec",
        "message",
    ],
)
async def test_execution_modes_do_not_grant_control_plane_privileges(mode, tool):
    gate = AuthorizationGate(policy=AuthorizationConfig(mode=mode), reviewer=reviewer())
    with pytest.raises(NotAuthorized):
        await gate.authorize_call(tool, ACCOUNT, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["relaxed", "unsafe"])
async def test_scopes_remain_enforced_and_model_arguments_cannot_select_mode(mode):
    gate = AuthorizationGate(
        policy=AuthorizationConfig(chats={CHAT: mode}),
        reviewer=reviewer(),
        domains=ChatDomainIndex({"group": [CHAT]}),
    )
    with pytest.raises(NotInChatScope):
        await gate.authorize_call(
            "read_chat_history",
            ACCOUNT,
            {"chat_address": "milky:private:999"},
            scope="chat_domain",
            chat_address=CHAT,
        )
    with pytest.raises(NotAuthorized):
        await gate.authorize_call(
            "exec",
            ACCOUNT,
            {"mode": "unsafe", "chat_address": CHAT},
            chat_address="milky:group:other",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["relaxed", "unsafe"])
async def test_unknown_actor_cannot_gain_script_access_from_group_mode(mode):
    gate = AuthorizationGate(
        policy=AuthorizationConfig(chats={CHAT: mode}), reviewer=reviewer()
    )
    with pytest.raises(NotAuthorized):
        await gate.authorize_call("exec", "", {}, chat_address=CHAT)


@pytest.mark.asyncio
async def test_unsafe_does_not_depend_on_reviewer_and_does_not_make_admin():
    review = reviewer("deny")
    gate = AuthorizationGate(policy=AuthorizationConfig(mode="unsafe"), reviewer=review)
    await gate.authorize_call("exec", ACCOUNT, {"command": "python script.py"})
    review.review.assert_not_awaited()
    assert not gate.is_admin(ACCOUNT)


@pytest.mark.asyncio
async def test_public_tools_do_not_require_review_or_admin():
    gate = AuthorizationGate(policy=AuthorizationConfig(mode="relaxed"))
    for tool in ("search_files", "web_fetch", "send_local_attachment"):
        await gate.authorize_call(tool, ACCOUNT)
    with pytest.raises(ActionDenied) as caught:
        await gate.authorize_call("exec", ACCOUNT)
    assert caught.value.code == "risk_review_unavailable"


def make_model_reviewer(chat, **kwargs):
    router = SimpleNamespace(
        resolve=lambda spec: SimpleNamespace(
            slot=SimpleNamespace(provider=SimpleNamespace(chat=chat)),
            model="review-model",
        )
    )
    return ModelRiskReviewer(router, RiskReviewConfig(**kwargs))


@pytest.mark.asyncio
async def test_model_review_has_no_history_or_tools_and_restores_context():
    captured = {}

    async def chat(**kwargs):
        captured.update(kwargs)
        assert not current_provider_request_context.get().allow_builtin_tools
        return ProviderResponse(
            content='{"verdict":"allow","reason":"ordinary search","evidence":""}'
        )

    before = current_provider_request_context.get()
    verdict = await make_model_reviewer(chat).review(
        RiskReviewRequest(
            "exec",
            {"command": "grep Elsa docs/*"},
            "find Elsa",
        )
    )
    assert verdict.verdict == "allow"
    assert len(captured["messages"]) == 2
    assert captured["tools"] == []
    assert current_provider_request_context.get() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "yes",
        '{"verdict":"allow"}',
        '{"verdict":"deny","reason":"suspicious","evidence":""}',
        '{"verdict":"maybe","reason":"unknown","evidence":""}',
    ],
)
async def test_unusable_review_never_authorizes(content):
    with pytest.raises(RiskReviewUnavailable):
        await make_model_reviewer(
            AsyncMock(return_value=ProviderResponse(content=content))
        ).review(RiskReviewRequest("exec", {"command": "python script.py"}))


@pytest.mark.asyncio
async def test_oversized_script_not_silently_truncated():
    chat = AsyncMock()
    with pytest.raises(RiskReviewUnavailable, match="too large"):
        await make_model_reviewer(chat, max_input_chars=1000).review(
            RiskReviewRequest("exec", {"command": "x" * 2000})
        )
    chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_propagates_and_restores_provider_context():
    before = current_provider_request_context.get()
    with pytest.raises(asyncio.CancelledError):
        await make_model_reviewer(
            AsyncMock(side_effect=asyncio.CancelledError())
        ).review(RiskReviewRequest("exec", {}))
    assert current_provider_request_context.get() == before
