from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nahida_bot.agent.providers.router import ModelRouter
from nahida_bot.agent.storage.document_store import BackfillResult
from nahida_bot.agent.storage.manager import DocumentStoreManager
from nahida_bot.agent.storage.models import SearchResult
from nahida_bot.agent.storage.vector import SQLiteVecIndex
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.plugins.knowledge_base.ingestion import parse_markdown
from nahida_bot.plugins.knowledge_base.plugin import KnowledgeBasePlugin
from nahida_bot.plugins.manifest import parse_manifest

from .helpers import RecordingMockBotAPI


def _kb_manifest() -> Any:
    root = Path(__file__).resolve().parents[1]
    return parse_manifest(
        root / "nahida_bot" / "plugins" / "knowledge_base" / "plugin.yaml"
    )


def _kb_manifest_with_config(config: dict[str, Any]) -> Any:
    manifest = _kb_manifest()
    merged = dict(manifest.config or {})
    merged.update(config)
    return manifest.model_copy(update={"config": merged})


class _Store:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.search_results: list[SearchResult] = []
        self.vector_results: list[SearchResult] = []
        self.hybrid_results: list[SearchResult] = []
        self.search_calls: list[tuple[str, int]] = []
        self.search_vector_calls: list[tuple[str, int, bool]] = []
        self.search_hybrid_calls: list[tuple[str, int, bool]] = []
        self.embed_calls: list[tuple[int, bool]] = []
        self.embed_result_count: int | None = None
        self.hybrid_error: Exception | None = None

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

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        self.search_calls.append((query, limit))
        return self.search_results[:limit]

    async def search_vector(
        self,
        query: str,
        provider: Any,
        *,
        limit: int = 10,
        vector_index: Any | None = None,
    ) -> list[SearchResult]:
        self.search_vector_calls.append((query, limit, vector_index is not None))
        return self.vector_results[:limit]

    async def search_hybrid(
        self,
        query: str,
        provider: Any,
        *,
        limit: int = 10,
        vector_index: Any | None = None,
    ) -> list[SearchResult]:
        self.search_hybrid_calls.append((query, limit, vector_index is not None))
        if self.hybrid_error is not None:
            raise self.hybrid_error
        return self.hybrid_results[:limit]

    async def embed_documents(
        self,
        provider: Any,
        *,
        limit: int = 100,
        vector_index: Any | None = None,
    ) -> BackfillResult:
        self.embed_calls.append((limit, vector_index is not None))
        target = min(limit, len(self.docs))
        if self.embed_result_count is not None:
            # Simulate a partial / failed backfill: only ``embed_result_count``
            # documents were embedded, but ``target`` still needed embedding.
            return BackfillResult(added=self.embed_result_count, needed=target)
        return BackfillResult(added=target, needed=target)


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


class _EmbeddingBackend:
    async def embed_texts(self, texts: list[str], *, model: str) -> list[Any]:
        return [SimpleNamespace(embedding=[1.0, 0.0, 0.0, 0.0]) for _ in texts]


class _ProviderManager:
    def __init__(self) -> None:
        provider = _EmbeddingBackend()
        self._slot = SimpleNamespace(
            id="embedder",
            provider=provider,
            default_model="kb-embed",
            tags_by_model={"kb-embed": ["embedding"]},
        )
        self._slots = {"embedder": self._slot}

    @property
    def slots(self) -> list[Any]:
        return [self._slot]

    def resolve_model_selection(self, model_name: str) -> tuple[Any, str] | None:
        if model_name in ("kb-embed", "embedder/kb-embed"):
            return self._slot, "kb-embed"
        return None


