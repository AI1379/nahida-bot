"""Memory subsystem — models, store interface, and SQLite implementation."""

from nahida_bot.agent.memory.models import (
    ConversationTurn,
    MemoryCandidate,
    MemoryEmbedding,
    MemoryItem,
    MemoryRecord,
)
from nahida_bot.agent.memory.consolidation import (
    ExtractedMemory,
    LlmMemoryDreamer,
    MemoryConsolidator,
    MemoryDream,
    RuleBasedMemoryExtractor,
    parse_memory_dream,
)
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore, extract_keywords
from nahida_bot.agent.memory.service import (
    DEFAULT_PROJECTION_LIMIT,
    MemoryService,
    project_workspace_memory,
    resolve_write_sensitivity,
)
from nahida_bot.agent.memory.store import MemoryStore, StructuredMemoryStore
from nahida_bot.agent.storage.embedding import (
    EmbeddingProvider,
    EmbeddingResult,
    HashEmbeddingProvider,
    RoutedEmbeddingProvider,
)
from nahida_bot.agent.storage.vector import (
    NoopVectorIndex,
    SQLiteVecIndex,
    VectorHit,
    VectorIndex,
    VectorRecord,
)

__all__ = [
    "ConversationTurn",
    "DEFAULT_PROJECTION_LIMIT",
    "ExtractedMemory",
    "LlmMemoryDreamer",
    "MemoryCandidate",
    "MemoryConsolidator",
    "MemoryDream",
    "MemoryEmbedding",
    "MemoryItem",
    "MemoryRecord",
    "MemoryService",
    "MemoryStore",
    "SQLiteMemoryStore",
    "StructuredMemoryStore",
    "EmbeddingProvider",
    "EmbeddingResult",
    "HashEmbeddingProvider",
    "RoutedEmbeddingProvider",
    "NoopVectorIndex",
    "RuleBasedMemoryExtractor",
    "SQLiteVecIndex",
    "VectorHit",
    "VectorIndex",
    "VectorRecord",
    "extract_keywords",
    "parse_memory_dream",
    "project_workspace_memory",
    "resolve_write_sensitivity",
]
