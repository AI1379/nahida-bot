"""Workspace file management service.

Provides a safe, sandboxed file API for WebUI. Only allows
operations within configured workspace roots.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from nahida_bot.workspace.sandbox import WorkspaceSandbox

logger = structlog.get_logger(__name__)

# Allowed file extensions for the file management UI.
TEXT_EXTENSIONS: frozenset[str] = frozenset({".md", ".txt", ".yaml", ".yml", ".json"})
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"}
)
ALLOWED_EXTENSIONS: frozenset[str] = TEXT_EXTENSIONS | IMAGE_EXTENSIONS

IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}

# Max single text file size for read/write operations (1 MiB).
MAX_FILE_SIZE = 1 * 1024 * 1024

# Max single binary file size for preview/upload operations (10 MiB).
MAX_BINARY_FILE_SIZE = 10 * 1024 * 1024

# Soft-delete directory name.
_TRASH_DIR = ".trash"


@dataclass(slots=True)
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: str = ""


@dataclass(slots=True)
class FileContent:
    path: str
    content: str
    size: int
    mtime: str


@dataclass(slots=True)
class FileMetadata:
    path: str
    size: int
    mtime: str


@dataclass(slots=True)
class ImageFile:
    path: str
    file_path: Path
    size: int
    mtime: str
    media_type: str


def list_files(
    sandbox: WorkspaceSandbox,
    relative_path: str = ".",
) -> list[FileEntry]:
    """List files and directories under a relative path in the workspace."""
    target = sandbox.resolve_safe_path(relative_path)
    if not target.is_dir():
        raise ValueError(f"Not a directory: {relative_path}")

    entries: list[FileEntry] = []
    for child in sorted(target.iterdir()):
        # Skip hidden files and trash directory
        if child.name.startswith(".") or child.name == _TRASH_DIR:
            continue

        is_dir = child.is_dir()
        size = 0
        mtime = ""
        if not is_dir:
            stat = child.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()

        entries.append(
            FileEntry(
                name=child.name,
                path=str(child.relative_to(sandbox.root)),
                is_dir=is_dir,
                size=size,
                mtime=mtime,
            )
        )
    return entries


def read_file(
    sandbox: WorkspaceSandbox,
    relative_path: str,
) -> FileContent:
    """Read a file's content from the workspace."""
    _check_text_extension(relative_path)
    target = sandbox.resolve_safe_path(relative_path)

    if not target.is_file():
        raise FileNotFoundError(f"File not found: {relative_path}")

    stat = target.stat()
    if stat.st_size > MAX_FILE_SIZE:
        raise ValueError(f"File too large ({stat.st_size} bytes, max {MAX_FILE_SIZE})")

    content = target.read_text(encoding="utf-8")
    return FileContent(
        path=relative_path,
        content=content,
        size=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    )


def write_file(
    sandbox: WorkspaceSandbox,
    relative_path: str,
    content: str,
) -> FileContent:
    """Write content to a file in the workspace."""
    _check_text_extension(relative_path)
    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        raise ValueError(f"Content too large (max {MAX_FILE_SIZE} bytes)")

    sandbox.write_text(relative_path, content)
    logger.debug("file_service.write", path=relative_path)
    stat = sandbox.resolve_safe_path(relative_path).stat()
    return FileContent(
        path=relative_path,
        content=content,
        size=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    )


def create_file(
    sandbox: WorkspaceSandbox,
    relative_path: str,
    content: str = "",
) -> FileContent:
    """Create a new file. Fails if it already exists."""
    _check_text_extension(relative_path)
    target = sandbox.resolve_safe_path(relative_path)
    if target.exists():
        raise FileExistsError(f"File already exists: {relative_path}")
    return write_file(sandbox, relative_path, content)


def read_image_file(
    sandbox: WorkspaceSandbox,
    relative_path: str,
) -> ImageFile:
    """Resolve an image file for raw preview delivery."""
    _check_image_extension(relative_path)
    target = sandbox.resolve_safe_path(relative_path)

    if not target.is_file():
        raise FileNotFoundError(f"File not found: {relative_path}")

    stat = target.stat()
    if stat.st_size > MAX_BINARY_FILE_SIZE:
        raise ValueError(
            f"File too large ({stat.st_size} bytes, max {MAX_BINARY_FILE_SIZE})"
        )

    suffix = target.suffix.lower()
    return ImageFile(
        path=relative_path,
        file_path=target,
        size=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        media_type=IMAGE_MEDIA_TYPES[suffix],
    )


