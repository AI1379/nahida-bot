"""One-shot migration: populate sqlite-vec index from existing KB embeddings.

Use this AFTER switching ``vector_backend`` from ``"json"`` to ``"sqlite-vec"``
in the KB plugin config.  The ``"json"`` backend stores embeddings in
``{collection}_doc_embeddings`` and searches via in-Python cosine similarity.
sqlite-vec needs those same embeddings in ``kb_{collection}_embedding_vec`` /
``kb_{collection}_vec_map`` tables.

Workflow
--------

1. **Back up the database first** — this script writes to ``data/nahida.db`` and
   has no undo::

    cp data/nahida.db data/nahida.db.bak

2. Update ``config.yaml``: ``knowledge_base.retrieval.vector_backend: sqlite-vec``
3. Run this script (it does NOT re-embed — just copies existing vectors)::

    uv run python scripts/migrate_kb_vector_backend.py --db data/nahida.db

4. Restart the bot.

   Dry-run first to see counts without indexing. Note: a dry run does **not**
   validate that the sqlite-vec extension loads (it never creates the vec
   table) — the real run validates that, failing fast on the first collection
   if the extension is missing::

    uv run python scripts/migrate_kb_vector_backend.py --db data/nahida.db --dry-run

A single bad collection (corrupt row, dimension mismatch) is reported and
skipped; the remaining collections still migrate. Rows whose embedding length
doesn't match the collection's declared dimension are skipped (and counted) so a
mixed-model collection doesn't abort the whole run.
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
    batch_size: int,
) -> tuple[int, int, int]:
    """Populate the sqlite-vec index for one KB collection.

    Returns ``(found, indexed, skipped)`` where ``skipped`` counts rows whose
    embedding length didn't match the collection's declared dimension.
    """
    emb_table = f"{collection}_doc_embeddings"
    docs_table = f"{collection}_docs"
    vec_table = f"kb_{collection}_embedding_vec"
    map_table = f"kb_{collection}_vec_map"

    if dry_run:
        # Pure count: do NOT instantiate SQLiteVecIndex or create the vec
        # table. Note this means a dry run does not validate that the sqlite-vec
        # extension loads — the real run does, failing fast on the first
        # collection's setup() if it's missing.
        count_row = await engine.fetch_one(
            f"SELECT COUNT(*) AS embedding_count "
            f"FROM [{emb_table}] e "
            f"JOIN [{docs_table}] d ON d.doc_id = e.doc_id "
            "WHERE d.status = 'active'"
        )
        return int(count_row["embedding_count"]) if count_row else 0, 0, 0

    dimension_row = await engine.fetch_one(
        f"SELECT e.dimensions "
        f"FROM [{emb_table}] e "
        f"JOIN [{docs_table}] d ON d.doc_id = e.doc_id "
        "WHERE d.status = 'active' "
        "LIMIT 1"
    )
    if dimension_row is None:
        return 0, 0, 0
    dimensions = int(dimension_row["dimensions"])

    # Validate sqlite-vec loads + the vec0 table can be created BEFORE touching
    # data. setup() uses CREATE ... IF NOT EXISTS; if the extension is missing
    # or vec0 is unavailable, it raises here (fast, on the first collection).
    index = SQLiteVecIndex(
        engine,
        dimensions=dimensions,
        table_name=vec_table,
        map_table=map_table,
    )
    await index.setup()

    cursor = await engine.db.execute(
        f"SELECT e.embedding_id, e.doc_id, e.embedding_json, e.dimensions "
        f"FROM [{emb_table}] e "
        f"JOIN [{docs_table}] d ON d.doc_id = e.doc_id "
        "WHERE d.status = 'active'"
    )
    found = 0
    indexed = 0
    skipped = 0
    try:
        while rows := list(await cursor.fetchmany(batch_size)):
            batch: list[VectorRecord] = []
            for row in rows:
                vec = json.loads(str(row["embedding_json"]))
                # Guard against mixed-model collections: a row whose vector
                # length doesn't match the declared dimension would crash the
                # vec0 insert; skip it rather than aborting the batch.
                if len(vec) != dimensions:
                    skipped += 1
                    continue
                batch.append(
                    VectorRecord(
                        embedding_id=str(row["embedding_id"]),
                        item_id=str(row["doc_id"]),
                        embedding=vec,
                    )
                )
            if batch:
                await index.upsert(batch)
                indexed += len(batch)
            found += len(rows)
    finally:
        await cursor.close()

    return found, indexed, skipped


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
        help="Print counts without indexing (does NOT validate the sqlite-vec extension)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Embeddings to decode and index at a time (default: 500)",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be greater than zero")

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
        total_skipped = 0
        failures: list[str] = []
        label = "[DRY RUN] " if args.dry_run else ""

        # Each collection is isolated: a single bad collection is reported and
        # skipped so independent collections still migrate.
        for name in targets:
            try:
                found, indexed, skipped = await migrate_collection(
                    engine,
                    name,
                    dry_run=args.dry_run,
                    batch_size=args.batch_size,
                )
            except Exception as exc:  # noqa: BLE001 — report and continue
                failures.append(name)
                print(f"{label}[{name}] FAILED: {exc}")
                continue
            total_found += found
            total_indexed += indexed
            total_skipped += skipped
            extra = f", {skipped} skipped (dimension mismatch)" if skipped else ""
            print(f"{label}[{name}] {found} found, {indexed} indexed{extra}")

        print(
            f"\n{label}Total: {total_found} found, {total_indexed} indexed, "
            f"{total_skipped} skipped across {len(targets)} collection(s)"
        )
        if failures:
            print(
                f"{label}{len(failures)} collection(s) FAILED: "
                f"{', '.join(failures)} — others were still processed."
            )
            if not args.dry_run:
                sys.exit(1)
    finally:
        await engine.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
