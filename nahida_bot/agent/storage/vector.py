"""Vector index helpers for document and memory retrieval."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from nahida_bot.db.engine import DatabaseEngine


@dataclass(slots=True, frozen=True)
class VectorRecord:
    """One vector index record."""

    embedding_id: str
    item_id: str
    embedding: list[float]


@dataclass(slots=True, frozen=True)
class VectorHit:
    """Vector search result."""

    item_id: str
    score: float


class VectorIndex(Protocol):
    """Protocol for pluggable vector indexes.

    Keep this structural so plugins and optional backends can provide a
    compatible object without importing or subclassing a nahida-bot base class.
    """

    async def upsert(self, records: list[VectorRecord]) -> None:
        """Insert or replace vector records."""
        ...

    async def delete(self, ids: list[str]) -> None:
        """Delete vector records by embedding id."""
        ...

    async def search(
        self, query_embedding: list[float], *, limit: int
    ) -> list[VectorHit]:
        """Search vectors by similarity."""
        ...


class NoopVectorIndex:
    """Vector index that intentionally stores nothing."""

    async def upsert(self, records: list[VectorRecord]) -> None:
        return None

    async def delete(self, ids: list[str]) -> None:
        return None

    async def search(
        self, query_embedding: list[float], *, limit: int
    ) -> list[VectorHit]:
        return []


class SQLiteVecIndex:
    """Optional sqlite-vec backed vector index.

    This class only activates when the ``sqlite_vec`` package is installed and
    loadable. Call ``setup()`` before use; otherwise methods raise RuntimeError.
    """

    _UPSERT_LOOKUP_BATCH_SIZE = 500

    def __init__(
        self,
        engine: "DatabaseEngine",
        *,
        dimensions: int,
        table_name: str = "memory_embedding_vec",
        map_table: str = "memory_vec_map",
    ) -> None:
        self._engine = engine
        self._dimensions = dimensions
        self._table_name = table_name
        self._map_table = map_table
        self._ready = False

    async def setup(self) -> None:
        await self._load_extension()

        async with self._engine.write_lock:
            await self._engine.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._table_name} "
                f"USING vec0(embedding float[{self._dimensions}])"
            )
            await self._engine.execute(
                f"CREATE TABLE IF NOT EXISTS {self._map_table} ("
                "embedding_id TEXT PRIMARY KEY, "
                "item_id TEXT NOT NULL, "
                "vec_rowid INTEGER NOT NULL UNIQUE)"
            )
            await self._engine.db.commit()
        self._ready = True

    async def _load_extension(self) -> None:
        """Load sqlite-vec into the current database connection."""
        try:
            import sqlite_vec  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("sqlite-vec is not installed") from exc

        await self._engine.db.enable_load_extension(True)
        try:
            raw_conn = cast(Any, self._engine.db)._conn
            await cast(Any, self._engine.db)._execute(sqlite_vec.load, raw_conn)
        finally:
            await self._engine.db.enable_load_extension(False)

    async def upsert(self, records: list[VectorRecord]) -> None:
        self._require_ready()
        if not records:
            return
        async with self._engine.write_lock:
            for start in range(0, len(records), self._UPSERT_LOOKUP_BATCH_SIZE):
                record_batch = records[start : start + self._UPSERT_LOOKUP_BATCH_SIZE]
                placeholders = ",".join("?" for _ in record_batch)
                existing_rows = await self._engine.fetch_all(
                    f"SELECT embedding_id, vec_rowid FROM {self._map_table} "
                    f"WHERE embedding_id IN ({placeholders})",
                    tuple(record.embedding_id for record in record_batch),
                )
                existing_by_id = {
                    str(row["embedding_id"]): int(row["vec_rowid"])
                    for row in existing_rows
                }
                persisted_ids = set(existing_by_id)

                updates = [
                    (
                        self._serialize(record.embedding),
                        existing_by_id[record.embedding_id],
                    )
                    for record in record_batch
                    if record.embedding_id in persisted_ids
                ]
                if updates:
                    await self._engine.db.executemany(
                        f"UPDATE {self._table_name} SET embedding = ? WHERE rowid = ?",
                        updates,
                    )

                for record in record_batch:
                    if record.embedding_id in persisted_ids:
                        continue
                    if record.embedding_id in existing_by_id:
                        await self._engine.execute(
                            f"UPDATE {self._table_name} SET embedding = ? WHERE rowid = ?",
                            (
                                self._serialize(record.embedding),
                                existing_by_id[record.embedding_id],
                            ),
                        )
                        continue
                    cursor = await self._engine.execute(
                        f"INSERT INTO {self._table_name} (embedding) VALUES (?)",
                        (self._serialize(record.embedding),),
                    )
                    if cursor.lastrowid is None:
                        raise RuntimeError("sqlite-vec insert did not return a rowid")
                    vec_rowid = int(cursor.lastrowid)
                    await self._engine.execute(
                        f"INSERT INTO {self._map_table} (embedding_id, item_id, vec_rowid) "
                        "VALUES (?, ?, ?)",
                        (record.embedding_id, record.item_id, vec_rowid),
                    )
                    existing_by_id[record.embedding_id] = vec_rowid
            await self._engine.db.commit()

    async def delete(self, ids: list[str]) -> None:
        self._require_ready()
        if not ids:
            return
        async with self._engine.write_lock:
            for embedding_id in ids:
                row = await self._engine.fetch_one(
                    f"SELECT vec_rowid FROM {self._map_table} WHERE embedding_id = ?",
                    (embedding_id,),
                )
                if row is None:
                    continue
                vec_rowid = int(row["vec_rowid"])
                await self._engine.execute(
                    f"DELETE FROM {self._table_name} WHERE rowid = ?",
                    (vec_rowid,),
                )
                await self._engine.execute(
                    f"DELETE FROM {self._map_table} WHERE embedding_id = ?",
                    (embedding_id,),
                )
            await self._engine.db.commit()

    async def search(
        self, query_embedding: list[float], *, limit: int
    ) -> list[VectorHit]:
        self._require_ready()
        rows = await self._engine.fetch_all(
            f"SELECT m.item_id, v.distance FROM {self._table_name} v "
            f"JOIN {self._map_table} m ON m.vec_rowid = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (self._serialize(query_embedding), limit),
        )
        return [
            VectorHit(
                item_id=str(row["item_id"]),
                score=1.0 / (1.0 + float(row["distance"])),
            )
            for row in rows
        ]

    async def drop(self) -> None:
        """Drop the sqlite-vec tables owned by this index."""
        if not self._ready:
            await self._load_extension()
        async with self._engine.write_lock:
            await self._engine.execute(f"DROP TABLE IF EXISTS {self._map_table}")
            await self._engine.execute(f"DROP TABLE IF EXISTS {self._table_name}")
            await self._engine.db.commit()
        self._ready = False

    def _require_ready(self) -> None:
        if not self._ready:
            raise RuntimeError("SQLiteVecIndex.setup() must be called before use")

    @staticmethod
    def _serialize(embedding: list[float]) -> object:
        try:
            from sqlite_vec import serialize_float32  # type: ignore[import-not-found]
        except ImportError:
            return json.dumps(embedding)
        return serialize_float32(embedding)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity for two vectors."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    *,
    k: int = 60,
    limit: int = 10,
) -> list[tuple[str, float]]:
    """Fuse ranked item id lists with reciprocal rank fusion."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
