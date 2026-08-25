"""Tests for the dreaming→KB promotion gate, ledger, and rollback (A3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nahida_bot.plugins.knowledge_base.config import (
    KBDreamPromotionConfig,
    parse_kb_config,
)
from nahida_bot.plugins.knowledge_base.promoter import DreamPromoter
from scripts import rollback_dream_promotions as rollback


class _Harness:
    """In-memory ports capturing promoter side effects."""

    def __init__(self, *, config: KBDreamPromotionConfig | None = None) -> None:
        self.config = config or KBDreamPromotionConfig()
        self.imported: list[dict[str, Any]] = []
        self.kb_hits: list[Any] = []
        self.ledger: dict[str, Any] | None = None

    def promoter(self) -> DreamPromoter:
        return DreamPromoter(
            import_content=self._import_content,
            search_documents=self._search_documents,
            load_ledger=self._load_ledger,
            save_ledger=self._save_ledger,
            config=self.config,
        )

    async def _import_content(
        self,
        collection: str,
        *,
        source_id: str,
        content: str,
        content_type: str = "text",
        extra_metadata: dict[str, Any] | None = None,
    ) -> int:
        self.imported.append(
            {
                "collection": collection,
                "source_id": source_id,
                "content": content,
                "extra_metadata": extra_metadata or {},
            }
        )
        return 1

    async def _search_documents(
        self, collection: str, query: str, *, limit: int = 5
    ) -> list[Any]:
        return self.kb_hits

    async def _load_ledger(self) -> dict[str, Any] | None:
        return self.ledger

    async def _save_ledger(self, ledger: dict[str, Any]) -> None:
        self.ledger = ledger


def _item(
    item_id: str = "m1",
    *,
    kind: str = "fact",
    confidence: float = 0.95,
    sensitivity: str = "public",
    portable: bool | None = None,
    status: str = "active",
    content: str = "The shared mascot is a dragon",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if portable is not None:
        metadata["portable"] = portable
    return {
        "item_id": item_id,
        "scope_type": "global",
        "scope_id": "__global__",
        "kind": kind,
        "title": "mascot",
        "content": content,
        "status": status,
        "confidence": confidence,
        "sensitivity": sensitivity,
        "metadata": metadata,
    }


@pytest.mark.asyncio
async def test_gate_matrix() -> None:
    harness = _Harness()
    promoter = harness.promoter()
    stats = await promoter.promote_once(
        [
            _item("ok"),  # passes every gate
            _item("kind", kind="preference"),  # wrong kind
            _item("conf", confidence=0.7),  # below threshold
            _item("sens", sensitivity="private"),  # not public
            _item("port", portable=False),  # not portable
            _item("gone", status="archived"),  # inactive
        ]
    )
    assert [entry["source_id"] for entry in harness.imported] == ["ok"]
    assert stats["promoted"] == 1
    assert stats["skipped_kind"] == 1
    assert stats["skipped_confidence"] == 1
    assert stats["skipped_sensitivity"] == 1
    assert stats["skipped_portable"] == 1
    assert stats["skipped_status"] == 1


@pytest.mark.asyncio
async def test_promoted_metadata_markers() -> None:
    harness = _Harness()
    await harness.promoter().promote_once([_item("m9")])
    entry = harness.imported[0]
    assert entry["collection"] == "dreams"
    assert entry["extra_metadata"]["dream_promotion"] is True
    assert entry["extra_metadata"]["promoted_from_item_id"] == "m9"
    assert entry["extra_metadata"]["memory_sensitivity"] == "public"
    # ledger recorded as promoted with content hash
    assert harness.ledger is not None and harness.ledger["m9"]["status"] == "promoted"
    assert harness.ledger["m9"]["content_hash"]


@pytest.mark.asyncio
async def test_ledger_makes_second_pass_idempotent() -> None:
    harness = _Harness()
    await harness.promoter().promote_once([_item("m1")])
    stats = await harness.promoter().promote_once([_item("m1")])
    assert stats["promoted"] == 0
    assert stats["skipped_ledger"] == 1
    assert len(harness.imported) == 1


@pytest.mark.asyncio
async def test_daily_limit_blocks_after_quota() -> None:
    harness = _Harness(config=KBDreamPromotionConfig(daily_limit=1))
    stats = await harness.promoter().promote_once([_item("a"), _item("b")])
    assert stats["promoted"] == 1
    assert stats["skipped_daily_limit"] == 1
    # Next pass (same day) is still blocked by the ledger-backed quota.
    stats2 = await harness.promoter().promote_once([_item("c")])
    assert stats2["promoted"] == 0
    assert stats2["skipped_daily_limit"] == 1


@pytest.mark.asyncio
async def test_daily_limit_zero_disables_promotion() -> None:
    harness = _Harness(config=KBDreamPromotionConfig(daily_limit=0))
    stats = await harness.promoter().promote_once([_item("a")])
    assert stats["promoted"] == 0
    assert stats["skipped_daily_limit"] == 1


@pytest.mark.asyncio
async def test_existing_kb_node_deduplicates() -> None:
    harness = _Harness()

    class _Hit:
        source_id = "m1"
        content = "The shared mascot  is a dragon"  # whitespace-insensitive match

    harness.kb_hits = [_Hit()]
    stats = await harness.promoter().promote_once([_item("m1")])
    assert stats["promoted"] == 0
    assert stats["skipped_duplicate"] == 1
    assert harness.ledger is not None
    assert harness.ledger["m1"]["status"] == "duplicate"
    # A duplicate verdict is final for this content: no re-import attempts.
    stats2 = await harness.promoter().promote_once([_item("m1")])
    assert stats2["skipped_ledger"] == 1


@pytest.mark.asyncio
async def test_import_failure_is_counted_not_fatal() -> None:
    harness = _Harness()

    async def flaky(
        collection: str,
        *,
        source_id: str,
        content: str,
        content_type: str = "text",
        extra_metadata: dict[str, Any] | None = None,
    ) -> int:
        if source_id == "m1":
            raise RuntimeError("kb down")
        return await harness._import_content(
            collection,
            source_id=source_id,
            content=content,
            content_type=content_type,
            extra_metadata=extra_metadata,
        )

    promoter = DreamPromoter(
        import_content=flaky,
        search_documents=harness._search_documents,
        load_ledger=harness._load_ledger,
        save_ledger=harness._save_ledger,
        config=harness.config,
    )
    stats = await promoter.promote_once([_item("m1"), _item("m2")])
    assert stats["failed"] == 1
    assert stats["promoted"] == 1  # m2 still promoted after m1's failure
    assert harness.ledger is not None
    assert "m1" not in harness.ledger  # failed import leaves no ledger entry
    assert harness.ledger["m2"]["status"] == "promoted"


def test_config_defaults_and_parse() -> None:
    config = parse_kb_config({})
    assert config.dream_promotion.enabled is False
    assert config.dream_promotion.min_confidence == 0.9
    assert config.dream_promotion.daily_limit == 2
    assert config.dream_promotion.collection == "dreams"
    assert config.dream_promotion.kinds == ["fact", "procedure", "decision"]

    tuned = parse_kb_config(
        {
            "dream_promotion": {
                "enabled": True,
                "min_confidence": 0.85,
                "daily_limit": 10,
                "kinds": ["fact"],
            }
        }
    )
    assert tuned.dream_promotion.enabled is True
    assert tuned.dream_promotion.min_confidence == 0.85
    assert tuned.dream_promotion.daily_limit == 10
    assert tuned.dream_promotion.kinds == ["fact"]


# ── Rollback script ───────────────────────────────────────────────


def _seed_dreams_kb(main_db: Path, kb_dir: Path) -> None:
    kb_dir.mkdir(parents=True, exist_ok=True)
    kb = kb_dir / "dreams.db"
    con = sqlite3.connect(str(kb))
    con.executescript(
        """
        CREATE TABLE dreams_docs (doc_id TEXT PRIMARY KEY, title TEXT, content TEXT,
            status TEXT, metadata_json TEXT, created_at TEXT, updated_at TEXT,
            retrieval_text TEXT, path TEXT, source_id TEXT, chunk_index INTEGER,
            parent_id TEXT, root_id TEXT, node_type TEXT);
        CREATE TABLE dreams_doc_fts (doc_id TEXT, title_index TEXT, content_index TEXT);
        CREATE TABLE dreams_doc_embeddings (embedding_id TEXT PRIMARY KEY,
            doc_id TEXT, provider_id TEXT, model TEXT, dimensions INTEGER,
            content_hash TEXT, embedding_json TEXT, created_at TEXT);
        """
    )
    for index, (promoted, source) in enumerate(
        [(True, "m1"), (True, "m2"), (False, "wiki")]
    ):
        metadata = (
            {"dream_promotion": True, "promoted_from_item_id": source}
            if promoted
            else {"path": "manual.md"}
        )
        con.execute(
            "INSERT INTO dreams_docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"doc{index}",
                "t",
                "c",
                "active",
                json.dumps(metadata),
                "2026",
                "2026",
                "",
                "",
                source,
                0,
                "",
                "",
                "passage",
            ),
        )
        con.execute(
            "INSERT INTO dreams_doc_embeddings VALUES (?,?, 'p','m',4,'h','[]','t')",
            (f"e{index}", f"doc{index}"),
        )
    con.commit()
    con.close()

    main = sqlite3.connect(str(main_db))
    main.executescript(
        """
        CREATE TABLE plugin_data (plugin_id TEXT, key TEXT, value_json TEXT,
            created_at TEXT, updated_at TEXT);
        """
    )
    main.execute(
        "INSERT INTO plugin_data VALUES ('knowledge_base', 'dream_promotions', ?, 't','t')",
        (json.dumps({"m1": {"status": "promoted"}, "m2": {"status": "promoted"}}),),
    )
    main.commit()
    main.close()


def test_rollback_removes_promoted_docs_and_ledger(tmp_path: Path) -> None:
    main_db = tmp_path / "main.db"
    kb_dir = tmp_path / "kb"
    _seed_dreams_kb(main_db, kb_dir)

    rollback.main_args = None  # not used; call internals directly
    kb_con = sqlite3.connect(str(kb_dir / "dreams.db"))
    docs = rollback._find_docs(kb_con, "dreams")
    kb_con.close()
    assert [doc_id for doc_id, _ in docs] == ["doc0", "doc1"]

    # Run the CLI path.
    import sys as _sys

    _sys.argv = [
        "rollback",
        "--db",
        str(main_db),
        "--kb-dir",
        str(kb_dir),
    ]
    rollback.main()

    kb_con = sqlite3.connect(str(kb_dir / "dreams.db"))
    remaining = kb_con.execute("SELECT COUNT(*) FROM dreams_docs").fetchone()[0]
    remaining_emb = kb_con.execute(
        "SELECT COUNT(*) FROM dreams_doc_embeddings"
    ).fetchone()[0]
    kb_con.close()
    assert remaining == 1  # only the manually-imported doc survives
    assert remaining_emb == 1

    main_con = sqlite3.connect(str(main_db))
    ledger_rows = main_con.execute(
        "SELECT COUNT(*) FROM plugin_data WHERE key='dream_promotions'"
    ).fetchone()[0]
    main_con.close()
    assert ledger_rows == 0
