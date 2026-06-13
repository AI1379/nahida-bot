# Knowledge Base System Design

## Overview

The Knowledge Base (KB) system provides document import, storage, and search capabilities to supplement the model's built-in world knowledge. It is implemented as a builtin plugin (`nahida_bot/plugins/knowledge_base/`) backed by the generic `DocumentStore` from `agent/storage/`.

## Architecture

```
User                    Agent (LLM)
  |                        |
  | /kb import ...         | kb_search tool call
  v                        v
KnowledgeBasePlugin ----> DocumentStoreManager
  |                        |
  | ingestion (chunking)   | DocumentStore (per-collection)
  v                        v
Plugin Data (metadata)    SQLite Tables ({coll}_docs, {coll}_doc_fts, {coll}_doc_embeddings)
```

**Key principle**: KB scope is "hard" — each knowledge base is a separate collection with its own physical tables. This differs from Memory's "soft" scope (same table, query-time filtering).

## Current Version (v1)

- **Tool-based retrieval**: Agent calls `kb_search` tool when it needs knowledge
- **Static PromptSupplement**: Tells the agent which collections are available
- **FTS5 search only**: No embedding/vector search in v1
- **Chunking**: Paragraph-based splitting with configurable size and overlap
- **Import formats**: Markdown (heading-aware) and plain text
- **Commands**: `/kb import`, `/kb import-text`, `/kb list`, `/kb search`, `/kb delete`, `/kb info`

## Planned: v1.5 — Per-collection Auto-inject

### Problem

Tool-based retrieval depends on the agent knowing when to search. When the agent "doesn't know that it doesn't know" something, it will confidently hallucinate instead of calling `kb_search`.

### Approach

Add **dynamic PromptSupplement** support to `PromptSupplementRegistry`:

```python
@dataclass
class PromptSupplementEntry:
    key: str
    instruction: str | None                                           # static
    dynamic_instruction: Callable[[MessageContext], Awaitable[str | None]] | None  # NEW
    plugin_id: str
    channel: str | None
    filter: Callable[[MessageContext], bool] | None
```

Each KB collection gets an `auto_inject` toggle (`/kb auto <name> on/off`). When enabled:

1. Plugin registers a dynamic supplement
2. On every turn, `get_matching()` calls the async callable
3. The callable runs a **lightweight FTS search** on user's message against auto-inject collections
4. If results score above a threshold, returns formatted text → injected into system prompt
5. If no relevant results, returns `None` → zero context overhead

**Cost**: FTS5 MATCH on 1-2 collections is sub-millisecond. No embedding API calls. Token cost only when relevant content is found (capped at 3 results / 2000 chars).

**Config addition**:

```yaml
knowledge_base:
  auto_inject: false          # global default
  auto_inject_max_results: 3
  auto_inject_max_chars: 2000
```

Per-collection metadata stored in plugin_data:

```json
{
  "python_docs": {"auto_inject": true, "created_at": "..."},
  "policies": {"auto_inject": false, "created_at": "..."}
}
```

## Planned: v2 — Full RAG

### Upgrades from v1.5

1. **Embedding-powered search**: When `memory.embedding.enabled` is true, KB auto-inject and `kb_search` tool use vector/hybrid search instead of FTS-only
2. **Collection-specific embedding**: Each collection gets its own `SQLiteVecIndex` (parameterized `map_table`)
3. **Smart chunking**: Semantic chunking (split by topic/section boundaries rather than fixed size)
4. **Source attribution**: Search results include provenance (file, section, page number)
5. **Incremental updates**: Re-import only changed documents (content hash comparison)
6. **URL import**: Fetch web pages, extract text, auto-chunk
7. **PDF import**: Extract text from PDF files

### Prerequisites

- `memory.embedding.enabled: true` in config (or a separate KB-specific embedding config)
- `RoutedEmbeddingProvider` initialized in Application
- Per-collection `SQLiteVecIndex` created during `DocumentStoreManager.get_or_create()`

### New Commands

- `/kb import-url <collection> <url>` — fetch and import a web page
- `/kb import-file <collection> <file>` — import PDF, DOCX, etc.
- `/kb reindex <collection>` — re-embed all documents
- `/kb stats` — show embedding coverage, index sizes

### New Tool Parameters

```json
{
  "kb_search": {
    "search_mode": "auto|fts|vector|hybrid",  // NEW: search strategy
    "min_score": 0.5                           // NEW: relevance threshold
  }
}
```

## Relationship to Memory System (#12)

| Dimension | Memory | Knowledge Base |
|-----------|--------|----------------|
| Content | User preferences, decisions, conversation history | World knowledge, documents, reference material |
| Source | Auto-extracted from conversations | User-initiated import |
| Scope type | Soft (query-time filtering by chat/user) | Hard (separate tables per collection) |
| Scope semantics | Who/where said it | What topic it covers |
| Lifecycle | Created and pruned automatically | Explicitly managed by user |
| Injection | Every turn via `_load_relevant_memory` | v1: tool-based; v1.5: optional auto-inject |
| Storage | `memory_items` + `memory_item_fts` + `memory_embeddings` | `{coll}_docs` + `{coll}_doc_fts` + `{coll}_doc_embeddings` |
| Shared infrastructure | `EmbeddingProvider`, `VectorIndex`, FTS5 tokenization | Same |

The two systems share the **storage engine** (`agent/storage/`) but maintain independent schemas and scope semantics.
