"""Native text extraction and page rendering, executed in an isolated child process.

Returns plain dicts (picklable). Imports only the pure geometry module from the
application, never services or infrastructure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from idp.domain.geometry import BBox, rotate_normalized, text_quality

MAX_VIEW_EDGE = 2000  # px, longest edge of viewer images


def _save_view(image: Any, path: Path) -> tuple[int, int]:
    image = image.convert("RGB")
    image.thumbnail((MAX_VIEW_EDGE, MAX_VIEW_EDGE))
    image.save(path, format="WEBP", quality=82, method=4)
    return image.size  # type: ignore[no-any-return]


def _pdf_words(page: Any, textpage: Any, rotation: int) -> list[dict[str, Any]]:
    left, bottom, right, top = page.get_cropbox()
    width, height = right - left, top - bottom
    count = textpage.count_chars()
    text = textpage.get_text_range(0, count)
    aligned = len(text) == count
    words: list[dict[str, Any]] = []
    chars: list[str] = []
    box: list[float] | None = None

    def flush() -> None:
        nonlocal box
        if chars and box is not None:
            norm = BBox.clamp(
                (box[0] - left) / width,
                (top - box[3]) / height,
                (box[2] - left) / width,
                (top - box[1]) / height,
            )
            words.append({"t": "".join(chars), "b": rotate_normalized(norm, rotation).as_list()})
        chars.clear()
        box = None

    for i in range(count):
        ch = text[i] if aligned else textpage.get_text_range(i, 1)
        if not ch or ch.isspace():
            flush()
            continue
        l_, b_, r_, t_ = textpage.get_charbox(i)
        if r_ <= l_ or t_ <= b_:  # generated/invisible character
            chars.append(ch)
            continue
        box = (
            [l_, b_, r_, t_]
            if box is None
            else [
                min(box[0], l_),
                min(box[1], b_),
                max(box[2], r_),
                max(box[3], t_),
            ]
        )
        chars.append(ch)
    flush()
    return words


def extract_pdf(  # noqa: PLR0917 — positional for ProcessPoolExecutor.submit
    path: str, workdir: str, render_dpi: int, ocr_dpi: int, min_quality: float, max_pages: int
) -> dict[str, Any]:
    import pypdfium2 as pdfium  # noqa: PLC0415

    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        reason = "encrypted" if "password" in str(exc).lower() else "corrupted"
        return {"ok": False, "error": reason, "message": f"PDF could not be opened ({reason})"}
    out = Path(workdir)
    pages: list[dict[str, Any]] = []
    try:
        if len(pdf) > max_pages:
            return {"ok": False, "error": "too_many_pages", "message": "Too many pages"}
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                rotation = int(page.get_rotation())
                width, height = page.get_size()
                textpage = page.get_textpage()
                try:
                    words = _pdf_words(page, textpage, rotation)
                finally:
                    textpage.close()
                quality = text_quality(" ".join(w["t"] for w in words))
                native_ok = len(words) > 0 and quality >= min_quality
                view_path = out / f"view-{index + 1}.webp"
                view_w, view_h = _save_view(page.render(scale=render_dpi / 72).to_pil(), view_path)
                ocr_path = None
                if not native_ok:
                    ocr_path = out / f"ocr-{index + 1}.png"
                    page.render(scale=ocr_dpi / 72, grayscale=True).to_pil().save(ocr_path)
                pages.append(
                    {
                        "page_number": index + 1,
                        "width": float(width),
                        "height": float(height),
                        "unit": "pt",
                        "rotation": rotation,
                        "native_words": words if native_ok else [],
                        "native_quality": quality,
                        "view_image": str(view_path),
                        "view_size": [view_w, view_h],
                        "ocr_image": str(ocr_path) if ocr_path else None,
                    }
                )
            finally:
                page.close()
    except pdfium.PdfiumError:
        return {"ok": False, "error": "corrupted", "message": "PDF page could not be parsed"}
    finally:
        pdf.close()
    return {"ok": True, "pages": pages}


def extract_image(path: str, workdir: str, max_pages: int) -> dict[str, Any]:
    from PIL import Image, ImageSequence, UnidentifiedImageError  # noqa: PLC0415

    from idp.providers.probing.parsers import MAX_IMAGE_PIXELS  # noqa: PLC0415

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    out = Path(workdir)
    pages: list[dict[str, Any]] = []
    try:
        with Image.open(path) as image:
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                if index >= max_pages:
                    return {"ok": False, "error": "too_many_pages", "message": "Too many frames"}
                rgb = frame.convert("RGB")
                ocr_path = out / f"ocr-{index + 1}.png"
                rgb.save(ocr_path)
                view_path = out / f"view-{index + 1}.webp"
                view_size = _save_view(rgb.copy(), view_path)
                pages.append(
                    {
                        "page_number": index + 1,
                        "width": float(rgb.width),
                        "height": float(rgb.height),
                        "unit": "px",
                        "rotation": 0,
                        "native_words": [],
                        "native_quality": 0.0,
                        "view_image": str(view_path),
                        "view_size": list(view_size),
                        "ocr_image": str(ocr_path),
                    }
                )
    except Image.DecompressionBombError:
        return {"ok": False, "error": "too_large", "message": "Image dimensions are too large"}
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return {"ok": False, "error": "corrupted", "message": "Image could not be decoded"}
    return {"ok": True, "pages": pages}
