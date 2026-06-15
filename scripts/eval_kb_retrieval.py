"""Run a local KB retrieval eval against the current SQLite FTS implementation.

The dataset directory is local-only. By default this script reads:

- kb-eval-data/teyvat/manifest.jsonl
- kb-eval-data/teyvat/queries.seed.jsonl

Metrics include document-level hits and heading-level hits. A document hit
counts when any returned chunk has metadata matching an expected source path.
A heading hit additionally requires the chunk title to match the expected
heading.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nahida_bot.agent.storage.sqlite_document_store import (  # noqa: E402
    SQLiteDocumentStore,
)
from nahida_bot.db.engine import DatabaseEngine  # noqa: E402
from nahida_bot.plugins.knowledge_base.ingestion import import_document  # noqa: E402

DEFAULT_DATASET = ROOT / "kb-eval-data" / "teyvat"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected object at {path}:{line_no}")
            rows.append(row)
    return rows


def _expected_paths(query: dict[str, Any]) -> set[str]:
    expected = query.get("expected")
    if not isinstance(expected, list):
        return set()
    paths: set[str] = set()
    for item in expected:
        if not isinstance(item, dict):
            continue
        value = item.get("relative_path")
        if isinstance(value, str) and value:
            paths.add(value)
    return paths


def _expected_headings(query: dict[str, Any]) -> set[str]:
    expected = query.get("expected")
    if not isinstance(expected, list):
        return set()
    headings: set[str] = set()
    for item in expected:
        if not isinstance(item, dict):
            continue
        value = item.get("heading")
        if isinstance(value, str) and value:
            headings.add(value)
    return headings


def _record_path(record: dict[str, Any]) -> Path:
    absolute = record.get("absolute_path")
    if isinstance(absolute, str) and absolute:
        return Path(absolute)
    raise ValueError(f"Manifest record has no absolute_path: {record!r}")


def _limit_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return rows
    return rows[:limit]


def _select_records(
    records: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    *,
    only_expected: bool,
    max_docs: int,
) -> list[dict[str, Any]]:
    if not only_expected:
        return _limit_rows(records, max_docs)

    wanted_paths = set().union(*(_expected_paths(query) for query in queries))
    selected = [
        record
        for record in records
        if isinstance(record.get("relative_path"), str)
        and record["relative_path"] in wanted_paths
    ]
    return _limit_rows(selected, max_docs)


async def _import_records(
    store: SQLiteDocumentStore,
    records: list[dict[str, Any]],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> int:
    imported_chunks = 0
    for index, record in enumerate(records, start=1):
        path = _record_path(record)
        content = path.read_text(encoding="utf-8-sig", errors="replace")
        imported_chunks += await import_document(
            store,
            source_id=str(record.get("source_id") or path.stem),
            content=content,
            content_type="markdown",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            extra_metadata={
                "relative_path": str(record.get("relative_path", "")),
                "source_title": str(record.get("title", "")),
                "category": str(record.get("category", "")),
                "page_id": record.get("page_id"),
            },
        )
        if index % 500 == 0:
            print(f"Imported {index}/{len(records)} docs...", file=sys.stderr)
    return imported_chunks


def _rank_for_expected(results: list[Any], expected_paths: set[str]) -> int | None:
    for rank, result in enumerate(results, start=1):
        metadata = getattr(result, "metadata", {})
        if not isinstance(metadata, dict):
            continue
        relative_path = metadata.get("relative_path")
        if isinstance(relative_path, str) and relative_path in expected_paths:
            return rank
    return None


def _base_title(title: str) -> str:
    marker = " (part "
    if marker in title:
        return title.split(marker, 1)[0]
    return title


def _rank_for_expected_heading(
    results: list[Any],
    expected_paths: set[str],
    expected_headings: set[str],
) -> int | None:
    if not expected_headings:
        return None
    for rank, result in enumerate(results, start=1):
        metadata = getattr(result, "metadata", {})
        if not isinstance(metadata, dict):
            continue
        relative_path = metadata.get("relative_path")
        if not isinstance(relative_path, str) or relative_path not in expected_paths:
            continue
        if _base_title(str(getattr(result, "title", ""))) in expected_headings:
            return rank
    return None


async def _run_eval(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.manifest.resolve()
    queries_path = args.queries.resolve()
    records = _read_jsonl(manifest_path)
    queries = _limit_rows(_read_jsonl(queries_path), max(0, int(args.max_queries)))
    records = _select_records(
        records,
        queries,
        only_expected=bool(args.only_expected),
        max_docs=max(0, int(args.max_docs)),
    )
    if not records:
        raise ValueError("No manifest records selected for import")
    if not queries:
        raise ValueError("No queries selected for evaluation")

    db_path = str(args.db.resolve()) if args.db else ":memory:"
    engine = DatabaseEngine(db_path)
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection=args.collection)
        await store.setup()
        print(
            f"Importing {len(records)} docs into {db_path}...",
            file=sys.stderr,
        )
        chunk_count = await _import_records(
            store,
            records,
            chunk_size=max(1, int(args.chunk_size)),
            chunk_overlap=max(0, int(args.chunk_overlap)),
        )

        details: list[dict[str, Any]] = []
        doc_reciprocal_sum = 0.0
        heading_reciprocal_sum = 0.0
        doc_hits = 0
        heading_hits = 0
        for query in queries:
            expected = _expected_paths(query)
            expected_headings = _expected_headings(query)
            results = await store.search(str(query.get("query", "")), limit=args.k)
            doc_rank = _rank_for_expected(results, expected)
            heading_rank = _rank_for_expected_heading(
                results,
                expected,
                expected_headings,
            )
            if doc_rank is not None:
                doc_hits += 1
                doc_reciprocal_sum += 1.0 / doc_rank
            if heading_rank is not None:
                heading_hits += 1
                heading_reciprocal_sum += 1.0 / heading_rank
            details.append(
                {
                    "id": query.get("id", ""),
                    "query": query.get("query", ""),
                    "expected_paths": sorted(expected),
                    "expected_headings": sorted(expected_headings),
                    "rank": doc_rank,
                    "doc_rank": doc_rank,
                    "heading_rank": heading_rank,
                    "hits": [
                        {
                            "rank": idx,
                            "doc_id": result.doc_id,
                            "title": result.title,
                            "score": result.score,
                            "relative_path": result.metadata.get("relative_path", ""),
                        }
                        for idx, result in enumerate(results, start=1)
                    ],
                }
            )

        total = len(queries)
        summary = {
            "manifest": str(manifest_path),
            "queries": str(queries_path),
            "db": db_path,
            "collection": args.collection,
            "documents_imported": len(records),
            "chunks_imported": chunk_count,
            "queries_evaluated": total,
            "k": args.k,
            "recall_at_k": doc_hits / total if total else 0.0,
            "mrr_at_k": doc_reciprocal_sum / total if total else 0.0,
            "hits": doc_hits,
            "doc_recall_at_k": doc_hits / total if total else 0.0,
            "doc_mrr_at_k": doc_reciprocal_sum / total if total else 0.0,
            "doc_hits": doc_hits,
            "heading_recall_at_k": heading_hits / total if total else 0.0,
            "heading_mrr_at_k": heading_reciprocal_sum / total if total else 0.0,
            "heading_hits": heading_hits,
        }
        if args.results:
            args.results.parent.mkdir(parents=True, exist_ok=True)
            with args.results.open("w", encoding="utf-8", newline="\n") as handle:
                for row in details:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                    handle.write("\n")
            summary["results"] = str(args.results.resolve())
        return summary
    finally:
        await engine.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_DATASET / "manifest.jsonl",
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_DATASET / "queries.seed.jsonl",
    )
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--collection", default="kb_eval")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--chunk-overlap", type=int, default=50)
    parser.add_argument("--max-docs", type=int, default=0)
    parser.add_argument("--max-queries", type=int, default=0)
    parser.add_argument(
        "--only-expected",
        action="store_true",
        help="Import only documents referenced by selected query gold paths.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        summary = asyncio.run(_run_eval(args))
    except Exception as exc:  # noqa: BLE001
        print(f"Eval failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
