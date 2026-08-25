"""Roll back dream-promoted KB nodes (A3, dreaming-to-kb.md §4.2).

Deletes every document whose metadata carries ``dream_promotion`` from the
dreams collection database (split layout: ``{kb_dir}/{collection}.db``; legacy
layout: the main db), and clears the promotion ledger from the main db's
plugin_data. Pure sqlite3 — no bot runtime needed. Usage:

    uv run python scripts/rollback_dream_promotions.py --db data/nahida.db \
        [--kb-dir data/kb] [--collection dreams] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_LEDGER_KEY = "dream_promotions"


def _find_docs(con: sqlite3.Connection, collection: str) -> list[tuple[str, str]]:
    """Return (doc_id, source_id) of dream-promoted docs in one collection."""
    try:
        rows = con.execute(
            f"SELECT doc_id, source_id, metadata_json FROM {collection}_docs "
            "WHERE metadata_json LIKE '%dream_promotion%'"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    result = []
    for doc_id, source_id, metadata_json in rows:
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(metadata, dict) and metadata.get("dream_promotion"):
            result.append((str(doc_id), str(source_id)))
    return result


def _clear_ledger(main_con: sqlite3.Connection) -> int:
    """Drop the promotion ledger rows from plugin_data; return removed count."""
    tables = {
        row[0]
        for row in main_con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "plugin_data" not in tables:
        return 0
    row = main_con.execute(
        "SELECT rowid, value_json FROM plugin_data "
        "WHERE plugin_id = 'knowledge_base' AND key = ?",
        (_LEDGER_KEY,),
    ).fetchone()
    if row is None:
        return 0
    rowid, value_json = row
    try:
        removed = len(json.loads(value_json or "{}"))
    except json.JSONDecodeError:
        removed = 0
    main_con.execute("DELETE FROM plugin_data WHERE rowid = ?", (rowid,))
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the main database")
    parser.add_argument(
        "--kb-dir", default="", help="Split-layout kb directory (optional)"
    )
    parser.add_argument("--collection", default="dreams")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    main_db = Path(args.db).resolve()
    if not main_db.is_file():
        sys.exit(f"database not found: {main_db}")

    kb_path = (
        Path(args.kb_dir).resolve() / f"{args.collection}.db"
        if args.kb_dir
        else main_db
    )
    if not kb_path.is_file():
        sys.exit(f"collection database not found: {kb_path}")

    kb_con = sqlite3.connect(str(kb_path))
    docs = _find_docs(kb_con, args.collection)
    kb_con.close()
    print(f"[rollback] dream-promoted docs in {kb_path}: {len(docs)}")
    for doc_id, source_id in docs[:10]:
        print(f"  {doc_id} (from memory item {source_id})")
    if len(docs) > 10:
        print(f"  ... and {len(docs) - 10} more")

    if args.dry_run:
        print("[rollback] dry run: nothing deleted")
        return

    kb_con = sqlite3.connect(str(kb_path))
    doc_ids = [doc_id for doc_id, _source_id in docs]
    kb_con.execute("BEGIN")
    if doc_ids:
        kb_con.executemany(
            f"DELETE FROM {args.collection}_doc_embeddings WHERE doc_id = ?",
            [(doc_id,) for doc_id in doc_ids],
        )
        kb_con.executemany(
            f"DELETE FROM {args.collection}_doc_fts WHERE doc_id = ?",
            [(doc_id,) for doc_id in doc_ids],
        )
        kb_con.executemany(
            f"DELETE FROM {args.collection}_docs WHERE doc_id = ?",
            [(doc_id,) for doc_id in doc_ids],
        )
    kb_con.commit()
    kb_con.close()

    main_con = sqlite3.connect(str(main_db))
    main_con.execute("BEGIN")
    removed = _clear_ledger(main_con)
    main_con.commit()
    main_con.close()

    print(f"[rollback] deleted {len(docs)} dream-promoted docs")
    print(f"[rollback] cleared {removed} promotion ledger entries")
    print("[rollback] note: vec index entries are reconciled on next embed pass")


if __name__ == "__main__":
    main()