def upload_file(
    sandbox: WorkspaceSandbox,
    relative_path: str,
    data: bytes,
    *,
    overwrite: bool = False,
) -> FileMetadata:
    """Upload a binary or text file into the workspace."""
    _check_allowed_extension(relative_path)
    if len(data) > MAX_BINARY_FILE_SIZE:
        raise ValueError(f"File too large (max {MAX_BINARY_FILE_SIZE} bytes)")

    target = sandbox.resolve_safe_path(relative_path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {relative_path}")
    if target.exists() and target.is_dir():
        raise ValueError(f"Cannot overwrite directory: {relative_path}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    logger.debug("file_service.upload", path=relative_path, size=len(data))
    stat = target.stat()
    return FileMetadata(
        path=relative_path,
        size=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    )


def create_directory(
    sandbox: WorkspaceSandbox,
    relative_path: str,
) -> None:
    """Create a new directory."""
    target = sandbox.resolve_safe_path(relative_path)
    if target.exists():
        raise FileExistsError(f"Already exists: {relative_path}")
    target.mkdir(parents=True, exist_ok=False)


def rename_entry(
    sandbox: WorkspaceSandbox,
    old_path: str,
    new_name: str,
) -> str:
    """Rename a file or directory.

    Args:
        old_path: Current relative path.
        new_name: New basename (not a full path).

    Returns:
        New relative path.
    """
    old_target = sandbox.resolve_safe_path(old_path)
    if not old_target.exists():
        raise FileNotFoundError(f"Not found: {old_path}")

    if "/" in new_name or "\\" in new_name or not new_name.strip():
        raise ValueError("new_name must be a simple basename without path separators")

    # Enforce extension policy when renaming files (not directories)
    if old_target.is_file():
        _check_allowed_extension(new_name)

    new_target = old_target.parent / new_name
    # Verify the new path stays in sandbox
    sandbox.resolve_safe_path(str(new_target.relative_to(sandbox.root)))

    if new_target.exists():
        raise FileExistsError(f"Target already exists: {new_name}")

    old_target.rename(new_target)
    logger.debug("file_service.rename", old=old_path, new=new_name)
    return str(new_target.relative_to(sandbox.root))


def soft_delete(
    sandbox: WorkspaceSandbox,
    relative_path: str,
) -> str:
    """Move a file to the workspace .trash directory.

    Returns the trash path.
    """
    target = sandbox.resolve_safe_path(relative_path)
    if not target.exists():
        raise FileNotFoundError(f"Not found: {relative_path}")

    trash_dir = sandbox.root / _TRASH_DIR
    trash_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    trash_name = f"{target.name}.{timestamp}.deleted"
    trash_path = trash_dir / trash_name
    shutil.move(str(target), str(trash_path))
    logger.debug("file_service.soft_delete", path=relative_path, trash=trash_name)

    return str(trash_path.relative_to(sandbox.root))


def _check_text_extension(path: str) -> None:
    """Verify the file has an editable text extension."""
    _check_extension(path, TEXT_EXTENSIONS)


def _check_image_extension(path: str) -> None:
    """Verify the file has a previewable image extension."""
    _check_extension(path, IMAGE_EXTENSIONS)


def _check_allowed_extension(path: str) -> None:
    """Verify the file has an extension accepted by the file UI."""
    _check_extension(path, ALLOWED_EXTENSIONS)


def _check_extension(path: str, allowed: frozenset[str]) -> None:
    """Verify the file has one of the allowed extensions.

    Rejects empty extensions and extensions not in the allow-list.
    Directory paths (ending with /) are skipped.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if not suffix:
        # Allow directories (no suffix) but reject extensionless files
        if not path.endswith("/"):
            raise ValueError(
                f"Files must have an allowed extension. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )
        return
    if suffix not in allowed:
        raise ValueError(
            f"File extension '{suffix}' not allowed. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )
