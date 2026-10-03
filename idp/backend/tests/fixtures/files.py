"""Deterministic sample documents generated in code (no binary fixtures in git)."""

from __future__ import annotations

import io

from PIL import Image


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str | None], *, width: int = 612, height: int = 792) -> bytes:
    """Build a valid PDF. A string page carries a text layer; None is image-only
    (vector drawing, no text), like a scanned page without OCR."""
    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, text in enumerate(pages):
        content_id = page_ids[index] + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        if text is None:
            stream = b"0.5 g 50 50 500 700 re f"
        else:
            lines = [
                f"BT /F1 12 Tf 72 {720 - 16 * n} Td ({_escape(line)}) Tj ET"
                for n, line in enumerate(text.splitlines() or [""])
            ]
            stream = "\n".join(lines).encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


INVOICE_TEXT = (
    "ACME Ltd. Invoice INV-2026-00123\nTotal due: 1,250.00 EUR\nIBAN DE89370400440532013000"
)


def native_pdf(pages: int = 1) -> bytes:
    return make_pdf([f"{INVOICE_TEXT}\nPage {n + 1}" for n in range(pages)])


def scanned_pdf(pages: int = 1) -> bytes:
    return make_pdf([None] * pages)


def corrupted_pdf() -> bytes:
    return b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 99 0 R >>\nthis is not a pdf body"


def png(width: int = 300, height: int = 200) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def multipage_tiff(frames: int = 3) -> bytes:
    buffer = io.BytesIO()
    images = [Image.new("L", (400, 500), 255) for _ in range(frames)]
    images[0].save(buffer, format="TIFF", save_all=True, append_images=images[1:])
    return buffer.getvalue()
