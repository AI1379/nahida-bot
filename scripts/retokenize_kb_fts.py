"""Re-tokenize the FTS index of KB collections in place.

Needed after changing tokenization (domain lexicon, digit-merge rules):
the ``{collection}_doc_fts`` rows were built with the old tokenizer, so
new whole-token terms (纳西妲, 世界树, 故事3) do not match until the index
is rebuilt. Embeddings are NOT touched — ``retrieval_text`` and its hash
are unchanged, so no re-embedding and no embedding API cost.

Run against a copy when verifying; the production database should be
updated during a maintenance window and backed up first.

Usage:
    uv run python scripts/retokenize_kb_fts.py --db data/nahida.db [--collections Teyvat] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nahida_bot.agent.storage.tokenization import tokenize_for_fts  # noqa: E402
from nahida_bot.db.engine import DatabaseEngine  # noqa: E402

BATCH_COMMIT = 200


async def discover_collections(engine: DatabaseEngine) -> list[str]:
    rows = await engine.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_docs'"
    )
    return sorted(str(row["name"])[: -len("_docs")] for row in rows)


async def retokenize_collection(
    engine: DatabaseEngine, collection: str, *, dry_run: bool
) -> tuple[int, int]:
    docs_table = f"{collection}_docs"
    fts_table = f"{collection}_doc_fts"
    rows = await engine.fetch_all(
        f"SELECT doc_id, title, content, retrieval_text FROM {docs_table}"
    )
    changed = 0
    pending: list[tuple[str, str, str]] = []
    for row in rows:
        doc_id = str(row["doc_id"])
        title = str(row["title"] or "")
        retrieval_text = str(row["retrieval_text"] or "")
        index_text = retrieval_text or (
            f"{title}\n{row['content']}" if title else str(row["content"])
        )
        new_title = tokenize_for_fts(title) if title else ""
        new_content = tokenize_for_fts(index_text)
        current = await engine.fetch_one(
            f"SELECT title_index, content_index FROM {fts_table} WHERE doc_id = ?",
            (doc_id,),
        )
        if (
            current is not None
            and str(current["title_index"]) == new_title
            and str(current["content_index"]) == new_content
        ):
            continue
        changed += 1
        pending.append((doc_id, new_title, new_content))
        if dry_run or len(pending) >= BATCH_COMMIT:
            if not dry_run:
                await _rewrite_rows(engine, fts_table, pending)
                pending.clear()
    if pending and not dry_run:
        await _rewrite_rows(engine, fts_table, pending)
    return changed, len(rows)


async def _rewrite_rows(
    engine: DatabaseEngine, fts_table: str, rows: list[tuple[str, str, str]]
) -> None:
    async with engine.write_lock:
        for doc_id, title_index, content_index in rows:
            await engine.execute(f"DELETE FROM {fts_table} WHERE doc_id = ?", (doc_id,))
            await engine.execute(
                f"INSERT INTO {fts_table} (doc_id, title_index, content_index) "
                "VALUES (?, ?, ?)",
                (doc_id, title_index, content_index),
            )
        await engine.db.commit()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the database")
    parser.add_argument(
        "--collections",
        nargs="*",
        default=None,
        help="Collection names (default: all collections with a _docs table)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = DatabaseEngine(args.db)
    await engine.initialize()
    try:
        collections = args.collections or await discover_collections(engine)
        for collection in collections:
            changed, total = await retokenize_collection(
                engine, collection, dry_run=args.dry_run
            )
            action = "would re-tokenize" if args.dry_run else "re-tokenized"
            print(f"[{collection}] {action} {changed}/{total} FTS rows")
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
