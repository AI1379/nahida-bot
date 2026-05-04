"""Milky channel plugin."""

from nahida_bot.channels.milky.config import (
    GroupTriggerMode,
    MilkyPluginConfig,
    parse_milky_config,
)
from nahida_bot.channels.milky.client import (
    MilkyAPIError,
    MilkyAuthError,
    MilkyClient,
    MilkyClientClosedError,
    MilkyClientError,
    MilkyHTTPStatusError,
    MilkyNetworkError,
    MilkyResponseError,
)
from nahida_bot.channels.milky.event_stream import MilkyEventStream
from nahida_bot.channels.milky.message_converter import MilkyMessageConverter
from nahida_bot.channels.milky.plugin import MilkyPlugin
from nahida_bot.channels.milky.segment_converter import (
    MilkyOutboundConverter,
    MilkyTargetError,
)
from nahida_bot.channels.milky.segments import (
    IncomingForwardSegment,
    IncomingForwardedMessage,
    IncomingSegment,
    OutgoingFileUpload,
    OutgoingForwardSegment,
    OutgoingForwardedMessage,
    OutgoingSegment,
    parse_incoming_segment,
    parse_incoming_segments,
    outgoing_segments_to_dicts,
)

__all__ = [
    "GroupTriggerMode",
    "IncomingForwardSegment",
    "IncomingForwardedMessage",
    "IncomingSegment",
    "MilkyAPIError",
    "MilkyAuthError",
    "MilkyClient",
    "MilkyClientClosedError",
    "MilkyClientError",
    "MilkyEventStream",
    "MilkyHTTPStatusError",
    "MilkyMessageConverter",
    "MilkyNetworkError",
    "MilkyOutboundConverter",
    "MilkyPlugin",
    "MilkyPluginConfig",
    "MilkyResponseError",
    "MilkyTargetError",
    "OutgoingFileUpload",
    "OutgoingForwardSegment",
    "OutgoingForwardedMessage",
    "OutgoingSegment",
    "outgoing_segments_to_dicts",
    "parse_incoming_segment",
    "parse_incoming_segments",
    "parse_milky_config",
]
