"""Abstract base class for the generic document store."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from nahida_bot.agent.storage.embedding import EmbeddingProvider
from nahida_bot.agent.storage.models import DocumentItem, SearchResult
from nahida_bot.agent.storage.vector import VectorIndex


class DocumentStore(ABC):
    """Generic document storage with full-text and vector search.

    Each instance manages one named collection.  Collections are physically
    isolated — separate tables, separate FTS index, separate vector index.

    The store handles document CRUD, FTS5 BM25 search, vector similarity
    search (via sqlite-vec or cosine fallback), and hybrid search with
    reciprocal rank fusion.
    """

    @property
    @abstractmethod
    def collection(self) -> str:
        """Name of the collection this store manages."""
        ...

    @abstractmethod
    async def setup(self) -> None:
        """Create tables and indexes for this collection.

        Safe to call multiple times (uses ``IF NOT EXISTS``).
        """
        ...

    @abstractmethod
    async def put(
        self,
        doc_id: str,
        content: str,
        *,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or replace a document in the collection."""
        ...

    @abstractmethod
    async def get(self, doc_id: str) -> DocumentItem | None:
        """Retrieve a single document by ID."""
        ...

    @abstractmethod
    async def delete(self, doc_id: str) -> bool:
        """Delete a document.  Returns ``True`` if it existed."""
        ...

    @abstractmethod
    async def count(self) -> int:
        """Count active documents in the collection."""
        ...

    @abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        """Full-text search using FTS5 BM25."""
        ...

    @abstractmethod
    async def search_vector(
        self,
        query: str,
        provider: EmbeddingProvider,
        *,
        limit: int = 10,
        vector_index: VectorIndex | None = None,
    ) -> list[SearchResult]:
        """Vector similarity search."""
        ...

    @abstractmethod
    async def search_hybrid(
        self,
        query: str,
        provider: EmbeddingProvider,
        *,
        limit: int = 10,
        vector_index: VectorIndex | None = None,
    ) -> list[SearchResult]:
        """Hybrid search combining FTS + vector with reciprocal rank fusion."""
        ...

    @abstractmethod
    async def put_embedding(
        self,
        doc_id: str,
        embedding: list[float],
        *,
        provider_id: str,
        model: str,
        content_hash: str,
        vector_index: VectorIndex | None = None,
    ) -> str:
        """Persist an embedding for a document and optionally upsert into a vector index."""
        ...

    @abstractmethod
    async def embed_documents(
        self,
        provider: EmbeddingProvider,
        *,
        limit: int = 100,
        vector_index: VectorIndex | None = None,
    ) -> int:
        """Batch-embed documents that lack embeddings.  Returns count embedded."""
        ...
