"""Tests for the read-only ``search_files`` tool (whitelisted roots)."""

from __future__ import annotations

import pytest

from nahida_bot.plugins.builtin.tools.file_search import FileSearchTools


@pytest.fixture
def root(tmp_path):
    (tmp_path / "doc.md").write_text(
        "Elsa was frozen in Ahtohallan\nnothing relevant here", encoding="utf-8"
    )
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.txt").write_text("安娜 went to find her sister", encoding="utf-8")
    vendor = tmp_path / ".git"
    vendor.mkdir()
    (vendor / "config.md").write_text("Elsa Elsa Elsa", encoding="utf-8")
    return tmp_path


def _tools(root) -> FileSearchTools:
    return FileSearchTools(None, [str(root)])


@pytest.mark.asyncio
async def test_finds_case_insensitive_matches_with_line_numbers(root) -> None:
    out = await _tools(root).search("elsa")
    assert "doc.md" in out
    assert "L1: Elsa was frozen in Ahtohallan" in out


@pytest.mark.asyncio
async def test_cjk_substring_matches(root) -> None:
    out = await _tools(root).search("安娜")
    assert "a.txt" in out


@pytest.mark.asyncio
async def test_prunes_vendor_directories(root) -> None:
    out = await _tools(root).search("elsa")
    assert ".git" not in out


@pytest.mark.asyncio
async def test_narrows_by_root_relative_path(root) -> None:
    out = await _tools(root).search("安娜", path="notes")
    assert "a.txt" in out
    out_all = await _tools(root).search("elsa", path="notes")
    assert "No matches" in out_all


@pytest.mark.asyncio
async def test_rejects_absolute_and_escaping_paths(root) -> None:
    tools = _tools(root)
    assert "Error" in await tools.search("elsa", path=str(root))
    assert "Error" in await tools.search("elsa", path="../outside")


@pytest.mark.asyncio
async def test_glob_filters_filenames(root) -> None:
    out = await _tools(root).search("安娜", glob="*.md")
    assert "No matches" in out


@pytest.mark.asyncio
async def test_no_roots_configured_reports_clearly(tmp_path) -> None:
    tools = FileSearchTools(None, [str(tmp_path / "missing")])
    assert not tools.configured
    assert not tools.definitions()
    out = await tools.search("anything")
    assert "not configured" in out


@pytest.mark.asyncio
async def test_empty_query_is_rejected(root) -> None:
    assert "empty" in await _tools(root).search("  ")


@pytest.mark.asyncio
async def test_direct_file_target_searches_only_that_file(root) -> None:
    out = await _tools(root).search("Ahtohallan", path="doc.md")
    assert "L1:" in out
    assert "a.txt" not in out
