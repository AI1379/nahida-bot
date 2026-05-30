"""OneBot segment dataclasses for v11 and v12 message formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Typed segment dataclasses ─────────────────────────────


@dataclass(slots=True)
class TextSegment:
    """Plain text segment."""

    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "text", "data": {"text": self.text}}


@dataclass(slots=True)
class AtSegment:
    """@mention segment (v11)."""

    qq: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "at", "data": {"qq": self.qq}}


@dataclass(slots=True)
class MentionSegment:
    """@mention segment (v12)."""

    user_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "mention", "data": {"user_id": self.user_id}}


@dataclass(slots=True)
class ReplySegment:
    """Reply/quote segment."""

    message_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "reply", "data": {"id": self.message_id}}


@dataclass(slots=True)
class ImageSegment:
    """Image segment.

    The ``file`` field accepts:
    - A file_id string from a previously received image
    - ``http(s)://`` URL
    - ``base64://<data>`` for inline base64-encoded content
    - ``file:///path`` for local files on the OneBot host
    """

    file: str = ""
    url: str = ""
    sub_type: str = ""  # "flash", "show", or empty for normal
    cache: bool = True
    proxy: bool = True
    timeout: int = 0

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"file": self.file}
        if self.url:
            data["url"] = self.url
        if self.sub_type:
            data["type"] = self.sub_type
        if not self.cache:
            data["cache"] = 0
        if not self.proxy:
            data["proxy"] = 0
        if self.timeout:
            data["timeout"] = self.timeout
        return {"type": "image", "data": data}


@dataclass(slots=True)
class RecordSegment:
    """Voice/audio segment."""

    file: str = ""
    url: str = ""
    duration: int = 0
    cache: bool = True
    proxy: bool = True
    timeout: int = 0

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"file": self.file}
        if self.url:
            data["url"] = self.url
        if self.duration:
            data["duration"] = self.duration
        if not self.cache:
            data["cache"] = 0
        if not self.proxy:
            data["proxy"] = 0
        if self.timeout:
            data["timeout"] = self.timeout
        return {"type": "record", "data": data}


@dataclass(slots=True)
class VideoSegment:
    """Video segment."""

    file: str = ""
    url: str = ""
    duration: int = 0
    width: int = 0
    height: int = 0
    cache: bool = True
    proxy: bool = True
    timeout: int = 0

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"file": self.file}
        if self.url:
            data["url"] = self.url
        if self.duration:
            data["duration"] = self.duration
        if self.width:
            data["width"] = self.width
        if self.height:
            data["height"] = self.height
        if not self.cache:
            data["cache"] = 0
        if not self.proxy:
            data["proxy"] = 0
        if self.timeout:
            data["timeout"] = self.timeout
        return {"type": "video", "data": data}


@dataclass(slots=True)
class FileSegment:
    """File segment (v11 extension, widely supported)."""

    file: str = ""
    name: str = ""
    file_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"file": self.file}
        if self.name:
            data["name"] = self.name
        if self.file_size:
            data["file_size"] = self.file_size
        return {"type": "file", "data": data}


@dataclass(slots=True)
class FaceSegment:
    """QQ emoji/face segment."""

    face_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "face", "data": {"id": self.face_id}}


@dataclass(slots=True)
class ForwardSegment:
    """Combined forward / merged-forward segment."""

    forward_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "forward", "data": {"id": self.forward_id}}


@dataclass(slots=True)
class LocationSegment:
    """Location segment (v11)."""

    lat: float = 0.0
    lon: float = 0.0
    title: str = ""
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"lat": self.lat, "lon": self.lon}
        if self.title:
            data["title"] = self.title
        if self.content:
            data["content"] = self.content
        return {"type": "location", "data": data}


@dataclass(slots=True)
class ShareSegment:
    """Share/link segment (v11)."""

    url: str = ""
    title: str = ""
    content: str = ""
    image: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"url": self.url}
        if self.title:
            data["title"] = self.title
        if self.content:
            data["content"] = self.content
        if self.image:
            data["image"] = self.image
        return {"type": "share", "data": data}


# ── Generic (fallback) segment ───────────────────────────


@dataclass(slots=True)
class OneBotSegment:
    """Fallback generic segment with raw type and data dict."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OneBotSegment:
        seg_type = str(raw.get("type", ""))
        data = raw.get("data")
        if not isinstance(data, dict):
            data = {}
        return cls(type=seg_type, data=data)


# ── Parse & render ───────────────────────────────────────


