import pytest

from idp.domain.geometry import (
    BBox,
    Line,
    PageLayout,
    TextSource,
    Word,
    detect_language,
    group_blocks,
    group_lines,
    rotate_normalized,
    table_density,
    text_quality,
)
from idp.providers.ocr.tesseract import parse_tsv


def w(text: str, x0: float, y0: float, x1: float, y1: float) -> Word:
    return Word(text, BBox(x0, y0, x1, y1))


def test_bbox_clamp_and_union() -> None:
    box = BBox.clamp(1.2, -0.1, 0.5, 0.4)
    assert box == BBox(0.5, 0.0, 1.0, 0.4)
    assert BBox(0, 0, 0.1, 0.1).union(BBox(0.5, 0.5, 0.6, 0.7)) == BBox(0, 0, 0.6, 0.7)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotation_preserves_area_and_bounds(rotation: int) -> None:
    box = BBox(0.1, 0.2, 0.3, 0.25)
    rotated = rotate_normalized(box, rotation)
    assert 0 <= rotated.x0 < rotated.x1 <= 1 and 0 <= rotated.y0 < rotated.y1 <= 1
    assert rotated.width * rotated.height == pytest.approx(box.width * box.height)
    twice = rotate_normalized(rotate_normalized(box, 180), 180)
    assert twice.as_list() == pytest.approx(box.as_list())


def test_lines_and_blocks_follow_reading_order() -> None:
    words = [
        w("Total", 0.1, 0.50, 0.2, 0.52),
        w("Invoice", 0.1, 0.10, 0.25, 0.12),
        w("INV-1", 0.3, 0.101, 0.4, 0.121),
        w("100.00", 0.7, 0.50, 0.8, 0.52),
        w("Date", 0.1, 0.13, 0.2, 0.15),
    ]
    lines = group_lines(words, page_number=1)
    assert [line.text for line in lines] == ["Invoice INV-1", "Date", "Total 100.00"]
    assert [line.id for line in lines] == ["p1-l0", "p1-l1", "p1-l2"]
    blocks = group_blocks(lines)
    assert [b.text for b in blocks] == ["Invoice INV-1\nDate", "Total 100.00"]


def test_table_density_detects_columnar_rows() -> None:
    row = (
        w("Widget", 0.1, 0.3, 0.2, 0.32),
        w("2", 0.5, 0.3, 0.52, 0.32),
        w("9.99", 0.8, 0.3, 0.85, 0.32),
    )
    prose = (w("Thank", 0.1, 0.6, 0.15, 0.62), w("you", 0.16, 0.6, 0.2, 0.62))
    lines = [Line("a", row), Line("b", prose)]
    assert table_density(lines) == 0.5


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("Invoice number INV-2026-00123 total due 1,250.00 EUR", 0.9, 1.0),
        ("��� ��", 0.0, 0.0),
        (" ", 0.0, 0.0),
        ("", 0.0, 0.0),
    ],
)
def test_text_quality(text: str, low: float, high: float) -> None:
    assert low <= text_quality(text) <= high


def test_language_detection() -> None:
    assert detect_language("Rechnung Datum Betrag und die Summe der Positionen") == "de"
    assert detect_language("The invoice date and the total for this order") == "en"
    assert detect_language("12345 67890") is None


def test_layout_json_roundtrip() -> None:
    lines = group_lines([w("Hello", 0.1, 0.1, 0.2, 0.12), w("world", 0.21, 0.1, 0.3, 0.12)], 1)
    layout = PageLayout(
        1, 612, 792, "pt", 0, TextSource.NATIVE, tuple(group_blocks(lines)), 0.98, "en"
    )
    restored = PageLayout.from_dict(layout.to_dict())
    assert restored.text == "Hello world"
    assert restored.lines[0].id == "p1-l0"
    assert restored.words[0].bbox.as_list() == pytest.approx(layout.words[0].bbox.as_list())


def test_tesseract_tsv_parsing() -> None:
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "1\t1\t0\t0\t0\t0\t0\t0\t1000\t2000\t-1\t",
        "5\t1\t1\t1\t1\t1\t100\t200\t150\t40\t95.5\tINVOICE",
        "5\t1\t1\t1\t1\t2\t300\t200\t200\t40\t88\tINV-7",
        "5\t1\t1\t1\t2\t1\t100\t300\t100\t40\t70\tTotal",
        "5\t1\t1\t1\t2\t2\t500\t300\t100\t40\t-1\t ",
    ]
    page = parse_tsv("\n".join([header, *rows]), 1000, 2000, page_number=3)
    assert [line.text for line in page.lines] == ["INVOICE INV-7", "Total"]
    assert page.lines[0].id == "p3-l0"
    assert page.lines[0].words[0].bbox == BBox(0.1, 0.1, 0.25, 0.12)
    assert page.lines[0].words[0].confidence == 0.955
    assert page.mean_confidence == pytest.approx((0.955 + 0.88 + 0.70) / 3, abs=1e-3)
