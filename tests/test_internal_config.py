"""Tests for AgentConfig, ContextConfig, SchedulerConfigModel, RouterConfigModel."""

import pytest
from pydantic import ValidationError

from nahida_bot.core.config import (
    AgentConfig,
    ContextConfig,
    MemoryConfig,
    MemoryConsolidationConfig,
    RouterConfigModel,
    SchedulerConfigModel,
    Settings,
)


class TestAgentConfig:
    def test_defaults(self) -> None:
        cfg = AgentConfig()
        assert cfg.max_steps == 8
        assert cfg.provider_timeout_seconds == 30.0
        assert cfg.retry_attempts == 2
        assert cfg.retry_backoff_seconds == 0.2
        assert cfg.tool_timeout_seconds == 135.0
        assert cfg.tool_retry_attempts == 1
        assert cfg.tool_retry_backoff_seconds == 0.1
        assert cfg.max_tool_log_chars == 400
        assert "Tool use policy" in cfg.tool_use_system_prompt
        assert "{code}" in cfg.provider_error_template

    def test_custom_values(self) -> None:
        cfg = AgentConfig(
            max_steps=16,
            provider_timeout_seconds=60.0,
            retry_attempts=3,
            retry_backoff_seconds=0.5,
            tool_timeout_seconds=200.0,
            tool_retry_attempts=2,
            tool_retry_backoff_seconds=0.2,
            max_tool_log_chars=800,
            tool_use_system_prompt="Custom prompt",
            provider_error_template="Error: {code}",
        )
        assert cfg.max_steps == 16
        assert cfg.provider_timeout_seconds == 60.0
        assert cfg.tool_use_system_prompt == "Custom prompt"

    def test_negative_values_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentConfig(max_steps=0)
        with pytest.raises(ValidationError):
            AgentConfig(provider_timeout_seconds=-1)
        with pytest.raises(ValidationError):
            AgentConfig(retry_attempts=-1)
        with pytest.raises(ValidationError):
            AgentConfig(tool_timeout_seconds=-1)
        with pytest.raises(ValidationError):
            AgentConfig(max_tool_log_chars=-1)


class TestContextConfig:
    def test_defaults(self) -> None:
        cfg = ContextConfig()
        assert cfg.max_tokens == 8000
        assert cfg.reserved_tokens == 1000
        assert cfg.max_chars is None
        assert cfg.reserved_chars == 0
        assert cfg.summary_max_chars == 600
        assert cfg.reasoning_policy == "budget"
        assert cfg.max_reasoning_tokens == 2000

    def test_custom_values(self) -> None:
        cfg = ContextConfig(
            max_tokens=16000,
            reserved_tokens=2000,
            max_chars=50000,
            reserved_chars=1000,
            summary_max_chars=1000,
            reasoning_policy="strip",
            max_reasoning_tokens=4000,
        )
        assert cfg.max_tokens == 16000
        assert cfg.max_chars == 50000
        assert cfg.reasoning_policy == "strip"

    @pytest.mark.parametrize("policy", ["strip", "append", "budget"])
    def test_valid_reasoning_policies(self, policy: str) -> None:
        cfg = ContextConfig(reasoning_policy=policy)  # type: ignore
        assert cfg.reasoning_policy == policy

    @pytest.mark.parametrize("policy", ["bad", "", "BUDGET", "full"])
    def test_invalid_reasoning_policy_rejected(self, policy: str) -> None:
        with pytest.raises(ValidationError):
            ContextConfig(reasoning_policy=policy)  # type: ignore

    def test_negative_values_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContextConfig(max_tokens=0)
        with pytest.raises(ValidationError):
            ContextConfig(reserved_tokens=-1)
        with pytest.raises(ValidationError):
            ContextConfig(summary_max_chars=-1)
        with pytest.raises(ValidationError):
            ContextConfig(max_reasoning_tokens=-1)


class TestSchedulerConfigModel:
    def test_defaults(self) -> None:
        cfg = SchedulerConfigModel()
        assert cfg.poll_interval_seconds == 1.0
        assert cfg.max_concurrent_fires == 5
        assert cfg.job_timeout_seconds == 120.0
        assert cfg.min_interval_seconds == 60
        assert cfg.max_prompt_chars == 4000
        assert cfg.max_jobs_per_chat == 20
        assert cfg.failure_retry_seconds == 300
        assert cfg.max_consecutive_failures == 3

    def test_custom_values(self) -> None:
        cfg = SchedulerConfigModel(
            poll_interval_seconds=2.0,
            max_concurrent_fires=10,
            job_timeout_seconds=300.0,
            max_jobs_per_chat=50,
        )
        assert cfg.poll_interval_seconds == 2.0
        assert cfg.max_concurrent_fires == 10
        assert cfg.job_timeout_seconds == 300.0
        assert cfg.max_jobs_per_chat == 50

    def test_negative_values_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SchedulerConfigModel(poll_interval_seconds=0)
        with pytest.raises(ValidationError):
            SchedulerConfigModel(max_concurrent_fires=0)
        with pytest.raises(ValidationError):
            SchedulerConfigModel(job_timeout_seconds=0)
        with pytest.raises(ValidationError):
            SchedulerConfigModel(min_interval_seconds=0)
        with pytest.raises(ValidationError):
            SchedulerConfigModel(max_jobs_per_chat=-1)


