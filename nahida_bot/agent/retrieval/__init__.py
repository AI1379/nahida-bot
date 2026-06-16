"""Shared retrieval models and adapters for Memory and Knowledge Base."""

from nahida_bot.agent.retrieval.adapters import (
    DocumentStoreRetrievalAdapter,
    MemoryStoreRetrievalAdapter,
)
from nahida_bot.agent.retrieval.models import (
    RetrievalMode,
    RetrievalProvenance,
    RetrievalRequest,
    RetrievalResult,
    RetrievalScope,
    RetrievalSourceType,
)
from nahida_bot.agent.retrieval.service import RetrievalAdapter, RetrievalService

__all__ = [
    "DocumentStoreRetrievalAdapter",
    "MemoryStoreRetrievalAdapter",
    "RetrievalAdapter",
    "RetrievalMode",
    "RetrievalProvenance",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalScope",
    "RetrievalService",
    "RetrievalSourceType",
]
