"""Re-export shim — canonical types live in nahida_bot_sdk."""

# pyright: reportUnusedImport=false

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
