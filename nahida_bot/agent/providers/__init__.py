"""Agent provider implementations and contracts."""

from nahida_bot.agent.providers.base import (
    ChatProvider,
    ModelCapabilities,
    ProviderResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from nahida_bot.agent.providers.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from nahida_bot.agent.providers.anthropic import AnthropicProvider
from nahida_bot.agent.providers.openai_compatible import OpenAICompatibleProvider
from nahida_bot.agent.providers.openai_responses import OpenAIResponsesProvider
from nahida_bot.agent.providers.reasoning import (
    ReasoningPolicy,
    extract_think_tags,
)
from nahida_bot.agent.providers.registry import (
    ProviderDescriptor,
    clear_runtime_providers,
    create_provider,
    get_provider_class,
    list_providers,
    register_provider,
    register_runtime_provider,
    unregister_runtime_provider,
)
from nahida_bot.agent.providers.router import ModelRouter, RoutedModel

# Import provider subclasses to trigger @register_provider decorators.
# These are intentionally imported for their side effects.
import nahida_bot.agent.providers.deepseek as _deepseek  # noqa: F401  # pyright: ignore[reportUnusedImport]
import nahida_bot.agent.providers.glm as _glm  # noqa: F401  # pyright: ignore[reportUnusedImport]
import nahida_bot.agent.providers.groq as _groq  # noqa: F401  # pyright: ignore[reportUnusedImport]
import nahida_bot.agent.providers.minimax as _minimax  # noqa: F401  # pyright: ignore[reportUnusedImport]
import nahida_bot.agent.providers.anthropic as _anthropic  # noqa: F401  # pyright: ignore[reportUnusedImport]
import nahida_bot.agent.providers.openai_responses as _openai_responses  # noqa: F401  # pyright: ignore[reportUnusedImport]

__all__ = [
    "AnthropicProvider",
    "ChatProvider",
    "ModelCapabilities",
    "ModelRouter",
    "OpenAICompatibleProvider",
    "OpenAIResponsesProvider",
    "ProviderAuthError",
    "ProviderBadResponseError",
    "ProviderDescriptor",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderResponse",
    "RoutedModel",
    "ProviderTimeoutError",
    "ProviderTransportError",
    "ReasoningPolicy",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
    "clear_runtime_providers",
    "create_provider",
    "extract_think_tags",
    "get_provider_class",
    "list_providers",
    "register_provider",
    "register_runtime_provider",
    "unregister_runtime_provider",
]
