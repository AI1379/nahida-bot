from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nahida_bot.plugins.knowledge_base.ingestion import parse_markdown
from nahida_bot.plugins.knowledge_base.plugin import KnowledgeBasePlugin
from nahida_bot.plugins.manifest import parse_manifest

from .helpers import RecordingMockBotAPI


def _kb_manifest():
    root = Path(__file__).resolve().parents[1]
    return parse_manifest(
        root / "nahida_bot" / "plugins" / "knowledge_base" / "plugin.yaml"
    )


class _Store:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def put(
        self,
        doc_id: str,
        content: str,
        *,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.docs[doc_id] = {
            "content": content,
            "title": title,
            "metadata": metadata or {},
        }

    async def count(self) -> int:
        return len(self.docs)


class _Manager:
    def __init__(self) -> None:
        self._stores: dict[str, _Store] = {}

    async def create(self, name: str) -> _Store:
        if name in self._stores:
            raise ValueError(f"Collection '{name}' already exists")
        store = _Store()
        self._stores[name] = store
        return store

    async def get_or_create(self, name: str) -> _Store:
        if name not in self._stores:
            self._stores[name] = _Store()
        return self._stores[name]

    def get(self, name: str) -> _Store | None:
        return self._stores.get(name)

    def list_collections(self) -> list[str]:
        return list(self._stores.keys())

    async def delete_collection(self, name: str) -> bool:
        return self._stores.pop(name, None) is not None


class _StrictRecordingAPI(RecordingMockBotAPI):
    def __init__(self, manager: _Manager) -> None:
        super().__init__()
        self._manager = manager

    def get_document_store_manager(self) -> _Manager:
        return self._manager

    def register_prompt_supplement(
        self,
        key: str,
        instruction: str,
        *,
        channel: str | None = None,
        filter=None,
    ) -> None:
        if key in self.registered_prompt_supplements:
            raise KeyError(f"Prompt supplement '{key}' is already registered")
        super().register_prompt_supplement(
            key,
            instruction,
            channel=channel,
            filter=filter,
        )


@pytest.mark.asyncio
async def test_import_content_refreshes_prompt_supplement_and_persists_metadata() -> (
    None
):
    manager = _Manager()
    api = _StrictRecordingAPI(manager)
    plugin = KnowledgeBasePlugin(api=api, manifest=_kb_manifest())

    await plugin.on_load()
    count = await plugin.import_content(
        "python_docs",
        source_id="Guide",
        content="Paragraph one.\n\nParagraph two.",
    )

    assert count == 1
    assert set(api.registered_prompt_supplements) == {"kb_context"}
    assert (
        "'python_docs'"
        in api.registered_prompt_supplements["kb_context"]["instruction"]
    )

    summaries = await plugin.list_collection_summaries()
    meta = await api.plugin_data_get("kb_collections")

    assert summaries == [
        {
            "name": "python_docs",
            "document_count": 1,
            "created_at": meta["python_docs"]["created_at"],
        }
    ]


def test_parse_markdown_duplicate_headings_keep_unique_chunk_ids() -> None:
    chunks = parse_markdown(
        "## Example\nFirst body.\n\n## Example\nSecond body.",
        source_id="guide",
    )

    assert len(chunks) == 2
    assert len({chunk.doc_id for chunk in chunks}) == 2
    assert [chunk.title for chunk in chunks] == ["Example", "Example"]
