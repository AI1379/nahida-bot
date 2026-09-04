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

# FIXME: This seems to be a part of channel plugins instead of core

# Platforms whose outbound path converts mention tokens into real mention
# segments. Other platforms leave tokens as literal text.
MENTION_CAPABLE_PLATFORMS = frozenset({"milky", "feishu"})

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
    seen: dict[str, None] = {}
    for part in parse_outbound_parts(text):
        if part.is_mention and part.user_id not in seen:
            seen[part.user_id] = None
            if len(seen) >= limit:
                break
    return list(seen)
