"""Prepare a local Teyvat KB retrieval eval dataset.

This script does not copy the full corpus into the repository. It scans a
Genshin Impact Wiki markdown export and writes local-only metadata files:

- manifest.jsonl: one source document per line, with path/title/category data
- queries.seed.jsonl: deterministic seed queries with expected source paths
- summary.json: corpus counts
- README.md: local usage notes

The output directory is intended to be excluded with .git/info/exclude.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "kb-eval-data" / "teyvat"
SOURCE_ENV_VAR = "KB_EVAL_TEYVAT_SOURCE"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_STEM_ID_RE = re.compile(r"^(?P<title>.+)_(?P<page_id>\d+)$")

_INTERESTING_HEADINGS = {
    "基础信息": "basic",
    "角色详细": "character_detail",
    "神之眼": "vision",
    "神之心": "gnosis",
    "命之座": "constellation",
    "特殊料理": "special_dish",
}
_STORY_RE = re.compile(r"^角色故事[1-5]$")
_CATEGORY_QUERY_PRIORITY = {
    "25_角色": 0,
    "68_书籍": 1,
    "43_任务": 2,
}
_TAG_QUERY_PRIORITY = {
    "character_story": 0,
    "vision": 1,
    "gnosis": 2,
    "character_detail": 3,
    "constellation": 4,
    "special_dish": 5,
    "basic": 6,
}


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    title: str
    line: int


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    source_id: str
    title: str
    page_id: int | None
    category: str
    relative_path: str
    absolute_path: str
    bytes: int
    sha256: str
    headings: list[Heading]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _title_and_page_id(path: Path) -> tuple[str, int | None]:
    match = _STEM_ID_RE.match(path.stem)
    if match is None:
        return path.stem, None
    return match.group("title"), int(match.group("page_id"))


def _extract_headings(text: str) -> list[Heading]:
    headings: list[Heading] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = _HEADING_RE.match(line)
        if match is None:
            continue
        headings.append(
            Heading(
                level=len(match.group(1)),
                title=match.group(2).strip(),
                line=line_no,
            )
        )
    return headings


def _document_record(path: Path, source: Path) -> DocumentRecord:
    text = _read_text(path)
    headings = _extract_headings(text)
    stem_title, page_id = _title_and_page_id(path)
    title = headings[0].title if headings and headings[0].level == 1 else stem_title
    rel = path.relative_to(source)
    category = rel.parts[0] if len(rel.parts) > 1 else ""
    stat = path.stat()
    return DocumentRecord(
        source_id=path.stem,
        title=title,
        page_id=page_id,
        category=category,
        relative_path=rel.as_posix(),
        absolute_path=str(path.resolve()),
        bytes=stat.st_size,
        sha256=_sha256_file(path),
        headings=headings,
    )


def _iter_markdown_files(
    source: Path,
    *,
    categories: set[str],
    max_docs: int,
) -> list[Path]:
    files: list[Path] = []
    for path in sorted(
        source.rglob("*.md"), key=lambda p: p.relative_to(source).as_posix()
    ):
        rel = path.relative_to(source)
        category = rel.parts[0] if len(rel.parts) > 1 else ""
        if categories and category not in categories:
            continue
        files.append(path)
        if max_docs > 0 and len(files) >= max_docs:
            break
    return files


def _seed_query_for(title: str, heading: str) -> tuple[str, str] | None:
    if _STORY_RE.match(heading):
        return f"{title}的{heading}讲了什么", "character_story"
    tag = _INTERESTING_HEADINGS.get(heading)
    if tag is None:
        return None
    if heading == "基础信息":
        return f"{title}的基础信息", tag
    if heading == "神之眼":
        return f"{title}的神之眼相关故事", tag
    if heading == "神之心":
        return f"{title}的神之心相关信息", tag
    if heading == "特殊料理":
        return f"{title}的特殊料理是什么", tag
    return f"{title} {heading}", tag


def _query_id(record: DocumentRecord, heading: Heading) -> str:
    raw = f"{record.relative_path}\0{heading.title}\0{heading.line}"
    return f"teyvat_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def _build_seed_queries(
    records: list[DocumentRecord],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    candidates: list[tuple[tuple[int, int, str, int], dict[str, Any]]] = []
    for record in records:
        for heading in record.headings:
            seed = _seed_query_for(record.title, heading.title)
            if seed is None:
                continue
            query, tag = seed
            priority = (
                _CATEGORY_QUERY_PRIORITY.get(record.category, 99),
                _TAG_QUERY_PRIORITY.get(tag, 99),
                record.title,
                heading.line,
            )
            candidates.append(
                (
                    priority,
                    {
                        "id": _query_id(record, heading),
                        "query": query,
                        "expected": [
                            {
                                "relative_path": record.relative_path,
                                "source_id": record.source_id,
                                "title": record.title,
                                "heading": heading.title,
                                "line": heading.line,
                            }
                        ],
                        "tags": ["teyvat", "seed", tag, record.category],
                        "notes": (
                            "Auto-generated seed query. Edit or replace with a "
                            "human-written query before treating it as gold."
                        ),
                    },
                )
            )
    queries = [row for _priority, row in sorted(candidates, key=lambda item: item[0])]
    if limit > 0:
        return queries[:limit]
    return queries


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _record_to_json(record: DocumentRecord) -> dict[str, Any]:
    payload = asdict(record)
    payload["headings"] = [asdict(heading) for heading in record.headings]
    return payload


def _write_readme(out: Path, source: Path) -> None:
    content = f"""# Local KB Eval Dataset: Teyvat