class _StrictRecordingAPI(RecordingMockBotAPI):
    def __init__(
        self,
        manager: Any,
        provider_manager: Any | None = None,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._provider_manager = provider_manager
        self._model_router = (
            ModelRouter(provider_manager) if provider_manager is not None else None
        )

    def get_document_store_manager(self) -> Any:
        return self._manager

    def get_provider_manager(self) -> Any | None:
        return self._provider_manager

    def get_model_router(self) -> ModelRouter | None:
        return self._model_router

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
@pytest.mark.parametrize(
    "model_spec",
    ["", "embedding", "kb-embed", "embedder/kb-embed"],
)
async def test_embedding_uses_unified_model_spec_routing(model_spec: str) -> None:
    manager = _Manager()
    api = _StrictRecordingAPI(manager, provider_manager=_ProviderManager())
    plugin = KnowledgeBasePlugin(
        api=api,
        manifest=_kb_manifest_with_config(
            {
                "embedding": {
                    "enabled": True,
                    "model": model_spec,
                    "dimensions": 4,
                }
            }
        ),
    )

    await plugin.on_load()

    provider = plugin._embedding_provider  # noqa: SLF001
    assert provider is not None
    assert provider.provider_id == "embedder"
    assert provider.model == "kb-embed"


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


@pytest.mark.asyncio
async def test_import_content_embeds_new_collection_without_extra_backfill() -> None:
    manager = _Manager()
    api = _StrictRecordingAPI(manager, provider_manager=_ProviderManager())
    plugin = KnowledgeBasePlugin(
        api=api,
        manifest=_kb_manifest_with_config(
            {
                "retrieval": {
                    "fts_enabled": True,
                    "vector_enabled": True,
                    "hybrid_enabled": True,
                    "vector_backend": "json",
                },
                "embedding": {
                    "enabled": True,
                    "dimensions": 4,
                    "batch_size": 8,
                    "embed_after_import": True,
                },
            }
        ),
    )

    await plugin.on_load()
    count = await plugin.import_content(
        "python_docs",
        source_id="Guide",
        content="Paragraph one.\n\nParagraph two.",
    )
    store = manager.get("python_docs")
    assert store is not None
    store.hybrid_results = [
        SearchResult(doc_id="Guide_chunk_0", title="Guide", content="Paragraph one.")
    ]

    results = await plugin.search_documents("python_docs", "Paragraph", limit=3)

    assert count == 1
    assert [result.doc_id for result in results] == ["Guide_chunk_0"]
    assert store.embed_calls == [(1, False)]
    assert store.search_hybrid_calls == [("Paragraph", 3, False)]


@pytest.mark.asyncio
async def test_search_documents_uses_vector_mode_and_backfills_once() -> None:
    manager = _Manager()
    store = await manager.get_or_create("python_docs")
    await store.put("doc_1", "AsyncIO guide", title="Guide")
    store.vector_results = [
        SearchResult(doc_id="doc_1", title="Guide", content="AsyncIO guide")
    ]
    api = _StrictRecordingAPI(manager, provider_manager=_ProviderManager())
    plugin = KnowledgeBasePlugin(
        api=api,
        manifest=_kb_manifest_with_config(
            {
                "retrieval": {
                    "fts_enabled": True,
                    "vector_enabled": True,
                    "hybrid_enabled": False,
                    "vector_backend": "json",
                },
                "embedding": {
                    "enabled": True,
                    "dimensions": 4,
                    "batch_size": 8,
                    "embed_after_import": False,
                },
            }
        ),
    )

    await plugin.on_load()
    first = await plugin.search_documents("python_docs", "AsyncIO", limit=2)
    second = await plugin.search_documents("python_docs", "AsyncIO", limit=2)

    assert [result.doc_id for result in first] == ["doc_1"]
    assert [result.doc_id for result in second] == ["doc_1"]
    assert store.embed_calls == [(1, False)]
    assert store.search_vector_calls == [
        ("AsyncIO", 2, False),
        ("AsyncIO", 2, False),
    ]
    assert store.search_calls == []
    assert store.search_hybrid_calls == []


@pytest.mark.asyncio
async def test_incomplete_embedding_backfill_is_retried() -> None:
    manager = _Manager()
    store = await manager.get_or_create("python_docs")
    await store.put("doc_1", "AsyncIO guide", title="Guide")
    store.embed_result_count = 0
    api = _StrictRecordingAPI(manager, provider_manager=_ProviderManager())
    plugin = KnowledgeBasePlugin(
        api=api,
        manifest=_kb_manifest_with_config(
            {
                "retrieval": {
                    "fts_enabled": False,
                    "vector_enabled": True,
                    "hybrid_enabled": False,
                    "vector_backend": "json",
                },
                "embedding": {
                    "enabled": True,
                    "dimensions": 4,
                    "batch_size": 8,
                    "embed_after_import": False,
                },
            }
        ),
    )

    await plugin.on_load()
    await plugin.search_documents("python_docs", "AsyncIO", limit=2)
    await plugin.search_documents("python_docs", "AsyncIO", limit=2)

    assert store.embed_calls == [(1, False), (1, False)]


@pytest.mark.asyncio
async def test_hybrid_search_failure_falls_back_to_fts() -> None:
    manager = _Manager()
    store = await manager.get_or_create("python_docs")
    await store.put("doc_1", "AsyncIO guide", title="Guide")
    store.hybrid_error = RuntimeError("embedding provider unavailable")
    store.search_results = [
        SearchResult(doc_id="doc_1", title="Guide", content="AsyncIO guide")
    ]
    api = _StrictRecordingAPI(manager, provider_manager=_ProviderManager())
    plugin = KnowledgeBasePlugin(
        api=api,
        manifest=_kb_manifest_with_config(
            {
                "retrieval": {
                    "fts_enabled": True,
                    "vector_enabled": True,
                    "hybrid_enabled": True,
                    "vector_backend": "json",
                },
                "embedding": {
                    "enabled": True,
                    "dimensions": 4,
                    "batch_size": 8,
                    "embed_after_import": False,
                },
            }
        ),
    )

    await plugin.on_load()
    results = await plugin.search_documents("python_docs", "AsyncIO", limit=2)

    assert [result.doc_id for result in results] == ["doc_1"]
    assert store.search_hybrid_calls == [("AsyncIO", 2, False)]
    assert store.search_calls == [("AsyncIO", 2)]


@pytest.mark.asyncio
async def test_delete_collection_drops_uncached_sqlite_vec_index() -> None:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        manager = DocumentStoreManager(engine)
        await manager.create("python_docs")
        index = SQLiteVecIndex(
            engine,
            dimensions=4,
            table_name="kb_python_docs_embedding_vec",
            map_table="kb_python_docs_vec_map",
        )
        await index.setup()
        api = _StrictRecordingAPI(manager)
        plugin = KnowledgeBasePlugin(
            api=api,
            manifest=_kb_manifest_with_config(
                {
                    "retrieval": {
                        "fts_enabled": True,
                        "vector_enabled": True,
                        "hybrid_enabled": True,
                        "vector_backend": "sqlite-vec",
                    }
                }
            ),
        )

        await plugin.on_load()
        await plugin.delete_collection("python_docs")

        rows = await engine.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE name IN ("
            "'kb_python_docs_embedding_vec', 'kb_python_docs_vec_map'"
            ")"
        )
        assert rows == []
    finally:
        await engine.close()


def test_parse_markdown_duplicate_headings_keep_unique_chunk_ids() -> None:
    chunks = parse_markdown(
        "## Example\nFirst body.\n\n## Example\nSecond body.",
        source_id="guide",
    )

    assert len(chunks) == 2
    assert len({chunk.doc_id for chunk in chunks}) == 2
    assert [chunk.title for chunk in chunks] == ["Example", "Example"]
