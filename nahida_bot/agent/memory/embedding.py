"""Embedding provider abstractions for durable memory."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol, cast

_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(slots=True, frozen=True)
class EmbeddingResult:
    """Embedding vector plus model identity."""

    embedding: list[float]
    provider_id: str
    model: str


class EmbeddingProvider(Protocol):
    """Protocol implemented by memory embedding providers."""

    provider_id: str
    model: str
    dimensions: int

    async def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed a batch of texts."""
        ...


class HashEmbeddingProvider:
    """Deterministic local embedding provider for tests and offline fallback.

    This is not a semantic embedding model. It provides stable vectors for
    plumbing, tests, and deployments that want hybrid retrieval code paths
    without an external embedding provider.
    """

    provider_id = "local"
    model = "hash"

    def __init__(self, *, dimensions: int = 64) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    async def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        return [
            EmbeddingResult(
                embedding=self._embed_one(text),
                provider_id=self.provider_id,
                model=self.model,
            )
            for text in texts
        ]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [token for token in _TOKEN_SPLIT.split(text.casefold()) if token]
        if not tokens:
            tokens = [text.casefold()] if text else [""]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


def memory_text_hash(text: str) -> str:
    """Return a stable content hash for embedded memory text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RoutedEmbeddingProvider:
    """EmbeddingProvider adapter around a routed chat provider.

    The underlying provider must expose an ``embed_texts`` coroutine. This keeps
    embedding routing decoupled from the main ChatProvider abstract contract.
    """

    def __init__(
        self,
        provider: Any,
        *,
        provider_id: str,
        model: str,
        dimensions: int = 0,
        batch_size: int = 16,
    ) -> None:
        if not model:
            raise ValueError("embedding model must not be empty")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._provider = provider
        self.provider_id = provider_id
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size

    async def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        embed = getattr(self._provider, "embed_texts", None)
        if not callable(embed):
            raise RuntimeError(
                f"Provider '{self.provider_id}' does not support embeddings"
            )
        results: list[EmbeddingResult] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            raw_results = await cast(Any, embed)(batch, model=self.model)
            for result in raw_results:
                embedding = list(getattr(result, "embedding", []) or [])
                if self.dimensions <= 0 and embedding:
                    self.dimensions = len(embedding)
                results.append(
                    EmbeddingResult(
                        embedding=embedding,
                        provider_id=self.provider_id,
                        model=self.model,
                    )
                )
        return results