def parse_segments(raw: object) -> list[OneBotSegment]:
    """Parse a OneBot message array into a list of segments."""
    if not isinstance(raw, list):
        return []
    return [OneBotSegment.from_dict(item) for item in raw if isinstance(item, dict)]


def segments_to_array(segments: list[Any]) -> list[dict[str, Any]]:
    """Convert typed segments or generic segments to a JSON-serializable array."""
    result: list[dict[str, Any]] = []
    for seg in segments:
        if hasattr(seg, "to_dict"):
            result.append(seg.to_dict())
        elif hasattr(seg, "type") and hasattr(seg, "data"):
            result.append({"type": getattr(seg, "type"), "data": getattr(seg, "data")})
        elif isinstance(seg, dict):
            result.append(seg)
    return result


def has_segment_type(segments: list[Any], seg_type: str) -> bool:
    """Check whether any segment matches the given type."""
    for seg in segments:
        t = _extract_type(seg)
        if t == seg_type:
            return True
    return False


def find_segments(segments: list[Any], seg_type: str) -> list[Any]:
    """Return all segments of a given type."""
    return [seg for seg in segments if _extract_type(seg) == seg_type]


def render_segments_plain_text(segments: list[Any]) -> str:
    """Render segments to plain text for LLM consumption."""
    parts: list[str] = []
    for seg in segments:
        seg_type = _extract_type(seg)
        data = _extract_data(seg)

        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        elif seg_type == "image":
            url = data.get("url") or data.get("file", "")
            sub = data.get("sub_type") or data.get("type", "")
            label = (
                "[Image"
                + (f" ({sub})" if sub else "")
                + (f": {url}" if url else "")
                + "]"
            )
            parts.append(label)
        elif seg_type in ("record", "voice"):
            url = data.get("url") or data.get("file", "")
            dur = data.get("duration", "")
            label = (
                "[Voice"
                + (f" {dur}s" if dur else "")
                + (f": {url}" if url else "")
                + "]"
            )
            parts.append(label)
        elif seg_type == "video":
            url = data.get("url") or data.get("file", "")
            dur = data.get("duration", "")
            label = (
                "[Video"
                + (f" {dur}s" if dur else "")
                + (f": {url}" if url else "")
                + "]"
            )
            parts.append(label)
        elif seg_type == "file":
            name = data.get("name") or data.get("file", "")
            size = data.get("file_size", 0)
            label = (
                "[File"
                + (f": {name}" if name else "")
                + (f" {size}B" if size else "")
                + "]"
            )
            parts.append(label)
        elif seg_type == "at":
            qq = str(data.get("qq", ""))
            parts.append(f"@[qq={qq}]")
        elif seg_type == "mention":
            user_id = str(data.get("user_id", ""))
            parts.append(f"@[user_id={user_id}]")
        elif seg_type == "reply":
            mid = str(data.get("id", data.get("message_id", "")))
            parts.append(f"[Reply to {mid}]")
        elif seg_type == "face":
            face_id = str(data.get("id", ""))
            parts.append(f"[Face: {face_id}]")
        elif seg_type == "forward":
            forward_id = str(data.get("id", ""))
            parts.append(f"[Forward: {forward_id}]")
        elif seg_type == "location":
            lat = data.get("lat", "")
            lon = data.get("lon", "")
            title = data.get("title", "")
            parts.append(f"[Location: {title or f'{lat},{lon}'}]")
        elif seg_type == "share":
            url = data.get("url", "")
            title = data.get("title", "")
            parts.append(f"[Share: {title} {url}]")
        elif seg_type == "json":
            parts.append(f"[JSON: {data.get('data', '')}]")
        elif seg_type == "xml":
            parts.append(f"[XML: {data.get('data', '')}]")
        else:
            parts.append(f"[{seg_type}]")
    return "".join(parts)


# ── Internal helpers ─────────────────────────────────────


def _extract_type(seg: Any) -> str:
    """Extract segment type from any segment representation."""
    if hasattr(seg, "type") and not callable(getattr(seg, "type")):
        return str(getattr(seg, "type"))
    if isinstance(seg, dict):
        return str(seg.get("type", ""))
    return ""


def _extract_data(seg: Any) -> dict[str, Any]:
    """Extract segment data dict from any segment representation."""
    if hasattr(seg, "data") and not callable(getattr(seg, "data")):
        data = getattr(seg, "data")
        if isinstance(data, dict):
            return data
    if isinstance(seg, dict):
        data = seg.get("data")
        if isinstance(data, dict):
            return data
    return {}
