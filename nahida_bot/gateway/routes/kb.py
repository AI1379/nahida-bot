"""Knowledge Base management API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel, Field

from nahida_bot.gateway.deps import get_application

logger = structlog.get_logger(__name__)

router = APIRouter()


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
    metadata: dict[str, Any] = Field(default_factory=dict)


class KbSearchResponse(BaseModel):
    results: list[KbDocumentResponse]


class KbImportResponse(BaseModel):
    collection: str
    source: str
    chunks: int


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


def _raise_kb_error(exc: Exception) -> None:
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

    filename = file.filename or "untitled"
    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be UTF-8 encoded text",
        )

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_type = "markdown" if ext in ("md", "markdown") else "text"
    source_id = filename.rsplit(".", 1)[0] if "." in filename else filename
    try:
        count = await plugin.import_content(
            collection_name,
            source_id=source_id,
            content=content,
            content_type=content_type,
            extra_metadata={
                "filename": filename,
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
