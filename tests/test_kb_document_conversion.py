"""Tests for optional knowledge-base document conversion."""

from types import SimpleNamespace
from typing import Any

import pytest

import nahida_bot.plugins.knowledge_base.document_conversion as conversion
from nahida_bot.plugins.knowledge_base.document_conversion import (
    DocumentConversionError,
    convert_document_bytes,
)


def test_plain_text_import_does_not_require_markitdown() -> None:
    converted = convert_document_bytes(b"\xef\xbb\xbfhello", "notes.txt")

    assert converted.content == "hello"
    assert converted.content_type == "text"


def test_markdown_import_preserves_markdown_type() -> None:
    converted = convert_document_bytes(b"# Guide\n\nBody", "guide.md")

    assert converted.content == "# Guide\n\nBody"
    assert converted.content_type == "markdown"


def test_rich_document_uses_markitdown_stream(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeStreamInfo:
        def __init__(self, **kwargs: Any) -> None:
            captured["stream_info"] = kwargs

    class FakeMarkItDown:
        def __init__(self, *, enable_plugins: bool) -> None:
            captured["enable_plugins"] = enable_plugins

        def convert_stream(self, stream, *, stream_info):
            captured["data"] = stream.read()
            captured["converted_stream_info"] = stream_info
            return SimpleNamespace(text_content="# Converted\n\nDocument body")

    monkeypatch.setattr(
        conversion,
        "_load_markitdown",
        lambda: (FakeMarkItDown, FakeStreamInfo),
    )

    converted = convert_document_bytes(b"%PDF-test", r"C:\fake\report.pdf")

    assert converted.content == "# Converted\n\nDocument body"
    assert converted.content_type == "markdown"
    assert captured["enable_plugins"] is False
    assert captured["data"] == b"%PDF-test"
    assert captured["stream_info"] == {
        "extension": ".pdf",
        "filename": "report.pdf",
    }


def test_legacy_doc_has_actionable_error() -> None:
    with pytest.raises(DocumentConversionError, match="Save the document as .docx"):
        convert_document_bytes(b"legacy", "report.doc")


def test_empty_converted_document_is_rejected(monkeypatch) -> None:
    class FakeMarkItDown:
        def __init__(self, *, enable_plugins: bool) -> None:
            pass

        def convert_stream(self, stream, *, stream_info):
            return SimpleNamespace(text_content="  \n")

    monkeypatch.setattr(
        conversion,
        "_load_markitdown",
        lambda: (FakeMarkItDown, lambda **kwargs: kwargs),
    )

    with pytest.raises(DocumentConversionError, match="no extractable text"):
        convert_document_bytes(b"%PDF-test", "empty.pdf")
