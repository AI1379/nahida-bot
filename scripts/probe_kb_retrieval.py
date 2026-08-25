"""Probe KB retrieval against a real database using stored embeddings.

Runs the gold eval set (``scripts/kb_eval_queries.json``, kb-direction.md §4)
against production-shaped data without calling any embedding API: the query
embedding is taken from a chosen "ideal answer" chunk's stored vector (a
pseudo-query), so the vector leg behaves exactly as it would with a perfect
semantic query embedding. A probe's selector may match several ideal chunks —
any of them counts as a hit.

The probe always works on a throwaway copy of the database: DatabaseEngine
opens read-write and runs schema migrations, which must never touch an
archive snapshot directly.

Usage:
    uv run python scripts/probe_kb_retrieval.py --db data/nahida-server-20260822.db

Output ends with a summary (top1/top3/top5 hit counts over the eval set) —
the baseline/regression report for #26 migrations and retrieval changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nahida_bot.agent.storage.embedding import EmbeddingResult  # noqa: E402
from nahida_bot.agent.storage.sqlite_document_store import (  # noqa: E402
    SQLiteDocumentStore,
)
from nahida_bot.agent.storage.vector import SQLiteVecIndex  # noqa: E402
from nahida_bot.db.engine import DatabaseEngine  # noqa: E402

EVAL_FILE = Path(__file__).resolve().parent / "kb_eval_queries.json"


def load_probes() -> tuple[str, list[tuple[str, str, str]]]:
    """Return (collection, [(label, query, selector_sql), ...])."""
    data = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    probes = [
        (str(p["label"]), str(p["query"]), str(p["selector"]))
        for p in data["probes"]
    ]
    return str(data["collection"]), probes


class PseudoQueryProvider:
    """Returns a preselected chunk embedding as the query vector."""

    def __init__(self, embedding: list[float]) -> None:
        self.provider_id = "siliconflow"
        self.model = "Qwen/Qwen3-Embedding-8B"
        self.dimensions = len(embedding)
        self._embedding = embedding

    async def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        return [
            EmbeddingResult(
                embedding=list(self._embedding),
                provider_id=self.provider_id,
                model=self.model,
            )
            for _ in texts
        ]


def _load_pseudo_embedding(db_path: Path, collection: str, doc_id: str) -> list[float]:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            f"SELECT embedding_json FROM {collection}_doc_embeddings WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        return json.loads(row[0])
    finally:
        con.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the nahida database")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    collection, probes = load_probes()

    archive = Path(args.db).resolve()
    if not archive.is_file():
        sys.exit(f"database not found: {archive}")

    # Always probe a throwaway copy — DatabaseEngine opens read-write and
    # runs migrations; an archive snapshot must never be touched directly.
    work = Path(tempfile.gettempdir()) / "kb-probe-work.db"
    print(f"[probe] copying {archive} -> {work} (engine runs on the copy only)")
    shutil.copyfile(archive, work)

    engine = DatabaseEngine(work)
    await engine.initialize()
    store = SQLiteDocumentStore(engine, collection=collection)
    vector_index = SQLiteVecIndex(
        engine,
        dimensions=4096,
        table_name=f"kb_{collection}_embedding_vec",
        map_table=f"kb_{collection}_vec_map",
    )
    await vector_index.setup()

    ranks: list[tuple[str, int | None]] = []

    try:
        probe_con = sqlite3.connect(str(work))
        probe_con.row_factory = sqlite3.Row

        def info(doc_id: str) -> str:
            row = probe_con.execute(
                f"SELECT source_id, title, node_type FROM {collection}_docs WHERE doc_id=?",
                (doc_id,),
            ).fetchone()
            if row is None:
                return str(doc_id)
            return f"{row['source_id']} > {row['title']} [{row['node_type']}]"

        for label, query, selector in probes:
            ideal_rows = probe_con.execute(selector).fetchall()
            if not ideal_rows:
                print(f"===== {label} =====\n  (ideal-answer chunk not found, skip)\n")
                continue
            ideal_ids = {row["doc_id"] for row in ideal_rows}
            provider = PseudoQueryProvider(
                _load_pseudo_embedding(work, collection, ideal_rows[0]["doc_id"])
            )

            print(f"===== {label} =====")
            print(f"query: {query}")
            print(
                f"ideal answers: {len(ideal_ids)} chunks, "
                f"pseudo-query source: {info(ideal_rows[0]['doc_id'])}"
            )

            hybrid = await store.search_hybrid(
                query, provider, limit=args.limit, vector_index=vector_index
            )
            hit_rank: int | None = None
            for i, r in enumerate(hybrid, 1):
                if hit_rank is None and r.doc_id in ideal_ids:
                    hit_rank = i
                marker = " <== ideal" if r.doc_id in ideal_ids else ""
                print(f"  {i}. {info(r.doc_id)}  (fused={r.score:.5f}){marker}")
            ranks.append((label, hit_rank))
            print()

        probe_con.close()
    finally:
        await engine.close()
        work.unlink(missing_ok=True)

    total = len(ranks)
    top1 = sum(1 for _, r in ranks if r == 1)
    top3 = sum(1 for _, r in ranks if r is not None and r <= 3)
    top5 = sum(1 for _, r in ranks if r is not None and r <= 5)
    print("===== summary =====")
    for label, rank in ranks:
        where = f"rank {rank}" if rank is not None else "MISS"
        print(f"  {label}: {where}")
    print(
        f"\neval set: {total} probes | top1 {top1}/{total} | "
        f"top3 {top3}/{total} | top5 {top5}/{total}"
    )


if __name__ == "__main__":
    asyncio.run(main())