class TestRouterConfigModel:
    def test_defaults(self) -> None:
        cfg = RouterConfigModel()
        assert cfg.system_prompt == "You are a helpful assistant."
        assert cfg.max_history_turns == 50
        assert cfg.agent_enabled is True
        assert cfg.command_timeout_seconds == 30.0
        assert "timed out" in cfg.command_timeout_message
        assert cfg.reply_to_inbound is True

    def test_custom_values(self) -> None:
        cfg = RouterConfigModel(
            system_prompt="Custom prompt",
            max_history_turns=100,
            agent_enabled=False,
            command_timeout_seconds=60.0,
            command_timeout_message="Timeout!",
            reply_to_inbound=False,
        )
        assert cfg.system_prompt == "Custom prompt"
        assert cfg.max_history_turns == 100
        assert cfg.agent_enabled is False
        assert cfg.command_timeout_message == "Timeout!"
        assert cfg.reply_to_inbound is False

    def test_negative_values_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RouterConfigModel(max_history_turns=0)
        with pytest.raises(ValidationError):
            RouterConfigModel(command_timeout_seconds=-1)


class TestSettingsSubConfigs:
    def test_default_agent(self) -> None:
        s = Settings()
        assert isinstance(s.agent, AgentConfig)
        assert s.agent.max_steps == 8

    def test_default_context(self) -> None:
        s = Settings()
        assert isinstance(s.context, ContextConfig)
        assert s.context.max_tokens == 8000

    def test_default_scheduler(self) -> None:
        s = Settings()
        assert isinstance(s.scheduler, SchedulerConfigModel)
        assert s.scheduler.max_concurrent_fires == 5
        assert s.scheduler.memory_dreaming_enabled is True
        assert s.scheduler.memory_dreaming_interval_seconds == 3600
        assert s.scheduler.memory_dreaming_provider_id == ""
        assert s.scheduler.memory_dreaming_model == ""

    def test_default_router(self) -> None:
        s = Settings()
        assert isinstance(s.router, RouterConfigModel)
        assert s.router.max_history_turns == 50

    def test_default_memory(self) -> None:
        s = Settings()
        assert isinstance(s.memory, MemoryConfig)
        assert isinstance(s.memory.consolidation, MemoryConsolidationConfig)
        assert s.memory.consolidation.rule_based_enabled is True

    def test_agent_from_dict(self) -> None:
        s = Settings.model_validate({"agent": {"max_steps": 16}})
        assert s.agent.max_steps == 16
        assert s.agent.provider_timeout_seconds == 30.0  # unchanged default

    def test_context_from_dict(self) -> None:
        s = Settings.model_validate(
            {"context": {"max_tokens": 16000, "reasoning_policy": "strip"}}
        )
        assert s.context.max_tokens == 16000
        assert s.context.reasoning_policy == "strip"

    def test_scheduler_from_dict(self) -> None:
        s = Settings.model_validate(
            {
                "scheduler": {
                    "max_jobs_per_chat": 50,
                    "memory_dreaming_interval_seconds": 7200,
                    "memory_dreaming_provider_id": "cheap",
                    "memory_dreaming_model": "cheap-model",
                }
            }
        )
        assert s.scheduler.max_jobs_per_chat == 50
        assert s.scheduler.memory_dreaming_interval_seconds == 7200
        assert s.scheduler.memory_dreaming_provider_id == "cheap"
        assert s.scheduler.memory_dreaming_model == "cheap-model"

    def test_router_from_dict(self) -> None:
        s = Settings.model_validate(
            {"router": {"max_history_turns": 100, "agent_enabled": False}}
        )
        assert s.router.max_history_turns == 100
        assert s.router.agent_enabled is False

    def test_memory_consolidation_from_dict(self) -> None:
        s = Settings.model_validate(
            {"memory": {"consolidation": {"rule_based_enabled": False}}}
        )
        assert s.memory.consolidation.rule_based_enabled is False