This directory is generated local data and should not be committed.

Source export:

```text
{source}
```

Files:

- `manifest.jsonl`: source document metadata and heading inventory.
- `queries.seed.jsonl`: deterministic seed queries. Treat these as scaffolding,
  not authoritative gold until reviewed.
- `summary.json`: corpus statistics.

Regenerate:

```bash
uv run python scripts/prepare_kb_eval_teyvat.py --source "{source}"
```

Run a smoke retrieval eval:

```bash
uv run python scripts/eval_kb_retrieval.py --only-expected --max-queries 20
```
"""
    (out / "README.md").write_text(content, encoding="utf-8", newline="\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help=f"Markdown export root. Defaults to ${SOURCE_ENV_VAR} when set.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--categories",
        nargs="*",
        default=[],
        help="Optional category directory names, e.g. 25_角色 68_书籍.",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=0,
        help="Limit scanned documents for quick local experiments. 0 means all.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=200,
        help="Limit generated seed queries. 0 means all candidates.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    source_arg = args.source or (
        Path(env_source) if (env_source := os.environ.get(SOURCE_ENV_VAR)) else None
    )
    if source_arg is None:
        print(
            f"Source directory is required. Pass --source or set {SOURCE_ENV_VAR}.",
            file=sys.stderr,
        )
        return 2
    source = source_arg.resolve()
    out = args.out.resolve()
    if not source.exists() or not source.is_dir():
        print(f"Source directory not found: {source}", file=sys.stderr)
        return 2

    categories = {str(category) for category in args.categories}
    files = _iter_markdown_files(
        source,
        categories=categories,
        max_docs=max(0, int(args.max_docs)),
    )
    records = [_document_record(path, source) for path in files]
    queries = _build_seed_queries(records, limit=max(0, int(args.max_queries)))

    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        out / "manifest.jsonl", [_record_to_json(record) for record in records]
    )
    _write_jsonl(out / "queries.seed.jsonl", queries)

    category_counts = Counter(record.category for record in records)
    summary = {
        "source_root": str(source),
        "documents": len(records),
        "bytes": sum(record.bytes for record in records),
        "categories": dict(sorted(category_counts.items())),
        "seed_queries": len(queries),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    _write_readme(out, source)

    print(
        f"Wrote {len(records)} docs and {len(queries)} seed queries to {out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
