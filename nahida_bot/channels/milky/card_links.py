"""Extract shareable web links from QQ card messages.

QQ clients deliver link shares (Bilibili videos, Xiaohongshu notes, music,
...) as rich "card" messages. Milky surfaces them as ``light_app`` segments
(JSON payload) or legacy ``xml`` segments, and the actual web link hides
behind platform-specific field names inside the payload. Without extraction
the agent only ever sees an opaque ``[LightApp: app_name=...]`` placeholder,
so it cannot open or summarize what was shared.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
from typing import Any
from xml.etree import ElementTree

# JSON card field names that carry the jump target of a share card.  Rank 0
# is the canonical share link ("jump_url", used by both structmsg and
# miniapp schemas); rank 1 names appear in less common schemas.  Cover /
# preview images deliberately have no rank here — they are assets, not the
# web page being shared.
_URL_FIELD_RANKS = {
    "jump_url": 0,
    "url": 1,
    "qqdocurl": 1,
    "share_url": 1,
    "web_url": 1,
}
_TITLE_FIELD_RANKS = {"title": 0, "prompt": 1, "desc": 2, "summary": 2}

# Key/tag fragments marking asset (image) fields: their URLs are covers and
# previews, never the page being shared.
_ASSET_KEY_PARTS = (
    "cover",
    "preview",
    "pic",
    "image",
    "icon",
    "thumb",
    "avatar",
    "banner",
)
_ASSET_TAGS = {
    "picture",
    "image",
    "thumb",
    "thumbnail",
    "icon",
    "avatar",
    "cover",
    "banner",
}

_URL_RE = re.compile(r"https?://[^\s\"'<>\\`]+")
_TRAILING_NOISE = "\"'),;]}>"
_DTD_RE = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
_SHARE_PREFIX_RE = re.compile(r"^\[分享\]\s*")
_MAX_URLS = 5
_MAX_TITLE_CHARS = 80


@dataclass(frozen=True, slots=True)
class CardShareInfo:
    """Web links and title extracted from a QQ share card payload."""

    title: str = ""
    urls: tuple[str, ...] = ()


def extract_light_app_info(json_payload: str) -> CardShareInfo:
    """Extract share links from a ``light_app`` JSON card payload.

    Structured extraction wins when the payload parses as JSON: ranked URL
    fields first, then http(s) values from any non-asset field (a pure
    miniapp card has no web link at all — only a page_path — and must not
    have its cover image surfaced as the share link). Only unparsable
    payloads fall back to a regex scan of the raw text.
    """
    payload = _load_json_payload(json_payload)
    if isinstance(payload, dict):
        urls, title = _collect_json_fields(payload)
        return CardShareInfo(title=title, urls=urls)
    return CardShareInfo(title="", urls=_regex_urls(json_payload))


def extract_xml_card_info(xml_payload: str) -> CardShareInfo:
    """Extract share links from a legacy XML card payload."""
    element = _parse_xml(xml_payload)
    if element is None:
        return CardShareInfo(title="", urls=_regex_urls(xml_payload))
    urls = _dedupe(
        normalized
        for node in element.iter()
        if not _is_asset_tag(node.tag)
        for normalized in [_normalize_url(node.get("url") or "")]
        if normalized
    )
    title = _clean_title(element.get("brief") or _first_element_text(element, "title"))
    return CardShareInfo(title=title, urls=urls)


def _load_json_payload(payload: str) -> object:
    """Load a JSON card payload, tolerating one level of double encoding."""
    try:
        loaded: object = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(loaded, str) and loaded.lstrip()[:1] in "{[":
        try:
            return json.loads(loaded)
        except (json.JSONDecodeError, ValueError):
            return None
    return loaded


def _collect_json_fields(payload: dict[str, Any]) -> tuple[tuple[str, ...], str]:
    """Walk a JSON card collecting ranked URL and title candidates."""
    url_candidates: list[tuple[int, int, str]] = []
    loose_candidates: list[tuple[int, str]] = []
    title_candidates: list[tuple[int, int, str]] = []
    counter = [0]

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                name = str(key).strip().lower()
                if isinstance(value, str):
                    if name in _URL_FIELD_RANKS:
                        normalized = _normalize_url(value)
                        if normalized:
                            url_candidates.append(
                                (_URL_FIELD_RANKS[name], counter[0], normalized)
                            )
                            counter[0] += 1
                    elif name in _TITLE_FIELD_RANKS and value.strip():
                        title_candidates.append(
                            (
                                _TITLE_FIELD_RANKS[name],
                                counter[0],
                                unescape(value).strip(),
                            )
                        )
                        counter[0] += 1
                    elif not _is_asset_key(name):
                        normalized = _normalize_url(value)
                        if normalized:
                            loose_candidates.append((counter[0], normalized))
                            counter[0] += 1
                    continue
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    urls = _dedupe(url for _, _, url in sorted(url_candidates))
    if not urls:
        # No ranked share-link field: accept an http(s) value from any
        # non-asset field so uncommon card schemas still surface a link.
        urls = _dedupe(url for _, url in sorted(loose_candidates))
    title = next(
        (text for _, _, text in sorted(title_candidates)),
        "",
    )
    return urls, _clean_title(title)


def _parse_xml(payload: str) -> ElementTree.Element | None:
    """Parse an XML card, refusing DTD/entity payloads stdlib cannot harden."""
    if _DTD_RE.search(payload):
        return None
    try:
        return ElementTree.fromstring(payload)
    except (ElementTree.ParseError, ValueError):
        return None


def _first_element_text(root: ElementTree.Element, tag: str) -> str:
    for node in root.iter(tag):
        if node.text and node.text.strip():
            return node.text.strip()
    return ""


def _is_asset_key(name: str) -> bool:
    return any(part in name for part in _ASSET_KEY_PARTS)


def _is_asset_tag(tag: object) -> bool:
    return isinstance(tag, str) and tag.lower() in _ASSET_TAGS


def _regex_urls(text: str) -> tuple[str, ...]:
    """Scan raw payload text for http(s) URLs as a last-resort fallback."""
    # JSON payloads sometimes escape slashes; repair before matching.
    searchable = text.replace("\\/", "/")
    return _dedupe(
        normalized
        for match in _URL_RE.findall(searchable)
        for normalized in [_normalize_url(match)]
        if normalized
    )


def _normalize_url(raw: str) -> str:
    value = unescape(raw).strip().rstrip(_TRAILING_NOISE)
    if not value.startswith(("http://", "https://")):
        return ""
    return value


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
        if len(seen) == _MAX_URLS:
            break
    return tuple(seen)


def _clean_title(title: str) -> str:
    cleaned = _SHARE_PREFIX_RE.sub("", unescape(title).strip())
    if len(cleaned) > _MAX_TITLE_CHARS:
        cleaned = cleaned[: _MAX_TITLE_CHARS - 1] + "…"
    return cleaned
