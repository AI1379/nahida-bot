"""Generic document storage with full-text and vector search.

This module provides a reusable storage layer that can power knowledge bases,
document collections, or any system that needs FTS5 + vector similarity
retrieval over text documents.

Key classes:

* :class:`DocumentStore` — abstract interface for a named collection.
* :class:`SQLiteDocumentStore` — SQLite-backed implementation.
* :class:`DocumentStoreManager` — collection lifecycle management.

Supporting modules:

* :mod:`embedding` — embedding provider protocol and implementations.
* :mod:`vector` — vector index protocol and SQLite-vec implementation.
* :mod:`tokenization` — CJK-aware keyword extraction and FTS query building.
* :mod:`models` — domain dataclasses.
* :mod:`repository` — raw SQL data access layer.
"""

from nahida_bot.agent.storage.document_store import DocumentStore
from nahida_bot.agent.storage.embedding import (
    EmbeddingProvider,
    EmbeddingResult,
    HashEmbeddingProvider,
    RoutedEmbeddingProvider,
    memory_text_hash,
)
from nahida_bot.agent.storage.manager import DocumentStoreManager
from nahida_bot.agent.storage.models import (
    DocumentEmbedding,
    DocumentItem,
    SearchResult,
)
from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore
from nahida_bot.agent.storage.tokenization import (
    build_fts_query,
    extract_keywords,
    tokenize_for_fts,
)
from nahida_bot.agent.storage.vector import (
    NoopVectorIndex,
    SQLiteVecIndex,
    VectorHit,
    VectorIndex,
    VectorRecord,
    cosine_similarity,
    reciprocal_rank_fusion,
)

__all__ = [
    # Store
    "DocumentStore",
    "SQLiteDocumentStore",
    "DocumentStoreManager",
    # Models
    "DocumentItem",
    "DocumentEmbedding",
    "SearchResult",
    # Embedding
    "EmbeddingProvider",
    "EmbeddingResult",
    "HashEmbeddingProvider",
    "RoutedEmbeddingProvider",
    "memory_text_hash",
    # Vector
    "VectorIndex",
    "VectorRecord",
    "VectorHit",
    "NoopVectorIndex",
    "SQLiteVecIndex",
    "cosine_similarity",
    "reciprocal_rank_fusion",
    # Tokenization
    "extract_keywords",
    "tokenize_for_fts",
    "build_fts_query",
]
