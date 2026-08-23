"""Probe KB retrieval against a real database using stored embeddings.

Reproduces the two issue #49 probe queries against the production-shaped
data without calling any embedding API: the query embedding is taken from a
chosen "ideal answer" chunk's stored vector (a pseudo-query), so the vector
leg behaves exactly as it would with a perfect semantic query embedding.

The probe always works on a throwaway copy of the database: DatabaseEngine
opens read-write and runs schema migrations, which must never touch an
archive snapshot directly.

Usage:
    uv run python scripts/probe_kb_retrieval.py --db data/nahida-server-20260822.db
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

COLLECTION = "Teyvat"

# (label, raw query, ideal-answer chunk selector SQL)
PROBES = [
    (
        "探针1 纳西妲对旅行者的态度",
        "纳西妲 对 旅行者 的态度",
        (
            "SELECT doc_id FROM Teyvat_docs WHERE source_id LIKE '%纳西妲%' "
            "AND title LIKE '%配音%' AND content LIKE '%旅行者%' "
            "AND status='active' LIMIT 1"
        ),
    ),
    (
        "探针2 世界树 净善宫 五百年",
        "世界树 净善宫 五百年",
        (
            "SELECT doc_id FROM Teyvat_docs WHERE source_id LIKE '%纳西妲%' "
            "AND title='角色关联语音 (part 4)' AND status='active'"
        ),
    ),
]


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


def _load_pseudo_embedding(db_path: Path, doc_id: str) -> list[float]:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT embedding_json FROM Teyvat_doc_embeddings WHERE doc_id = ?",
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
    store = SQLiteDocumentStore(engine, collection=COLLECTION)
    vector_index = SQLiteVecIndex(
        engine,
        dimensions=4096,
        table_name=f"kb_{COLLECTION}_embedding_vec",
        map_table=f"kb_{COLLECTION}_vec_map",
    )
    await vector_index.setup()

    try:
        probe_con = sqlite3.connect(str(work))
        probe_con.row_factory = sqlite3.Row

        def info(doc_id: str) -> str:
            row = probe_con.execute(
                "SELECT source_id, title, node_type FROM Teyvat_docs WHERE doc_id=?",
                (doc_id,),
            ).fetchone()
            if row is None:
                return str(doc_id)
            return f"{row['source_id']} > {row['title']} [{row['node_type']}]"

        for label, query, selector in PROBES:
            row = probe_con.execute(selector).fetchone()
            if row is None:
                print(f"===== {label} =====\n  (ideal-answer chunk not found, skip)\n")
                continue
            provider = PseudoQueryProvider(_load_pseudo_embedding(work, row["doc_id"]))

            print(f"===== {label} =====")
            print(f"query: {query}")
            print(f"ideal answer (pseudo-query source): {info(row['doc_id'])}")

            fts = await store.search(query, limit=args.limit)
            print("FTS leg (top-5):")
            for i, r in enumerate(fts, 1):
                print(f"  {i}. {info(r.doc_id)}  (bm25={r.score:.3f})")

            hybrid = await store.search_hybrid(
                query, provider, limit=args.limit, vector_index=vector_index
            )
            print("Hybrid final (top-5):")
            for i, r in enumerate(hybrid, 1):
                marker = " <== ideal" if r.doc_id == row["doc_id"] else ""
                print(f"  {i}. {info(r.doc_id)}  (fused={r.score:.5f}){marker}")
            print()
        probe_con.close()
    finally:
        await engine.close()
        work.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
