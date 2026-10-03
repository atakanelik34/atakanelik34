"""Document intake rules: content-type detection, allow-list, filename hygiene.

Pure functions. The declared MIME type and filename from the client are
advisory only; the detected type (from magic bytes) decides what is accepted.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

# Bytes needed from the start of a file to detect its type.
SNIFF_BYTES = 2048

PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"
TIFF = "image/tiff"

ACCEPTED_MIME_TYPES: frozenset[str] = frozenset({PDF, PNG, JPEG, TIFF})

MAX_FILENAME_LENGTH = 255
DEFAULT_FILENAME = "document"
_CONTROL_OR_SEPARATOR = re.compile(r"[\x00-\x1f\x7f/\\]")


class DocumentSource(StrEnum):
    """Ingestion channel. Every channel adapter funnels into the same service."""

    WEB_UPLOAD = "web_upload"
    API = "api"
    EMAIL = "email"
    SFTP = "sftp"
    FOLDER = "folder"
    OBJECT_STORAGE = "object_storage"


class ScanStatus(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    # No scanner configured: recorded honestly, never reported as clean.
    NOT_SCANNED = "not_scanned"


def detect_mime_type(head: bytes) -> str | None:
    """Detect a supported type from leading bytes; None when unrecognised."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return TIFF
    # The PDF spec tolerates leading junk before the header (within 1 KiB).
    if b"%PDF-" in head[:1024]:
        return PDF
    return None


def sanitize_filename(raw: str | None) -> str:
    """Display-only filename: no paths, control characters or unbounded length.

    The result is never used to build a filesystem path or storage key.
    """
    if not raw:
        return DEFAULT_FILENAME
    name = unicodedata.normalize("NFC", raw)
    # Keep only the last path component from either separator style.
    name = re.split(r"[/\\]", name)[-1]
    name = _CONTROL_OR_SEPARATOR.sub("", name).strip().lstrip(".")
    if not name:
        return DEFAULT_FILENAME
    if len(name) > MAX_FILENAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            name = stem[: MAX_FILENAME_LENGTH - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_FILENAME_LENGTH]
    return name
