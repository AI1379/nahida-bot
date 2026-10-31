"""What a memory is about, independently of who may read it.

Accounts are stable local people; a Person is only a confirmed cross-channel
equivalence. Relationships retain their participants instead of becoming an
unattributed sentence about 'the users'. No graph inference is performed here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

SubjectKind = Literal[
    "bot", "environment", "account", "person", "chat", "relationship", "unknown"
]
MEMORY_CATEGORIES = ("bot", "environment", "person", "chat", "relationship", "unknown")


@dataclass(frozen=True, slots=True)
class MemorySubject:
    kind: SubjectKind = "unknown"
    id: str = ""
    members: tuple[MemorySubject, ...] = ()

    @property
    def category(self) -> str:
        return "person" if self.kind == "account" else self.kind

    @classmethod
    def parse(cls, value: object) -> MemorySubject:
        if not isinstance(value, dict):
            return cls()
        kind, identity = value.get("kind"), value.get("id", "")
        if kind == "relationship":
            raw_members = value.get("members")
            if (
                not isinstance(raw_members, (list, tuple))
                or not 2 <= len(raw_members) <= 4
            ):
                return cls()
            # Reject recursive relations and names masquerading as accounts.
            if any(
                not isinstance(m, dict) or m.get("kind") not in {"account", "person"}
                for m in raw_members
            ):
                return cls()
            members = tuple(
                sorted(
                    {cls.parse(m) for m in raw_members}, key=lambda m: (m.kind, m.id)
                )
            )
            if len(members) < 2 or any(m.kind == "unknown" for m in members):
                return cls()
            key = "\n".join(f"{m.kind}:{m.id}" for m in members)
            return cls(
                "relationship", hashlib.sha256(key.encode()).hexdigest()[:24], members
            )
        if kind == "bot":
            return cls("bot", "self")
        if kind == "environment":
            return cls("environment", "runtime")
        if not isinstance(identity, str) or not identity.strip():
            return cls()
        identity = identity.strip()
        if kind == "account":
            from nahida_bot.identity.models import AccountKey

            try:
                AccountKey.parse(identity)
            except ValueError:
                return cls()
            return cls("account", identity)
        if kind == "person":
            return cls("person", identity)
        if kind == "chat":
            return cls("chat", identity)
        return cls()

    def label(self) -> str:
        if self.kind == "relationship":
            return "relationship:(" + ", ".join(m.label() for m in self.members) + ")"
        return f"{self.kind}:{self.id or 'unknown'}"


def tool_subject(
    selector: str,
    *,
    current_account: str = "",
    chat_key: str = "",
    account_key: str = "",
    relationship_accounts: list[str] | None = None,
) -> MemorySubject:
    """Resolve constrained tool selectors without inventing identity or sources."""
    if relationship_accounts and selector != "relationship":
        raise ValueError("relationship participants require a relationship subject")
    if selector == "relationship":
        if account_key:
            raise ValueError("a relationship cannot also have a single-account subject")
        result = MemorySubject.parse(
            {
                "kind": "relationship",
                "members": [
                    {"kind": "account", "id": key}
                    for key in (relationship_accounts or [])
                ],
            }
        )
        if result.kind != "relationship":
            raise ValueError(
                "relationships require 2-4 distinct identified participants"
            )
        return result
    if account_key:
        if selector not in {"unknown", "current_sender"} or (
            selector == "current_sender" and account_key != current_account
        ):
            raise ValueError("conflicting memory subjects")
        result = MemorySubject.parse({"kind": "account", "id": account_key})
        if result.kind != "account":
            raise ValueError("invalid subject account key")
        return result
    if selector == "current_sender":
        return MemorySubject.parse({"kind": "account", "id": current_account})
    if selector == "chat":
        return MemorySubject.parse({"kind": "chat", "id": chat_key})
    if selector in {"bot", "environment"}:
        return MemorySubject.parse({"kind": selector})
    return MemorySubject()
