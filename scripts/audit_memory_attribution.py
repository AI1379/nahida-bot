"""Report unattributed active memories from an explicit read-only DB snapshot.

Usage: uv run python scripts/audit_memory_attribution.py --database snapshot.db
       --output attribution-audit.json
This tool never guesses a subject, updates items, or invokes a model.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nahida_bot.agent.memory.attribution import MemoryAttribution
from nahida_bot.agent.memory.portability import metadata_is_portable


def audit_items(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    findings = []
    scanned = 0
    for item in items:
        if item.get("status") != "active":
            continue
        scanned += 1
        try:
            metadata = json.loads(item.get("metadata_json") or "{}")
        except (TypeError, ValueError):
            metadata = {}
        attribution = MemoryAttribution.parse(metadata)
        problems = []
        if attribution.subject.kind == "unknown":
            problems.append("unknown_subject")
        if not attribution.evidence:
            problems.append("missing_evidence")
        if (
            attribution.subject.kind == "unknown"
            and item.get("sensitivity") == "public"
            and metadata_is_portable(metadata)
        ):
            problems.append("unknown_subject_portable")
        if not problems:
            continue
        counts.update(problems)
        findings.append(
            {
                "item_id": item["item_id"],
                "kind": item["kind"],
                "title": item.get("title", ""),
                "scope_type": item["scope_type"],
                "scope_id": item["scope_id"],
                "created_at": item.get("created_at", ""),
                "source": item.get("source", ""),
                "problems": problems,
                "action": "review_against_original_turns",
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "scanned_active_items": scanned,
        "flagged_items": len(findings),
        "problem_counts": dict(counts),
        "findings": findings,
        "note": "Flags indicate missing attribution, not proven false content. No automatic reassignment or deletion is proposed.",
    }


def audit_database(database: Path) -> dict[str, Any]:
    if not database.is_file():
        raise FileNotFoundError(database)
    with sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT item_id, kind, title, scope_type, scope_id, source, status, sensitivity, metadata_json, created_at "
            "FROM memory_items WHERE status='active' ORDER BY created_at, item_id"
        )
        report = audit_items(dict(row) for row in rows)
    report["database"] = str(database.resolve())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="An explicit archived SQLite snapshot",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON review report; never the database path",
    )
    args = parser.parse_args()
    database, output = args.database.resolve(), args.output.resolve()
    if output in {database, Path(str(database) + "-wal"), Path(str(database) + "-shm")}:
        parser.error("output must not overwrite the database or its journal")
    report = audit_database(database)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("scanned_active_items", "flagged_items", "problem_counts")
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
