"""nahida-bot-sdk — public types for nahida-bot plugin development."""

# Chat addressing
from nahida_bot_sdk.chat_address import (  # noqa: F401
    KNOWN_TARGET_TYPES,
    TARGET_TYPE_UNKNOWN,
    VALID_TARGET_TYPES,
    ChatAddress,
    SessionKey,
    SessionKeyKind,
    TargetType,
    classify_session_key,
    is_valid_target_type,
    normalize_target_type,
)

# Messaging
from nahida_bot_sdk.messaging import (  # noqa: F401
    Attachment,
    AttentionFrame,
    ChatContext,
    InboundAttachment,
    InboundMessage,
    MediaDownloadResult,
    MessageContext,
    OutboundMessage,
    SenderContext,
)

# API protocols
from nahida_bot_sdk.api import (  # noqa: F401
    BotAPI,
    ChannelService,
    LLMResponse,
    LLMUsage,
    ManagedTempFile,
    PluginLogger,
    SubagentResult,
    SubscriptionHandle,
    WebhookHandle,
    WebhookRequest,
    WebhookResponse,
)

# Plugin base
from nahida_bot_sdk.plugin import (  # noqa: F401
    MemoryRef,
    Plugin,
    SessionInfo,
    register_command,
    register_tool,
    subscribe,
)

# Manifest
from nahida_bot_sdk.manifest import (  # noqa: F401
    Capabilities,
    FilesystemPermission,
    ManifestParseError,
    MemoryPermission,
    NetworkPermission,
    Permissions,
    PluginDataPermission,
    PluginDependency,
    PluginManifest,
    SystemPermission,
    parse_manifest,
)

# Events
from nahida_bot_sdk.events import (  # noqa: F401
    AgentResponseRequested,
    AgentResponseRequestPayload,
    AgentRunCancelled,
    AgentRunFinished,
    AgentRunPayload,
    AgentRunStarted,
    AgentStopPayload,
    AgentStopRequested,
    AppInitializing,
    AppLifecyclePayload,
    AppStarted,
    AppStopped,
    AppStopping,
    Event,
    MessageObserved,
    MessagePayload,
    MessageReactionEvent,
    MessageReactionPayload,
    MessageReceived,
    MessageSending,
    MessageSent,
    PluginDisabled,
    PluginEnabled,
    PluginErrorOccurred,
    PluginErrorPayload,
    PluginLoaded,
    PluginPayload,
    PluginUnloaded,
    PokeEvent,
    PokePayload,
    SchedulerNotification,
    SchedulerNotificationPayload,
)

# Commands
from nahida_bot_sdk.commands import (  # noqa: F401
    CommandHandlerResult,
    CommandInfo,
    CommandMatch,
    CommandResult,
)

# Testing utilities
from nahida_bot_sdk.testing import (  # noqa: F401
    ConsoleMockBotAPI,
    MockBotAPI,
    RecordingMockBotAPI,
    StubChannelService,
    load_plugin_for_test,
)

__all__ = [
    # Chat addressing
    "ChatAddress",
    "KNOWN_TARGET_TYPES",
    "SessionKey",
    "SessionKeyKind",
    "TARGET_TYPE_UNKNOWN",
    "TargetType",
    "VALID_TARGET_TYPES",
    "classify_session_key",
    "is_valid_target_type",
    "normalize_target_type",
    # Messaging
    "Attachment",
    "AttentionFrame",
    "ChatContext",
    "InboundAttachment",
    "InboundMessage",
    "MediaDownloadResult",
    "MessageContext",
    "OutboundMessage",
    "SenderContext",
    # API protocols
    "BotAPI",
    "ChannelService",
    "LLMResponse",
    "LLMUsage",
    "ManagedTempFile",
    "PluginLogger",
    "SubagentResult",
    "SubscriptionHandle",
    "WebhookHandle",
    "WebhookRequest",
    "WebhookResponse",
    # Plugin base
    "MemoryRef",
    "Plugin",
    "SessionInfo",
    "register_command",
    "register_tool",
    "subscribe",
    # Manifest
    "Capabilities",
    "FilesystemPermission",
    "ManifestParseError",
    "MemoryPermission",
    "NetworkPermission",
    "Permissions",
    "PluginDataPermission",
    "PluginDependency",
    "PluginManifest",
    "SystemPermission",
    "parse_manifest",
    # Events
    "AgentResponseRequested",
    "AgentResponseRequestPayload",
    "AgentRunCancelled",
    "AgentRunFinished",
    "AgentRunPayload",
    "AgentRunStarted",
    "AgentStopPayload",
    "AgentStopRequested",
    "AppInitializing",
    "AppLifecyclePayload",
    "AppStarted",
    "AppStopped",
    "AppStopping",
    "Event",
    "MessageObserved",
    "MessagePayload",
    "MessageReactionEvent",
    "MessageReactionPayload",
    "MessageReceived",
    "MessageSending",
    "MessageSent",
    "PluginDisabled",
    "PluginEnabled",
    "PluginErrorOccurred",
    "PluginErrorPayload",
    "PluginLoaded",
    "PluginPayload",
    "PluginUnloaded",
    "PokeEvent",
    "PokePayload",
    "SchedulerNotification",
    "SchedulerNotificationPayload",
    # Commands
    "CommandHandlerResult",
    "CommandInfo",
    "CommandMatch",
    "CommandResult",
    # Testing
    "ConsoleMockBotAPI",
    "MockBotAPI",
    "RecordingMockBotAPI",
    "StubChannelService",
    "load_plugin_for_test",
]
