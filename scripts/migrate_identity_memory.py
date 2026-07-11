"""Plan/apply account or chat memory migration into person scopes.

Only deterministic evidence is accepted:

- an existing ``account`` scope whose account has an active person link; or
- a structured metadata/evidence account key with an active person link.

Content text and display names are never inspected. Generated entries remain
``pending`` and require review, matching ``migrate_memory_scope.py`` safety.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

try:
    from migrate_memory_scope import (
        MigrationPlan,
        PlanEntry,
        ScopeEvidence,
        apply_plan,
        write_plan,
    )
except ModuleNotFoundError:  # imported as scripts.migrate_identity_memory in tests
    from scripts.migrate_memory_scope import (
        MigrationPlan,
        PlanEntry,
        ScopeEvidence,
        apply_plan,
        write_plan,
    )

PERSONAL_KINDS = frozenset({"preference", "fact", "task"})
ACCOUNT_KEYS = frozenset({"account_key", "sender_account_key", "origin_account_key"})


def inspect_identity_memory(db_path: Path) -> MigrationPlan:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        links = {
            str(row["account_key"]): str(row["person_id"])
            for row in conn.execute(
                "SELECT account_key, person_id FROM person_accounts "
                "WHERE status = 'active'"
            )
        }
        rows = conn.execute(
            "SELECT item_id, kind, status, scope_type, scope_id, metadata_json, "
            "evidence_json FROM memory_items ORDER BY updated_at DESC, item_id"
        ).fetchall()
        entries = [_inspect_row(row, links) for row in rows]
        return MigrationPlan(
            version=1,
            generated_at=datetime.now(UTC).isoformat(),
            db_path=str(db_path),
            include_archived=True,
            summary=dict(Counter(entry.action for entry in entries)),
            entries=entries,
        )
    finally:
        conn.close()


def _inspect_row(row: sqlite3.Row, links: dict[str, str]) -> PlanEntry:
    item_id = str(row["item_id"])
    kind = str(row["kind"])
    scope_type = str(row["scope_type"])
    scope_id = str(row["scope_id"])
    status = str(row["status"])
    if kind not in PERSONAL_KINDS or scope_type in {"person", "global"}:
        return PlanEntry(
            item_id=item_id,
            kind=kind,
            status=status,
            current_scope_type=scope_type,
            current_scope_id=scope_id,
            action="skip_non_global",
            reason="not an eligible account/chat-scoped personal memory",
        )

    accounts: set[str] = set()
    if scope_type == "account":
        accounts.add(scope_id)
    accounts.update(_extract_accounts(_load_json(row["metadata_json"])))
    accounts.update(_extract_accounts(_load_json(row["evidence_json"])))
    resolved = {(account, links[account]) for account in accounts if account in links}
    people = {person_id for _, person_id in resolved}
    evidence = [
        ScopeEvidence(
            source="identity_link",
            value=account,
            scope_type="person",
            scope_id=person_id,
        )
        for account, person_id in sorted(resolved)
    ]
    if len(people) == 1:
        person_id = next(iter(people))
        return PlanEntry(
            item_id=item_id,
            kind=kind,
            status=status,
            current_scope_type=scope_type,
            current_scope_id=scope_id,
            action="migrate",
            reason="single person resolved from active account links",
            target_scope_type="person",
            target_scope_id=person_id,
            evidence=evidence,
        )
    return PlanEntry(
        item_id=item_id,
        kind=kind,
        status=status,
        current_scope_type=scope_type,
        current_scope_id=scope_id,
        action="manual_review",
        reason=(
            "conflicting linked persons in structured evidence"
            if len(people) > 1
            else "no active account-to-person link in structured evidence"
        ),
        evidence=evidence,
    )


def _extract_accounts(value: object) -> set[str]:
    result: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key) in ACCOUNT_KEYS and isinstance(child, str) and child:
                    result.add(child)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return result


def _load_json(value: object) -> object:
    if not isinstance(value, str) or not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_cmd = sub.add_parser("inspect")
    inspect_cmd.add_argument("--db", type=Path, required=True)
    inspect_cmd.add_argument("--plan", type=Path, required=True)
    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--db", type=Path, required=True)
    apply_cmd.add_argument("--plan", type=Path, required=True)
    apply_cmd.add_argument("--confirm", action="store_true")
    apply_cmd.add_argument("--dry-run", action="store_true")
    apply_cmd.add_argument("--apply-all-safe", action="store_true")
    args = parser.parse_args()
    if args.command == "inspect":
        plan = inspect_identity_memory(args.db)
        write_plan(plan, args.plan)
        print(json.dumps(plan.summary, ensure_ascii=False, sort_keys=True))
        return
    result = apply_plan(
        db_path=args.db,
        plan_path=args.plan,
        confirm=args.confirm,
        dry_run=args.dry_run,
        apply_all_safe=args.apply_all_safe,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
