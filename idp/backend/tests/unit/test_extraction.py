import tempfile
from pathlib import Path

import pytest

from idp.domain.geometry import BBox, PageLayout, TextSource, Word, group_blocks, group_lines
from idp.domain.taxonomy import SchemaDefinition
from idp.domain.templates import TEMPLATES
from idp.providers.digitization.local import HybridDigitizer
from idp.providers.extraction.base import ExtractionContext
from idp.providers.extraction.key_value import KeyValueExtractor
from idp.providers.extraction.merge import merge
from idp.providers.extraction.regex_extractor import RegexExtractor
from idp.providers.extraction.table import TableExtractor
from tests.fixtures import files

PROVIDERS = (RegexExtractor(), KeyValueExtractor(), TableExtractor())


@pytest.fixture(scope="module")
def invoice_layout() -> PageLayout:
    import asyncio

    digitizer = HybridDigitizer(
        ocr=None,
        workers=1,
        timeout_seconds=60,
        max_pages=5,
        memory_limit_mb=2048,
        render_dpi=72,
        ocr_dpi=150,
        min_native_quality=0.5,
    )
    work = Path(tempfile.mkdtemp())
    (work / "in").write_bytes(files.invoice_pdf())
    try:
        result = asyncio.run(digitizer.digitize(work / "in", "application/pdf", work))
    finally:
        digitizer.close()
    return result.pages[0].layout


async def _run(schema: SchemaDefinition, *layouts: PageLayout):  # type: ignore[no-untyped-def]
    ctx = ExtractionContext(schema=schema, layouts=layouts)
    candidates = []
    for provider in PROVIDERS:
        if provider.assess(ctx).can_handle:
            candidates.extend(await provider.extract(ctx))
    return merge(schema, candidates)


async def test_invoice_fields_rows_and_provenance(invoice_layout: PageLayout) -> None:
    merged = await _run(TEMPLATES["invoice"].definition, invoice_layout)
    scalars = {m.path: m.best for m in merged if not m.row_id}
    values = {p: (c.value if c else None) for p, c in scalars.items()}
    assert values == {
        "vendor_name": "ACME Industrial Supplies GmbH",
        "vendor_tax_number": "DE123456789",
        "invoice_number": "INV-2026-00123",
        "invoice_date": "2026-10-03",
        "due_date": "2026-11-02",
        "purchase_order_number": "PO-5531",
        "currency": "EUR",
        "subtotal": "1050.00",
        "tax": "199.50",
        "total": "1249.50",
        "iban": "DE89370400440532013000",
    }
    # The vendor name comes from a weak heuristic and must not look certain.
    assert scalars["vendor_name"] is not None
    assert scalars["vendor_name"].confidence < 0.6
    assert scalars["invoice_number"] is not None
    assert scalars["invoice_number"].method == "regex:native"

    total = scalars["total"]
    assert total is not None
    assert total.page == 1
    assert total.raw_text == "1,249.50"
    assert total.bbox is not None
    assert 0.7 < total.bbox.x0 < 0.9  # right-hand amount column
    assert total.bbox.y0 > 0.2

    rows: dict[str, dict[str, object]] = {}
    for m in merged:
        if m.row_id and m.best:
            rows.setdefault(m.row_id, {})[m.path.split(".")[-1]] = m.best.value
    expected = [
        {
            "description": "Hydraulic pump HP-200",
            "quantity": "2",
            "unit_price": "450.00",
            "total": "900.00",
        },
        {
            "description": "Seal kit SK-7",
            "quantity": "10",
            "unit_price": "12.50",
            "total": "125.00",
        },
        {
            "description": "Installation service",
            "quantity": "1",
            "unit_price": "25.00",
            "total": "25.00",
        },
    ]
    assert sorted(rows.values(), key=str) == sorted(expected, key=str)
    assert all(r.startswith("r_") for r in rows)


async def test_missing_fields_are_explicit() -> None:
    schema = SchemaDefinition.model_validate(
        {"fields": [{"name": "order_number", "type": "string", "aliases": ["order no"]}]}
    )
    layout = _layout([("Nothing relevant here", 0.1, 0.1)])
    [field] = await _run(schema, layout)
    assert field.path == "order_number"
    assert field.best is None


def _layout(rows: list[tuple[str, float, float]], conf: float | None = None) -> PageLayout:
    words = []
    for text, x, y in rows:
        cursor = x
        for token in text.split():
            width = 0.012 * len(token)
            words.append(Word(token, BBox(cursor, y, cursor + width, y + 0.015), conf))
            cursor += width + 0.008
    lines = group_lines(words, 1)
    source = TextSource.OCR if conf is not None else TextSource.NATIVE
    return PageLayout(1, 612, 792, "pt", 0, source, tuple(group_blocks(lines)), 0.9)


