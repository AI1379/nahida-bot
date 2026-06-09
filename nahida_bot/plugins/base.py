"""Re-export shim — canonical types live in nahida_bot_sdk."""

# pyright: reportUnusedImport=false

from nahida_bot_sdk.api import (  # noqa: F401
    BotAPI,
    ChannelService,
    PluginLogger,
    SubscriptionHandle,
    WebhookHandle,
    WebhookRequest,
    WebhookResponse,
)
from nahida_bot_sdk.chat_address import ChatAddress  # noqa: F401
from nahida_bot_sdk.commands import (  # noqa: F401
    CommandHandlerResult,
    CommandInfo,
    CommandMatch,
    CommandResult,
)
from nahida_bot_sdk.manifest import PluginManifest  # noqa: F401
from nahida_bot_sdk.messaging import (  # noqa: F401
    Attachment,
    ChatContext,
    InboundAttachment,
    InboundMessage,
    MediaDownloadResult,
    MessageContext,
    OutboundMessage,
    SenderContext,
)
from nahida_bot_sdk.plugin import (  # noqa: F401
    MemoryRef,
    Plugin,
    SessionInfo,
    register_command,
    register_tool,
    subscribe,
)
