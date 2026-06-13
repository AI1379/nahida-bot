"""Convert uploaded knowledge-base documents into ingestible text."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

PLAIN_TEXT_EXTENSIONS = frozenset({".text", ".txt"})
MARKDOWN_EXTENSIONS = frozenset({".markdown", ".md"})
MARKITDOWN_EXTENSIONS = frozenset(
    {
        ".csv",
        ".docx",
        ".epub",
        ".htm",
        ".html",
        ".ipynb",
        ".json",
        ".msg",
        ".pdf",
        ".pptx",
        ".xls",
        ".xlsx",
        ".xml",
    }
)
SUPPORTED_DOCUMENT_EXTENSIONS = (
    PLAIN_TEXT_EXTENSIONS | MARKDOWN_EXTENSIONS | MARKITDOWN_EXTENSIONS
)


class DocumentImportDependencyError(RuntimeError):
    """Raised when optional rich-document conversion dependencies are missing."""


class DocumentConversionError(ValueError):
    """Raised when an uploaded document cannot be converted."""


@dataclass(frozen=True, slots=True)
class ConvertedDocument:
    """Normalized document content ready for the KB ingestion pipeline."""

    content: str
    content_type: Literal["markdown", "text"]


def convert_document_bytes(data: bytes, filename: str) -> ConvertedDocument:
    """Convert an uploaded document to text or Markdown.

    Plain text and Markdown files do not require optional dependencies. Other
    supported formats are converted through MarkItDown using an in-memory
    stream, so uploaded filenames are never treated as local paths or URLs.
    """
    safe_filename = normalize_document_filename(filename)
    extension = Path(safe_filename).suffix.lower()

    if extension == ".doc":
        raise DocumentConversionError(
            "Legacy .doc files are not supported. Save the document as .docx first."
        )
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_DOCUMENT_EXTENSIONS))
        raise DocumentConversionError(
            f"Unsupported document format '{extension or '(none)'}'. "
            f"Supported extensions: {supported}."
        )

    if extension in PLAIN_TEXT_EXTENSIONS:
        return ConvertedDocument(
            content=_decode_utf8(data, safe_filename),
            content_type="text",
        )
    if extension in MARKDOWN_EXTENSIONS:
        return ConvertedDocument(
            content=_decode_utf8(data, safe_filename),
            content_type="markdown",
        )

    return ConvertedDocument(
        content=_convert_with_markitdown(data, safe_filename, extension),
        content_type="markdown",
    )


def normalize_document_filename(filename: str) -> str:
    """Discard client-supplied path components from an upload filename."""
    normalized = filename.replace("\\", "/")
    return Path(normalized).name or "untitled"


def _decode_utf8(data: bytes, filename: str) -> str:
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentConversionError(
            f"Text file '{filename}' must be UTF-8 encoded."
        ) from exc
    if not content.strip():
        raise DocumentConversionError(f"Document '{filename}' contains no text.")
    return content


def _load_markitdown() -> tuple[Any, Any]:
    try:
        module = importlib.import_module("markitdown")
    except ImportError as exc:
        raise DocumentImportDependencyError(
            "Rich document import is not installed. "
            "Run `uv sync --extra document-import` and restart Nahida Bot."
        ) from exc
    return module.MarkItDown, module.StreamInfo


def _convert_with_markitdown(data: bytes, filename: str, extension: str) -> str:
    MarkItDown, StreamInfo = _load_markitdown()
    try:
        converter = MarkItDown(enable_plugins=False)
        result = converter.convert_stream(
            BytesIO(data),
            stream_info=StreamInfo(
                extension=extension,
                filename=filename,
            ),
        )
    except Exception as exc:
        raise DocumentConversionError(
            f"Failed to convert '{filename}'. The file may be corrupt or "
            "unsupported; ensure `uv sync --extra document-import` is installed."
        ) from exc

    content = str(result.text_content)
    if not content.strip():
        raise DocumentConversionError(
            f"Document '{filename}' produced no extractable text."
        )
    return content
