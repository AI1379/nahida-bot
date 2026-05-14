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
from nahida_bot.agent.memory.store import MemoryStore
from nahida_bot.agent.memory.embedding import (
    EmbeddingProvider,
    EmbeddingResult,
    HashEmbeddingProvider,
    RoutedEmbeddingProvider,
)
from nahida_bot.agent.memory.vector import (
    NoopVectorIndex,
    SQLiteVecIndex,
    VectorHit,
    VectorIndex,
    VectorRecord,
)

__all__ = [
    "ConversationTurn",
    "ExtractedMemory",
    "LlmMemoryDreamer",
    "MemoryCandidate",
    "MemoryConsolidator",
    "MemoryDream",
    "MemoryEmbedding",
    "MemoryItem",
    "MemoryRecord",
    "MemoryStore",
    "SQLiteMemoryStore",
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
]
