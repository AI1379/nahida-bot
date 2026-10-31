"""Atomic identity changes and their audit records share one transaction."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.identity.models import AccountKey


class SQLiteIdentityMutations:
    def __init__(self, engine: DatabaseEngine) -> None:
        self.db = engine

    @asynccontextmanager
    async def transaction(self):
        async with self.db.write_lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                await self.db.db.commit()
            except BaseException:
                await self.db.db.rollback()
                raise

    async def audit(
        self, action: str, actor: str, target: str, before: dict, after: dict
    ) -> None:
        await self.db.execute(
            "INSERT INTO identity_audit_log(action,actor,target_type,target_id,before_json,after_json,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                action,
                actor,
                "candidate"
                if action.startswith("identity.")
                else "person"
                if action == "person.upsert"
                else "account",
                target,
                json.dumps(before),
                json.dumps(after),
                "{}",
                datetime.now(UTC).isoformat(),
            ),
        )

    async def person_for(self, account: str) -> str:
        row = await self.db.fetch_one(
            "SELECT pa.person_id FROM person_accounts pa JOIN persons p USING(person_id) WHERE account_key=? AND pa.status='active' AND p.status='active'",
            (account,),
        )
        return str(row["person_id"]) if row else ""

    async def person(self, person_id: str, display_name: str, actor: str) -> None:
        async with self.transaction():
            before = await self.db.fetch_one(
                "SELECT person_id,display_name,status FROM persons WHERE person_id=?",
                (person_id,),
            )
            now = datetime.now(UTC).isoformat()
            await self.db.execute(
                "INSERT INTO persons(person_id,display_name,status,metadata_json,created_at,updated_at) VALUES (?,?,'active','{}',?,?) ON CONFLICT(person_id) DO UPDATE SET display_name=excluded.display_name,status='active',updated_at=excluded.updated_at",
                (person_id, display_name, now, now),
            )
            await self.audit(
                "person.upsert",
                actor,
                person_id,
                dict(before) if before else {},
                {
                    "person_id": person_id,
                    "display_name": display_name,
                    "status": "active",
                },
            )

    async def _link(
        self,
        account: str,
        person_id: str,
        actor: str,
        label: str = "",
        verification: str = "manual_link",
    ) -> None:
        parsed = AccountKey.parse(account)
        existing = await self.db.fetch_one(
            "SELECT person_id,verification FROM person_accounts WHERE account_key=? AND status='active'",
            (account,),
        )
        collision = await self.db.fetch_one(
            "SELECT account_key FROM person_accounts WHERE person_id=? AND channel=? AND account_key!=? AND status='active'",
            (person_id, parsed.channel, account),
        )
        if collision:
            raise ValueError("a Person may only link different channels")
        await self.db.execute(
            "INSERT INTO person_accounts(account_key,person_id,channel,account_type,platform_account_id,label,status,verification,linked_by,linked_at,metadata_json) VALUES (?,?,?,'user',?,?,'active',?,?,?,'{}') "
            "ON CONFLICT(account_key) DO UPDATE SET person_id=excluded.person_id,status='active',verification=excluded.verification,linked_by=excluded.linked_by,linked_at=excluded.linked_at,label=excluded.label",
            (
                account,
                person_id,
                parsed.channel,
                parsed.platform_user_id,
                label,
                verification,
                actor,
                datetime.now(UTC).isoformat(),
            ),
        )
        await self.audit(
            "account.link",
            actor,
            account,
            dict(existing) if existing else {},
            {"person_id": person_id, "verification": verification},
        )

    async def link(
        self, account: str, person_id: str, actor: str, label: str = ""
    ) -> None:
        async with self.transaction():
            person = await self.db.fetch_one(
                "SELECT person_id FROM persons WHERE person_id=? AND status='active'",
                (person_id,),
            )
            if not person:
                raise ValueError(f"person {person_id!r} does not exist")
            await self._link(account, person_id, actor, label)

    async def unlink(self, account: str, actor: str) -> bool:
        async with self.transaction():
            before = await self.db.fetch_one(
                "SELECT person_id,verification FROM person_accounts WHERE account_key=? AND status='active'",
                (account,),
            )
            if not before:
                return False
            await self.db.execute(
                "UPDATE person_accounts SET status='inactive' WHERE account_key=?",
                (account,),
            )
            await self.audit(
                "account.unlink", actor, account, dict(before), {"status": "inactive"}
            )
            return True

    async def propose(
        self, requester: str, target: str, evidence: dict[str, Any]
    ) -> dict[str, Any]:
        source_key, target_key = AccountKey.parse(requester), AccountKey.parse(target)
        if source_key.channel == target_key.channel:
            raise ValueError("identity candidates must span different channels")
        async with self.transaction():
            existing = await self.db.fetch_one(
                "SELECT * FROM identity_link_candidates WHERE status='pending' AND ((requester_account=? AND target_account=?) OR (requester_account=? AND target_account=?))",
                (requester, target, target, requester),
            )
            if existing:
                return self.public_candidate(dict(existing))
            snapshot = {a: await self.person_for(a) for a in (requester, target)}
            if snapshot[requester] and snapshot[requester] == snapshot[target]:
                raise ValueError("these accounts are already linked")
            candidate_id = "link_" + uuid4().hex[:16]
            await self.db.execute(
                "INSERT INTO identity_link_candidates(candidate_id,requester_account,target_account,evidence_json,identity_snapshot_json,created_at) VALUES (?,?,?,?,?,?)",
                (
                    candidate_id,
                    requester,
                    target,
                    json.dumps(evidence, ensure_ascii=False),
                    json.dumps(snapshot),
                    datetime.now(UTC).isoformat(),
                ),
            )
            await self.audit(
                "identity.propose",
                requester,
                candidate_id,
                {},
                {"requester_account": requester, "target_account": target},
            )
            return {
                "candidate_id": candidate_id,
                "requester_account": requester,
                "target_account": target,
                "status": "pending",
            }

    async def candidates(self, actor: str) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT * FROM identity_link_candidates WHERE status='pending' AND (requester_account=? OR target_account=?) ORDER BY created_at DESC LIMIT 50",
            (actor, actor),
        )
        return [self.public_candidate(dict(row)) for row in rows]

    @staticmethod
    def public_candidate(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row[key]
            for key in ("candidate_id", "requester_account", "target_account", "status")
        }

    async def resolve(
        self, candidate_id: str, actor: str, *, confirm: bool
    ) -> dict[str, Any]:
        async with self.transaction():
            raw = await self.db.fetch_one(
                "SELECT * FROM identity_link_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            if raw is None or actor not in {
                raw["requester_account"],
                raw["target_account"],
            }:
                raise ValueError("candidate is unavailable for this account")
            row = dict(raw)
            if row["status"] != "pending":
                raise ValueError("candidate is already resolved")
            person_id = ""
            if confirm:
                if actor != row["target_account"]:
                    raise ValueError(
                        "confirmation must come from the other channel's account"
                    )
                person_id = await self._confirm(row, actor)
            state = "confirmed" if confirm else "rejected"
            await self.db.execute(
                "UPDATE identity_link_candidates SET status=?,person_id=?,resolved_at=? WHERE candidate_id=?",
                (state, person_id, datetime.now(UTC).isoformat(), candidate_id),
            )
            await self.audit(
                "identity." + state,
                actor,
                candidate_id,
                {"status": "pending"},
                {"status": state, "person_id": person_id},
            )
            return {
                **self.public_candidate(row),
                "status": state,
                "person_id": person_id,
            }

    async def _confirm(self, row: dict[str, Any], actor: str) -> str:
        accounts = (row["requester_account"], row["target_account"])
        snapshot = json.loads(row["identity_snapshot_json"])
        current = {a: await self.person_for(a) for a in accounts}
        if current != snapshot:
            raise ValueError("identity changed since proposal; create a new candidate")
        people = {p for p in current.values() if p}
        if len(people) > 1:
            raise ValueError(
                "both accounts already have different Persons; administrator resolution is required"
            )
        person_id = next(iter(people), "person_" + uuid4().hex[:16])
        if not people:
            now = datetime.now(UTC).isoformat()
            await self.db.execute(
                "INSERT INTO persons(person_id,display_name,status,metadata_json,created_at,updated_at) VALUES (?,'','active','{}',?,?)",
                (person_id, now, now),
            )
            await self.audit(
                "person.upsert", actor, person_id, {}, {"person_id": person_id}
            )
        for account in accounts:
            if not current[account]:
                await self._link(
                    account, person_id, actor, verification="self_verified"
                )
        return person_id