async def test_contained_alias_does_not_steal_value() -> None:
    schema = SchemaDefinition.model_validate(
        {
            "fields": [
                {"name": "invoice_date", "type": "date", "aliases": ["date"]},
                {"name": "due_date", "type": "date", "aliases": ["due date"]},
            ]
        }
    )
    layout = _layout([("Due date: 17.10.2026", 0.1, 0.1), ("Date: 01.10.2026", 0.1, 0.2)])
    result = {m.path: m.best.value for m in await _run(schema, layout) if m.best}
    assert result == {"invoice_date": "2026-10-01", "due_date": "2026-10-17"}


async def test_value_below_label_and_ocr_confidence_factor() -> None:
    schema = SchemaDefinition.model_validate(
        {"fields": [{"name": "total", "type": "decimal", "aliases": ["amount due"]}]}
    )
    layout = _layout([("Amount due", 0.6, 0.5), ("1.250,00", 0.6, 0.52)], conf=0.5)
    [field] = await _run(schema, layout)
    assert field.best is not None
    assert field.best.value == "1250.00"
    assert field.best.method == "key_value:below:ocr"
    assert field.best.confidence < 0.45  # 0.75 base x 0.5 OCR word confidence


async def test_failed_normalization_halves_confidence() -> None:
    schema = SchemaDefinition.model_validate(
        {"fields": [{"name": "issued", "type": "date", "aliases": ["issued"]}]}
    )
    [field] = await _run(schema, _layout([("Issued: sometime soon", 0.1, 0.1)]))
    assert field.best is None or not field.best.normalized


async def test_pathological_regex_cannot_hang() -> None:
    schema = SchemaDefinition.model_validate(
        {
            "fields": [
                {"name": "x", "type": "string", "extraction_hints": {"patterns": ["((a+)+)+b"]}}
            ]
        }
    )
    layout = _layout([("a" * 60 + "c", 0.1, 0.1)])
    merged = await _run(schema, layout)
    assert merged[0].best is None


def _ocr_row_split_layout() -> PageLayout:
    """How Tesseract reads a totals block: each visual row becomes two lines, the label
    column and the amount column (observed on the phase 13 scanned fixtures, F17)."""
    from idp.domain.geometry import Line

    def line(i: int, text: str, x0: float, y0: float) -> Line:
        words, x = [], x0
        for t in text.split():
            words.append(Word(t, BBox(x, y0, x + 0.012 * len(t), y0 + 0.012), 0.95))
            x += 0.012 * len(t) + 0.008
        return Line(id=f"p1-l{i}", words=tuple(words))

    lines = [
        line(0, "INVOICE", 0.12, 0.10),
        line(1, "Invoice number: INV-2026-22082", 0.12, 0.12),
        line(2, "Subtotal", 0.605, 0.245),
        line(3, "17,477.52", 0.78, 0.2455),
        line(4, "VAT 7%", 0.604, 0.266),
        line(5, "1,223.43", 0.78, 0.2662),
        line(6, "Total due", 0.604, 0.286),
        line(7, "18,700.95 EUR", 0.78, 0.2858),
        line(8, "IBAN: DE82 3102 6511 4045 5884 57", 0.12, 0.33),
    ]
    return PageLayout(
        page_number=1,
        width=612,
        height=792,
        unit="pt",
        rotation=0,
        source=TextSource.OCR,
        blocks=tuple(group_blocks(lines)),
    )


async def test_ocr_amounts_split_from_their_labels_are_read_on_the_same_row() -> None:
    """F17: the value of a label alone on its OCR line is on the same visual row to the
    right - never the VAT rate on the label's line, never the next row's amount."""
    merged = await _run(TEMPLATES["invoice"].definition, _ocr_row_split_layout())
    values = {m.path: (m.best.value if m.best else None) for m in merged if not m.row_id}
    assert values["tax"] == "1223.43"  # not "7" from "VAT 7%"
    assert values["subtotal"] == "17477.52"  # not the VAT amount on the next row
    assert values["total"] == "18700.95"


def test_a_rate_is_never_returned_as_an_amount() -> None:
    from idp.providers.extraction.key_value import _value_in

    tax = dict(TEMPLATES["invoice"].definition.flatten())["tax"]
    assert _value_in(" 7%", tax) is None
    assert _value_in(" 19% 199.50", tax) is not None  # the amount after the rate still wins
