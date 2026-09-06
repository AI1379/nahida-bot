"""Outbound mention tokens: parse LLM-emitted @-tokens into structured parts.

The LLM addresses a specific user by writing an inline mention token in its
reply text. The canonical taught format is the CQ at-code::

    [CQ:at,qq=123456]

The alias forms ``@[qq=123456]`` and ``@[user_id=123456]`` are also parsed
because the model may copy them from rendered history. Ids are either numeric
platform ids (QQ) or Feishu open_ids (``ou_xxxxxxxx``), matched by prefix so
stray tokens with other shapes stay literal. Tokens are only converted to
real mention segments by channels after the target has been validated (see
the Milky and Feishu plugins' membership checks); unvalidated tokens stay in
the text verbatim, so a wrong or hallucinated user id degrades to literal
text instead of breaking the send.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# One scanner over all accepted token forms. The CQ form is checked first so
# the canonical syntax wins when forms overlap. Ids are digits (QQ) or a
# Feishu open_id (ou_ + alphanumeric); other shapes never match and stay
# literal.
_MENTION_TOKEN_RE = re.compile(
    r"\[CQ:at,qq=(?P<cq_id>\d+|ou_[0-9A-Za-z]+)\]"
    r"|@\[qq=(?P<qq_id>\d+|ou_[0-9A-Za-z]+)\]"
    r"|@\[user_id=(?P<uid_id>\d+|ou_[0-9A-Za-z]+)\]"
)


@dataclass(slots=True, frozen=True)
class OutboundPart:
    """One part of an outbound text: literal text or a mention token."""

    text: str = ""
    user_id: str = ""
    raw: str = ""

    @property
    def is_mention(self) -> bool:
        return bool(self.user_id)


def parse_outbound_parts(text: str) -> list[OutboundPart]:
    """Split outbound text into literal chunks and mention tokens, in order."""
    if not text:
        return []
    parts: list[OutboundPart] = []
    cursor = 0
    for match in _MENTION_TOKEN_RE.finditer(text):
        if match.start() > cursor:
            parts.append(OutboundPart(text=text[cursor : match.start()]))
        user_id = next(
            value
            for value in (
                match.group("cq_id"),
                match.group("qq_id"),
                match.group("uid_id"),
            )
            if value is not None
        )
        parts.append(OutboundPart(user_id=user_id, raw=match.group(0)))
        cursor = match.end()
    if cursor < len(text):
        parts.append(OutboundPart(text=text[cursor:]))
    return parts


def extract_mention_ids(text: str, *, limit: int) -> list[str]:
    """Return unique mention target ids in order of first appearance, capped.

    Tokens beyond ``limit`` unique targets are not returned; callers leave
    those unconverted (literal) in the outgoing text.
    """
    if limit <= 0:
        return []
    seen: dict[str, None] = {}
    for part in parse_outbound_parts(text):
        if part.is_mention and part.user_id not in seen:
            seen[part.user_id] = None
            if len(seen) >= limit:
                break
    return list(seen)


def build_mention_instruction(*, id_description: str, max_targets: int) -> str:
    """Render the active channel's mention contract from its configuration."""
    return (
        "## Mentioning Users\n"
        "To notify a specific group member, write [CQ:at,qq=<user_id>] inline.\n"
        f"- Use {id_description} exactly as shown in the sender context or received "
        "at-tokens. Never invent or guess IDs.\n"
        "- If you do not know the ID, address the person by name in plain text.\n"
        "- Group chats only; in private chats just use names.\n"
        f"- Use at most {max_targets} distinct mention targets per message, usually "
        "only one, and only when directing the reply at someone or needing their attention.\n"
        "- In scheduled or proactive runs be extra conservative: a mention notifies the person."
    )
