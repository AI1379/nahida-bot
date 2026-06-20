"""One-shot migration: populate sqlite-vec index from existing KB embeddings.

Use this AFTER switching ``vector_backend`` from ``"json"`` to ``"sqlite-vec"``
in the KB plugin config.  The ``"json"`` backend stores embeddings in
``{collection}_doc_embeddings`` and searches via in-Python cosine similarity.
sqlite-vec needs those same embeddings in ``kb_{collection}_embedding_vec`` /
``kb_{collection}_vec_map`` tables.

Workflow
--------

1. Update ``config.yaml``: ``knowledge_base.retrieval.vector_backend: sqlite-vec``
2. Run this script (it does NOT re-embed — just copies existing vectors)::

    uv run python scripts/migrate_kb_vector_backend.py --db data/nahida.db

3. Restart the bot.

   Dry-run first to see what will happen::

    uv run python scripts/migrate_kb_vector_backend.py --db data/nahida.db --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys

from nahida_bot.agent.storage.vector import SQLiteVecIndex, VectorRecord
from nahida_bot.db.engine import DatabaseEngine


async def migrate_collection(
    engine: DatabaseEngine,
    collection: str,
    *,
    dry_run: bool,
) -> tuple[int, int]:
    """Populate the sqlite-vec index for one KB collection. Returns (found, indexed)."""
    emb_table = f"{collection}_doc_embeddings"
    docs_table = f"{collection}_docs"
    vec_table = f"kb_{collection}_embedding_vec"
    map_table = f"kb_{collection}_vec_map"

    rows = await engine.fetch_all(
        f"SELECT e.embedding_id, e.doc_id, e.embedding_json, e.dimensions "
        f"FROM [{emb_table}] e "
        f"JOIN [{docs_table}] d ON d.doc_id = e.doc_id "
        "WHERE d.status = 'active'"
    )
    if not rows:
        return 0, 0

    dimensions = int(rows[0]["dimensions"])
    found = len(rows)

    if dry_run:
        return found, 0

    index = SQLiteVecIndex(
        engine,
        dimensions=dimensions,
        table_name=vec_table,
        map_table=map_table,
    )
    await index.setup()

    batch: list[VectorRecord] = []
    for row in rows:
        embedding = json.loads(str(row["embedding_json"]))
        batch.append(
            VectorRecord(
                embedding_id=str(row["embedding_id"]),
                item_id=str(row["doc_id"]),
                embedding=embedding,
            )
        )
    await index.upsert(batch)
    return found, len(batch)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate KB embeddings to sqlite-vec")
    parser.add_argument("--db", required=True, help="Path to nahida.db")
    parser.add_argument(
        "--collection",
        help="Migrate a single collection (default: all KB collections)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without doing it",
    )
    args = parser.parse_args()

    engine = DatabaseEngine(args.db)
    await engine.initialize()

    try:
        tables = await engine.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE '%_doc_embeddings'"
        )
        all_collections = sorted(
            row["name"][: -len("_doc_embeddings")] for row in tables
        )

        if args.collection:
            if args.collection not in all_collections:
                print(
                    f"Collection '{args.collection}' not found. "
                    f"Available: {all_collections or '(none)'}"
                )
                sys.exit(1)
            targets = [args.collection]
        else:
            targets = all_collections

        if not targets:
            print("No KB collections found. Nothing to migrate.")
            return

        total_found = 0
        total_indexed = 0
        label = "[DRY RUN] " if args.dry_run else ""

        for name in targets:
            found, indexed = await migrate_collection(
                engine, name, dry_run=args.dry_run
            )
            total_found += found
            total_indexed += indexed
            print(f"{label}[{name}] {found} embeddings found, {indexed} indexed")

        print(
            f"\n{label}Total: {total_found} embeddings found, "
            f"{total_indexed} indexed across {len(targets)} collection(s)"
        )
    finally:
        await engine.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
