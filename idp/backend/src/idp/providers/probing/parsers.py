"""Parsers executed inside an isolated child process.

They return plain dicts (always picklable) instead of raising, so a parser
failure crosses the process boundary as data. Never import application code
here: the child should load as little as possible.
"""

from __future__ import annotations

import contextlib
import resource
from typing import Any

# A page whose extracted text has fewer non-whitespace characters than this is
# treated as image-only (scanned pages often carry a few stray glyphs).
MIN_TEXT_CHARS = 16
MAX_IMAGE_PIXELS = 178_956_970  # Pillow's default bomb threshold, made explicit


def init_child(memory_limit_mb: int) -> None:
    """Process-pool initializer: cap address space so a hostile file cannot exhaust RAM."""
    limit = memory_limit_mb * 1024 * 1024
    # Not supported on every platform (e.g. macOS); best effort there.
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def probe_pdf(path: str, max_pages: int) -> dict[str, Any]:
    import pypdfium2 as pdfium  # noqa: PLC0415 — child-process import

    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        message = str(exc).lower()
        if "password" in message:
            return {"ok": False, "error": "encrypted", "message": "PDF is password protected"}
        return {"ok": False, "error": "corrupted", "message": "PDF could not be parsed"}
    try:
        count = len(pdf)
        if count == 0:
            return {"ok": False, "error": "empty", "message": "PDF has no pages"}
        if count > max_pages:
            return {
                "ok": False,
                "error": "too_many_pages",
                "message": f"PDF has {count} pages; the limit is {max_pages}",
            }
        pages = []
        for index in range(count):
            page = pdf[index]
            try:
                width, height = page.get_size()
                textpage = page.get_textpage()
                try:
                    chars = sum(1 for c in textpage.get_text_range() if not c.isspace())
                finally:
                    textpage.close()
                pages.append(
                    {
                        "page_number": index + 1,
                        "width": float(width),
                        "height": float(height),
                        "unit": "pt",
                        "rotation": int(page.get_rotation()),
                        "has_text_layer": chars >= MIN_TEXT_CHARS,
                        "char_count": chars,
                    }
                )
            finally:
                page.close()
        return {"ok": True, "pages": pages}
    except pdfium.PdfiumError:
        return {"ok": False, "error": "corrupted", "message": "PDF page could not be parsed"}
    finally:
        pdf.close()


def probe_image(path: str, max_pages: int) -> dict[str, Any]:
    from PIL import Image, ImageSequence, UnidentifiedImageError  # noqa: PLC0415

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(path) as image:
            pages = []
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                if index >= max_pages:
                    return {
                        "ok": False,
                        "error": "too_many_pages",
                        "message": f"Image has more than {max_pages} frames",
                    }
                width, height = frame.size
                pages.append(
                    {
                        "page_number": index + 1,
                        "width": float(width),
                        "height": float(height),
                        "unit": "px",
                        "rotation": 0,
                        "has_text_layer": False,
                        "char_count": 0,
                    }
                )
            # Force a full decode of the first frame so truncated files fail here.
            image.seek(0)
            image.load()
    except Image.DecompressionBombError:
        return {"ok": False, "error": "too_large", "message": "Image dimensions are too large"}
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return {"ok": False, "error": "corrupted", "message": "Image could not be decoded"}
    return {"ok": True, "pages": pages}
