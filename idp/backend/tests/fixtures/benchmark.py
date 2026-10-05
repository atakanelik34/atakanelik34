"""Deterministic benchmark / calibration fixture set with ground truth.

SYNTHETIC. Every document is generated in code from a seed: fictional vendors,
valid-checksum but fictional IBANs, no personal data. The set exercises the
variation the deterministic extractors must cope with (labelled vs unlabelled
supplier, English vs German labels, date and number formats, optional fields,
1-5 line items, scanned pages), plus documents that must *not* be extracted
(non-invoices) or cannot be processed (corrupted files).

It is not a sample of any real document population: accuracy measured on it
says how the pipeline behaves on these layouts, not how it will behave on a
customer's invoices. See docs/GO_LIVE_READINESS.md (calibration).
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from PIL import Image

from tests.fixtures.files import make_text_pdf

VENDORS = [
    ("Nordwind Maschinenbau GmbH", "DE811234567"),
    ("Blue Harbor Logistics Ltd", "GB123456789"),
    ("Atlas Office Supplies AG", "DE298765432"),
    ("Kestrel Engineering BV", "NL123456789B01"),
    ("Sunvale Packaging GmbH", "DE356789012"),
    ("Orion Facility Services SE", "DE412345678"),
    ("Maple Ridge Components Inc", None),
    ("Falkenberg Elektro KG", "DE523456789"),
    ("Westbrook Paper Mills Ltd", "GB987654321"),
    ("Tannenhof Druck GmbH", "DE634567890"),
    ("Corvid Software Solutions Oy", "FI12345678"),
    ("Riverside Tools & Hardware Ltd", None),
]
ITEMS = [
    "Hydraulic pump HP-200",
    "Seal kit SK-7",
    "Installation service",
    "Copy paper A4 80g (box)",
    "Pallet transport Berlin-Hamburg",
    "Maintenance contract Q3",
    "Steel bracket 40mm",
    "Cable reel 50m",
    "Consulting hours",
    "Toner cartridge TN-421",
]
EN = {
    "title": "INVOICE",
    "supplier": "Supplier",
    "number": "Invoice number",
    "date": "Invoice date",
    "due": "Due date",
    "po": "PO number",
    "vat_id": "VAT ID",
    "desc": "Description",
    "qty": "Qty",
    "price": "Unit price",
    "amount": "Amount",
    "subtotal": "Subtotal",
    "tax": "VAT",
    "total": "Total due",
    "currency": "Currency",
}
DE = {
    "title": "RECHNUNG",
    "supplier": "Lieferant",
    "number": "Rechnungsnummer",
    "date": "Rechnungsdatum",
    "due": "Zahlbar bis",
    "po": "Bestellnummer",
    "vat_id": "USt-IdNr",
    "desc": "Beschreibung",
    "qty": "Menge",
    "price": "Einzelpreis",
    "amount": "Betrag",
    "subtotal": "Zwischensumme",
    "tax": "MwSt",
    "total": "Gesamtbetrag",
    "currency": "Waehrung",
}
CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    kind: str  # native_invoice | scanned_invoice | non_invoice | corrupted
    data: bytes
    truth: dict[str, Any] | None  # normalised expected values (None: nothing to extract)
    lines: list[dict[str, str]] = field(default_factory=list)
    variant: dict[str, Any] = field(default_factory=dict)


def _iban(rng: random.Random) -> str:
    """A German IBAN with a valid mod-97 check (fictional bank and account)."""
    bban = f"{rng.randrange(10**7, 10**8)}{rng.randrange(10**9, 10**10)}"
    digits = int(bban + "131400")  # D=13 E=14, check digits 00
    check = 98 - digits % 97
    return f"DE{check:02d}{bban}"


def _money(value: Decimal, style: str) -> str:
    text = f"{value:,.2f}"
    if style == "de":
        text = text.replace(",", "_").replace(".", ",").replace("_", ".")
    return text


def _date(day: int, month: int, year: int, style: str) -> str:
    if style == "iso":
        return f"{year:04d}-{month:02d}-{day:02d}"
    return f"{day:02d}.{month:02d}.{year:04d}"


def invoice_case(seed: int, *, scanned: bool = False) -> Case:
    rng = random.Random(seed)  # noqa: S311 — fixture generation, not security
    lang = DE if rng.random() < 0.25 else EN
    money_style = "de" if (lang is DE or rng.random() < 0.15) else "en"
    date_style = rng.choice(["dmy", "dmy", "iso"])
    supplier_style = rng.choice(["labelled", "labelled", "unlabelled"])
    vendor, vat_id = rng.choice(VENDORS)
    currency = rng.choice(["EUR", "EUR", "EUR", "USD", "GBP"])
    rate = Decimal(rng.choice(["19", "19", "7", "20"]))
    number = rng.choice(
        [
            f"INV-2026-{rng.randrange(10000, 99999)}",
            f"RE-{rng.randrange(1000, 9999)}",
            f"A{rng.randrange(100000, 999999)}",
            f"2026-{rng.randrange(100, 999)}-{rng.randrange(10, 99)}",
        ]
    )
    day, month = rng.randrange(1, 28), rng.randrange(1, 10)
    due = (min(day + 14, 28), month + 1) if rng.random() < 0.7 else None
    po = f"PO-{rng.randrange(1000, 9999)}" if rng.random() < 0.6 else None
    iban = _iban(rng) if rng.random() < 0.8 else None
    with_vat_id = vat_id is not None and rng.random() < 0.85

    items = []
    for _ in range(rng.randrange(1, 6)):
        qty = Decimal(rng.randrange(1, 25))
        price = (Decimal(rng.randrange(150, 250000)) / 100).quantize(CENT)
        items.append((rng.choice(ITEMS), qty, price, (qty * price).quantize(CENT)))
    subtotal = sum((i[3] for i in items), Decimal("0"))
    tax = (subtotal * rate / 100).quantize(CENT, ROUND_HALF_UP)
    total = subtotal + tax

    amount_x = rng.choice([470, 480, 490])
    rows: list[list[tuple[float, str]]] = []
    rows.append([(72, f"{lang['supplier']}: {vendor}" if supplier_style == "labelled" else vendor)])
    rows.append([(72, rng.choice(["Hauptstrasse 1, 10115 Berlin", "12 Dock Road, London E16"]))])
    if with_vat_id:
        rows.append([(72, f"{lang['vat_id']}: {vat_id}")])
    rows += [[], [(72, lang["title"])]]
    rows.append(
        [
            (72, f"{lang['number']}: {number}"),
            (360, f"{lang['date']}: {_date(day, month, 2026, date_style)}"),
        ]
    )
    second: list[tuple[float, str]] = []
    if po:
        second.append((72, f"{lang['po']}: {po}"))
    if due:
        second.append((360, f"{lang['due']}: {_date(due[0], due[1], 2026, date_style)}"))
    if second:
        rows.append(second)
    rows += [
        [],
        [(72, lang["desc"]), (300, lang["qty"]), (370, lang["price"]), (amount_x, lang["amount"])],
    ]
    for desc, qty, price, amount in items:
        rows.append(
            [
                (72, desc),
                (300, str(qty)),
                (370, _money(price, money_style)),
                (amount_x, _money(amount, money_style)),
            ]
        )
    rows += [
        [],
        [(370, lang["subtotal"]), (amount_x, _money(subtotal, money_style))],
        [(370, f"{lang['tax']} {rate}%"), (amount_x, _money(tax, money_style))],
        [(370, lang["total"]), (amount_x, f"{_money(total, money_style)} {currency}")],
        [],
    ]
    if iban:
        rows.append([(72, f"IBAN: {' '.join(iban[i : i + 4] for i in range(0, len(iban), 4))}")])
    rows.append([(72, f"{lang['currency']}: {currency}")])

    data = make_text_pdf(rows)
    if scanned:
        data = scan(data, seed=seed)
    truth: dict[str, Any] = {
        "vendor_name": vendor,
        "vendor_tax_number": vat_id if with_vat_id else None,
        "invoice_number": number,
        "invoice_date": f"2026-{month:02d}-{day:02d}",
        "due_date": f"2026-{due[1]:02d}-{due[0]:02d}" if due else None,
        "purchase_order_number": po,
        "currency": currency,
        "subtotal": str(subtotal),
        "tax": str(tax),
        "total": str(total),
        "iban": iban,
    }
    lines = [
        {"description": d, "quantity": str(q), "unit_price": str(p), "total": str(a)}
        for d, q, p, a in items
    ]
    return Case(
        name=f"{'scan' if scanned else 'native'}-{seed}",
        kind="scanned_invoice" if scanned else "native_invoice",
        data=data,
        truth=truth,
        lines=lines,
        variant={
            "lang": "de" if lang is DE else "en",
            "money": money_style,
            "date": date_style,
            "supplier": supplier_style,
            "lines": len(items),
        },
    )


def scan(pdf: bytes, *, seed: int, dpi: int = 300) -> bytes:
    """Render page 1 and degrade it like a scanner: skew, noise, blur, JPEG; no text layer."""
    import pypdfium2 as pdfium
    from PIL import ImageFilter

    rng = random.Random(seed)  # noqa: S311
    doc = pdfium.PdfDocument(pdf)
    try:
        page = doc[0].render(scale=dpi / 72).to_pil().convert("L")
    finally:
        doc.close()
    page = page.rotate(rng.uniform(-1.5, 1.5), resample=Image.Resampling.BICUBIC, fillcolor=255)
    noise = Image.effect_noise(page.size, rng.uniform(20, 45))
    page = Image.blend(page, noise, rng.uniform(0.08, 0.18))
    page = page.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8)))
    buffer = io.BytesIO()
    page.convert("RGB").save(buffer, format="PDF", resolution=dpi, quality=rng.randrange(55, 80))
    return buffer.getvalue()


def non_invoice_case(seed: int) -> Case:
    rng = random.Random(seed)  # noqa: S311
    kind = rng.choice(["Delivery note", "Meeting minutes", "Price list"])
    rows = [
        [(72, rng.choice(VENDORS)[0])],
        [],
        [(72, kind.upper())],
        [(72, f"Reference: DN-{rng.randrange(1000, 9999)}")],
        [],
        *[[(72, rng.choice(ITEMS)), (400, f"{rng.randrange(1, 9)} pcs")] for _ in range(4)],
    ]
    return Case(name=f"other-{seed}", kind="non_invoice", data=make_text_pdf(rows), truth=None)


def corrupted_case(seed: int) -> Case:
    rng = random.Random(seed)  # noqa: S311
    good = make_text_pdf([[(72, "Invoice number: X-1")]])
    cut = rng.randrange(40, len(good) // 2)
    return Case(
        name=f"corrupt-{seed}", kind="corrupted", data=good[:cut] + b"\x00garbage", truth=None
    )


def fixture_set(
    *, native: int = 60, scanned: int = 15, other: int = 6, corrupted: int = 3, seed: int = 2026
) -> list[Case]:
    cases = [invoice_case(seed + i) for i in range(native)]
    cases += [invoice_case(seed + 1000 + i, scanned=True) for i in range(scanned)]
    cases += [non_invoice_case(seed + 2000 + i) for i in range(other)]
    cases += [corrupted_case(seed + 3000 + i) for i in range(corrupted)]
    return cases
