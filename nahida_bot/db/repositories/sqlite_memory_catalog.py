"""Structured memory lookup; apply audience and subject filters before LIMIT."""

from __future__ import annotations

from typing import Any

from nahida_bot.db.engine import DatabaseEngine


class SQLiteMemoryCatalog:
    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def select(
        self,
        *,
        scopes: list[tuple[str, str]],
        category: str,
        subjects: list[tuple[str, str]],
        public_recall: bool,
        account_owner: str = "",
        chat_owner: str = "",
        query: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        from nahida_bot.db.repositories.sqlite_memory_repo import SQLiteMemoryRepository
        from nahida_bot.agent.memory.subjects import MEMORY_CATEGORIES

        if category not in MEMORY_CATEGORIES or not scopes or limit <= 0:
            return []
        visibility, params = [], []
        for scope_type, scope_id in scopes:
            clause = "(scope_type=? AND scope_id=?)"
            if scope_type == "global":
                clause = "(scope_type=? AND scope_id=? AND sensitivity='public')"
            elif scope_type == "account" and scope_id != account_owner:
                clause = "(scope_type=? AND scope_id=? AND sensitivity!='secret_like' AND COALESCE(json_extract(metadata_json,'$.portable'),1) != 0)"
            visibility.append(clause)
            params.extend((scope_type, scope_id))
        if public_recall:
            visibility.append(
                "(sensitivity='public' AND COALESCE(json_extract(metadata_json, '$.portable'),1) != 0)"
            )
        kind = "json_extract(metadata_json, '$.attribution.subject.kind')"
        if category == "unknown":
            kind = f"COALESCE({kind},'unknown')"
        identity = "json_extract(metadata_json, '$.attribution.subject.id')"
        conditions = ["status='active'", "(" + " OR ".join(visibility) + ")"]
        conditions.append(
            "(sensitivity!='secret_like' OR (scope_type='chat' AND scope_id=?) OR (EXISTS (SELECT 1 FROM json_each(metadata_json,'$.attribution.evidence') e WHERE json_extract(e.value,'$.chat_id')=?) AND NOT EXISTS (SELECT 1 FROM json_each(metadata_json,'$.attribution.evidence') e WHERE COALESCE(json_extract(e.value,'$.chat_id'),'') NOT IN ('',?))))"
        )
        params.extend((chat_owner, chat_owner, chat_owner))
        if category == "person":
            conditions.append(f"{kind} IN ('account','person')")
        else:
            conditions.append(f"{kind}=?")
            params.append(category)
        if category in {"person", "relationship", "chat"}:
            if not subjects:
                return []
            matches = []
            for subject_kind, subject_id in dict.fromkeys(subjects):
                if category == "relationship":
                    matches.append(
                        "EXISTS (SELECT 1 FROM json_each(metadata_json, '$.attribution.subject.members') AS member WHERE json_extract(member.value,'$.kind')=? AND json_extract(member.value,'$.id')=?)"
                    )
                else:
                    matches.append(f"({kind}=? AND {identity}=?)")
                params.extend((subject_kind, subject_id))
            conditions.append("(" + " OR ".join(matches) + ")")
        if query:
            conditions.append(
                "(instr(lower(content),lower(?))>0 OR instr(lower(title),lower(?))>0)"
            )
            params.extend((query, query))
        rows = await self._engine.fetch_all(
            "SELECT *, 0.0 AS score FROM memory_items WHERE "
            + " AND ".join(conditions)
            + " ORDER BY importance DESC, updated_at DESC, item_id LIMIT ?",
            (*params, min(limit, 100)),
        )
        return [SQLiteMemoryRepository._memory_item_row_to_dict(row) for row in rows]
