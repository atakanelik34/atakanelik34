"""Deterministic sample documents generated in code (no binary fixtures in git)."""

from __future__ import annotations

import io

from PIL import Image


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(
    pages: list[str | None],
    *,
    width: int = 612,
    height: int = 792,
    font_size: int = 12,
    rotate: int = 0,
) -> bytes:
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
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Rotate {rotate} "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        if text is None:
            stream = b"0.5 g 50 50 500 700 re f"
        else:
            lines = [
                f"BT /F1 {font_size} Tf 72 {720 - int(font_size * 1.4) * n} Td ({_escape(line)}) Tj ET"
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


def text_image(text: str, *, dpi: int = 200, font_size: int = 24) -> bytes:
    """A PNG that *looks* like the text (rendered glyphs) but has no text layer."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(make_pdf([text], font_size=font_size))
    try:
        image = pdf[0].render(scale=dpi / 72).to_pil().convert("L")
    finally:
        pdf.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def image_pdf(text: str) -> bytes:
    """A 'scanned' PDF: one page whose only content is a JPEG of rendered text."""
    with Image.open(io.BytesIO(text_image(text))) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size
        jpeg = io.BytesIO()
        rgb.save(jpeg, format="JPEG", quality=90)
    data = jpeg.getvalue()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>",
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>"
        ).encode()
        + b"\nstream\n"
        + data
        + b"\nendstream",
    ]
    content = b"q 612 0 0 792 0 0 cm /Im0 Do Q"
    objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
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


MIXED_PACKET_PAGES: list[str | None] = [
    "ACME Ltd. INVOICE\nInvoice number INV-2026-00123\nInvoice date 03.10.2026\nPage 1 of 3",
    "Invoice INV-2026-00123 continued\nLine items and amount due\nPage 2 of 3",
    "Terms and conditions apply\nPage 3 of 3",
    "DELIVERY NOTE\nDelivery note number DN-77\nShipment to consignee\nPage 1 of 2",
    "Delivery note DN-77 delivered items\nPage 2 of 2",
    "SERVICE AGREEMENT\nThis contract is made between the parties hereinafter\nPage 1 of 5",
    "The agreement term is twelve months between the parties",
    "Governing law of this contract is German law",
    "Further provisions of the agreement",
    "Signature of both parties to the contract",
]


def mixed_packet() -> bytes:
    """10 pages: invoice (1-3), delivery note (4-5), contract (6-10)."""
    return make_pdf(MIXED_PACKET_PAGES)


def make_text_pdf(rows: list[list[tuple[float, str]]], *, font_size: int = 10) -> bytes:
    """One page; each row is a list of (x position in pt, text) placed on one line."""
    lines = []
    for n, row in enumerate(rows):
        y = 750 - n * int(font_size * 1.6)
        for x, text in row:
            lines.append(f"BT /F1 {font_size} Tf {x} {y} Td ({_escape(text)}) Tj ET")
    stream = "\n".join(lines).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
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


INVOICE_ROWS: list[list[tuple[float, str]]] = [
    [(72, "ACME Industrial Supplies GmbH")],
    [(72, "Hauptstrasse 1, 10115 Berlin")],
    [(72, "VAT ID: DE123456789")],
    [],
    [(72, "INVOICE")],
    [(72, "Invoice number: INV-2026-00123"), (360, "Invoice date: 03.10.2026")],
    [(72, "PO number: PO-5531"), (360, "Due date: 02.11.2026")],
    [],
    [(72, "Description"), (300, "Qty"), (370, "Unit price"), (480, "Amount")],
    [(72, "Hydraulic pump HP-200"), (300, "2"), (370, "450.00"), (480, "900.00")],
    [(72, "Seal kit SK-7"), (300, "10"), (370, "12.50"), (480, "125.00")],
    [(72, "Installation service"), (300, "1"), (370, "25.00"), (480, "25.00")],
    [],
    [(370, "Subtotal"), (480, "1,050.00")],
    [(370, "VAT 19%"), (480, "199.50")],
    [(370, "Total due"), (480, "1,249.50 EUR")],
    [],
    [(72, "IBAN: DE89 3704 0044 0532 0130 00")],
    [(72, "Currency: EUR")],
]


def invoice_pdf(rows: list[list[tuple[float, str]]] | None = None) -> bytes:
    return make_text_pdf(rows or INVOICE_ROWS)


def clean_invoice_pdf(total: str = "1,249.50 EUR") -> bytes:
    """An invoice every rule accepts: labelled supplier, consistent totals, valid IBAN."""
    rows = [list(r) for r in INVOICE_ROWS]
    rows[0] = [(72, "Supplier: ACME Industrial Supplies GmbH")]
    rows[15] = [(370, "Total due"), (480, total)]
    return make_text_pdf(rows)


def scan_like_pdf(lines: list[str], *, dpi: int = 300, skew_degrees: float = 1.2) -> bytes:
    """A single-page PDF that imitates a scanner: rendered text, slight skew, sensor
    noise, blur and JPEG compression, no text layer.

    Synthetic — not a real scanned document. Real scans for OCR validation belong
    in `tests/fixtures/scans/` (see the README there).
    """
    import random

    import pypdfium2 as pdfium
    from PIL import ImageFilter

    pdf = pdfium.PdfDocument(make_pdf(["\n".join(lines)], font_size=14))
    try:
        page = pdf[0].render(scale=dpi / 72).to_pil().convert("L")
    finally:
        pdf.close()
    page = page.rotate(skew_degrees, resample=Image.Resampling.BICUBIC, fillcolor=255)
    rng = random.Random(4711)  # noqa: S311 — deterministic noise, not security
    noise = Image.frombytes(
        "L", page.size, bytes(rng.randrange(0, 40) for _ in range(page.width * page.height))
    )
    page = Image.blend(page, Image.eval(noise, lambda v: 255 - v), 0.25)
    page = page.filter(ImageFilter.GaussianBlur(0.6))
    buffer = io.BytesIO()
    page.convert("RGB").save(buffer, format="PDF", resolution=dpi, quality=60)
    return buffer.getvalue()


# The EICAR anti-virus test string: harmless by design, detected by every scanner.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def eicar_pdf() -> bytes:
    """A valid PDF carrying EICAR as an embedded file.

    It passes the PDF allow-list (a bare EICAR file is refused with 415 before
    any scan), and ClamAV extracts the embedded stream and reports
    `Eicar-Signature`. That is the malware path through the real upload API.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R"
        b" /Names << /EmbeddedFiles << /Names [(eicar.com) 5 0 R] >> >> >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(EICAR) + EICAR + b"\nendstream",
        b"<< /Type /Filespec /F (eicar.com) /EF << /F 4 0 R >> >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
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
