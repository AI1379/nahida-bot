"""Knowledge Base management API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Mapping, NoReturn

import structlog
from anyio import to_thread
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from nahida_bot.gateway.deps import get_application
from nahida_bot.plugins.knowledge_base.document_conversion import (
    convert_document_bytes,
    normalize_document_filename,
)

logger = structlog.get_logger(__name__)

router = APIRouter()
_MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
_MAX_DOCUMENT_FILES = 200


# ── Response models ──────────────────────────────────


class KbCollectionSummary(BaseModel):
    name: str
    document_count: int
    created_at: str = ""


class KbCollectionListResponse(BaseModel):
    collections: list[KbCollectionSummary]


class KbDocumentResponse(BaseModel):
    doc_id: str
    title: str
    content: str
    score: float = 0.0
    path: str = ""
    source_id: str = ""
    node_type: str = "passage"
    metadata: dict[str, Any] = Field(default_factory=dict)


class KbSearchResponse(BaseModel):
    results: list[KbDocumentResponse]


class KbImportResponse(BaseModel):
    collection: str
    source: str
    chunks: int


class KbBatchImportItem(BaseModel):
    source: str
    status: Literal["imported", "failed"]
    chunks: int = 0
    error: str = ""


class KbBatchImportResponse(BaseModel):
    collection: str
    imported_files: int
    failed_files: int
    chunks: int
    results: list[KbBatchImportItem]


class KbDocumentListResponse(BaseModel):
    documents: list[KbDocumentResponse]
    total: int
    limit: int
    offset: int


class KbCollectionStatusResponse(BaseModel):
    name: str
    document_count: int
    embedding_status: str  # "idle" | "embedding" | "embedded"


class KbActionResponse(BaseModel):
    status: str
    detail: str


# ── Helpers ───────────────────────────────────────────


def _require_kb_plugin(app) -> Any:
    """Get the active knowledge-base plugin instance, or 503."""
    manager = getattr(app, "plugin_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin manager not initialized",
        )
    record = manager.get_record("knowledge_base")
    plugin = getattr(record, "instance", None) if record is not None else None
    required = (
        "list_collection_summaries",
        "get_collection_summary",
        "create_collection",
        "import_content",
        "search_documents",
        "delete_collection",
    )
    if plugin is None or any(
        not callable(getattr(plugin, name, None)) for name in required
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge base plugin is not available",
        )
    return plugin


def _summary_from_mapping(summary: Mapping[str, Any]) -> KbCollectionSummary:
    return KbCollectionSummary(
        name=str(summary.get("name", "")),
        document_count=int(summary.get("document_count", 0)),
        created_at=str(summary.get("created_at", "")),
    )


def _raise_kb_error(exc: Exception) -> NoReturn:
    detail = str(exc)
    if isinstance(exc, LookupError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        ) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
                if "already exists" in detail
                else status.HTTP_400_BAD_REQUEST
            ),
            detail=detail,
        ) from exc
    raise exc


async def _import_uploaded_document(
    plugin: Any,
    collection_name: str,
    file: UploadFile,
) -> KbImportResponse:
    filename = normalize_document_filename(file.filename or "untitled")
    content_bytes = await file.read(_MAX_DOCUMENT_BYTES + 1)
    if len(content_bytes) > _MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Document exceeds the 25 MiB upload limit.",
        )
    try:
        converted = await to_thread.run_sync(
            convert_document_bytes,
            content_bytes,
            filename,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)

    source_id = filename.rsplit(".", 1)[0] if "." in filename else filename
    try:
        count = await plugin.import_content(
            collection_name,
            source_id=source_id,
            content=converted.content,
            content_type=converted.content_type,
            extra_metadata={
                "filename": filename,
                "original_content_type": file.content_type or "",
                "imported_at": datetime.now(UTC).isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)

    logger.info(
        "kb.api.import_file", collection=collection_name, file=filename, chunks=count
    )
    return KbImportResponse(
        collection=collection_name,
        source=filename,
        chunks=count,
    )


# ── Endpoints ─────────────────────────────────────────


@router.get("/api/kb/collections", response_model=KbCollectionListResponse)
async def list_collections(app=Depends(get_application)) -> KbCollectionListResponse:
    plugin = _require_kb_plugin(app)
    try:
        summaries = await plugin.list_collection_summaries()
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)
    return KbCollectionListResponse(
        collections=[_summary_from_mapping(summary) for summary in summaries]
    )


@router.get(
    "/api/kb/collections/{collection_name}",
    response_model=KbCollectionSummary,
)
async def get_collection(
    collection_name: str,
    app=Depends(get_application),
) -> KbCollectionSummary:
    plugin = _require_kb_plugin(app)
    try:
        summary = await plugin.get_collection_summary(collection_name)
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)
    return _summary_from_mapping(summary)


@router.post(
    "/api/kb/collections/{collection_name}/import-text",
    response_model=KbImportResponse,
)
async def import_text(
    collection_name: str,
    source: str = Form(...),
    content: str = Form(...),
    content_type: str = Form("text"),
    app=Depends(get_application),
) -> KbImportResponse:
    """Import text content into a collection.

    The content is chunked and stored for full-text search.
    """
    plugin = _require_kb_plugin(app)
    try:
        count = await plugin.import_content(
            collection_name,
            source_id=source,
            content=content,
            content_type=content_type,
            extra_metadata={"imported_at": datetime.now(UTC).isoformat()},
        )
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)

    logger.info(
        "kb.api.import_text", collection=collection_name, source=source, chunks=count
    )
    return KbImportResponse(
        collection=collection_name,
        source=source,
        chunks=count,
    )


@router.post(
    "/api/kb/collections/{collection_name}/import-file",
    response_model=KbImportResponse,
)
async def import_file(
    collection_name: str,
    file: UploadFile = File(...),
    app=Depends(get_application),
) -> KbImportResponse:
    """Upload and import a file into a collection."""
    plugin = _require_kb_plugin(app)
    return await _import_uploaded_document(plugin, collection_name, file)


@router.post(
    "/api/kb/collections/{collection_name}/import-files",
    response_model=KbBatchImportResponse,
)
async def import_files(
    collection_name: str,
    files: list[UploadFile] = File(...),
    app=Depends(get_application),
) -> KbBatchImportResponse:
    """Upload multiple documents and return one result per file."""
    plugin = _require_kb_plugin(app)
    if len(files) > _MAX_DOCUMENT_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {_MAX_DOCUMENT_FILES} documents can be imported at once.",
        )

    results: list[KbBatchImportItem] = []
    total_chunks = 0
    for file in files:
        source = normalize_document_filename(file.filename or "untitled")
        try:
            imported = await _import_uploaded_document(
                plugin,
                collection_name,
                file,
            )
        except HTTPException as exc:
            results.append(
                KbBatchImportItem(
                    source=source,
                    status="failed",
                    error=str(exc.detail),
                )
            )
            continue
        total_chunks += imported.chunks
        results.append(
            KbBatchImportItem(
                source=imported.source,
                status="imported",
                chunks=imported.chunks,
            )
        )

    imported_files = sum(item.status == "imported" for item in results)
    return KbBatchImportResponse(
        collection=collection_name,
        imported_files=imported_files,
        failed_files=len(results) - imported_files,
        chunks=total_chunks,
        results=results,
    )


@router.post(
    "/api/kb/collections/{collection_name}/search",
    response_model=KbSearchResponse,
)
async def search_collection(
    collection_name: str,
    query: str = Form(...),
    limit: int = Form(5),
    app=Depends(get_application),
) -> KbSearchResponse:
    plugin = _require_kb_plugin(app)
    try:
        results = await plugin.search_documents(collection_name, query, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)

    return KbSearchResponse(
        results=[
            KbDocumentResponse(
                doc_id=r.doc_id,
                title=r.title,
                content=r.content,
                score=r.score,
                path=getattr(r, "path", ""),
                source_id=getattr(r, "source_id", ""),
                node_type=getattr(r, "node_type", "passage"),
                metadata=r.metadata,
            )
            for r in results
        ]
    )


@router.delete(
    "/api/kb/collections/{collection_name}",
    response_model=KbActionResponse,
)
async def delete_collection(
    collection_name: str,
    app=Depends(get_application),
) -> KbActionResponse:
    plugin = _require_kb_plugin(app)
    try:
        await plugin.delete_collection(collection_name)
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)

    logger.info("kb.api.delete_collection", collection=collection_name)
    return KbActionResponse(
        status="ok", detail=f"Deleted collection '{collection_name}'"
    )


@router.post(
    "/api/kb/collections/{collection_name}/create",
    response_model=KbActionResponse,
)
async def create_collection(
    collection_name: str,
    app=Depends(get_application),
) -> KbActionResponse:
    """Create an empty collection."""
    try:
        plugin = _require_kb_plugin(app)
        await plugin.create_collection(collection_name)
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)

    logger.info("kb.api.create_collection", collection=collection_name)
    return KbActionResponse(
        status="ok", detail=f"Created collection '{collection_name}'"
    )


@router.get(
    "/api/kb/collections/{collection_name}/documents",
    response_model=KbDocumentListResponse,
)
async def list_documents(
    collection_name: str,
    limit: int = 50,
    offset: int = 0,
    app=Depends(get_application),
) -> KbDocumentListResponse:
    """List documents in a collection with pagination."""
    plugin = _require_kb_plugin(app)
    try:
        docs, total = await plugin.list_documents(
            collection_name,
            limit=min(limit, 500),
            offset=max(0, offset),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)

    return KbDocumentListResponse(
        documents=[
            KbDocumentResponse(
                doc_id=d.doc_id,
                title=d.title,
                content=d.content,
                score=0.0,
                path=getattr(d, "path", ""),
                source_id=getattr(d, "source_id", ""),
                node_type=getattr(d, "node_type", "passage"),
                metadata=d.metadata,
            )
            for d in docs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/api/kb/collections/{collection_name}/status",
    response_model=KbCollectionStatusResponse,
)
async def get_collection_status(
    collection_name: str,
    app=Depends(get_application),
) -> KbCollectionStatusResponse:
    """Get collection status including embedding progress."""
    plugin = _require_kb_plugin(app)
    try:
        summary = await plugin.get_collection_summary(collection_name)
    except Exception as exc:  # noqa: BLE001
        _raise_kb_error(exc)

    return KbCollectionStatusResponse(
        name=str(summary.get("name", collection_name)),
        document_count=int(summary.get("document_count", 0)),
        embedding_status=str(summary.get("embedding_status", "idle")),
    )
