"""Document geometry: the internal representation every digitizer maps into.

Coordinates are normalised `[x0, y0, x1, y1]` in 0..1 relative to the page as
displayed (after rotation), origin top-left. The viewer can draw them at any
zoom; extraction provenance points at them. Pure Python, no I/O.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from typing import Any

# --- primitives ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def clamp(cls, x0: float, y0: float, x1: float, y1: float) -> BBox:
        def c(v: float) -> float:
            return min(max(v, 0.0), 1.0)

        return cls(c(min(x0, x1)), c(min(y0, y1)), c(max(x0, x1)), c(max(y0, y1)))

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def union(self, other: BBox) -> BBox:
        return BBox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def as_list(self) -> list[float]:
        return [round(self.x0, 5), round(self.y0, 5), round(self.x1, 5), round(self.y1, 5)]

    @classmethod
    def from_list(cls, values: list[float]) -> BBox:
        return cls(*values)


def union_all(boxes: list[BBox]) -> BBox:
    result = boxes[0]
    for box in boxes[1:]:
        result = result.union(box)
    return result


def rotate_normalized(box: BBox, rotation: int) -> BBox:
    """Map a box from unrotated page space to displayed space (PDF /Rotate, clockwise)."""
    r = rotation % 360
    if r == 90:
        return BBox.clamp(1 - box.y1, box.x0, 1 - box.y0, box.x1)
    if r == 180:
        return BBox.clamp(1 - box.x1, 1 - box.y1, 1 - box.x0, 1 - box.y0)
    if r == 270:
        return BBox.clamp(box.y0, 1 - box.x1, box.y1, 1 - box.x0)
    return box


class TextSource(StrEnum):
    NATIVE = "native"  # PDF text layer
    OCR = "ocr"
    NONE = "none"  # nothing readable (e.g. image page without an OCR engine)


class OcrStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    DONE = "done"
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"


# --- layout -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    bbox: BBox
    confidence: float | None = None  # OCR confidence 0..1; None for native text


@dataclass(frozen=True, slots=True)
class Line:
    id: str  # stable within the page: "p{page}-l{index}"
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def bbox(self) -> BBox:
        return union_all([w.bbox for w in self.words])


@dataclass(frozen=True, slots=True)
class Block:
    lines: tuple[Line, ...]

    @property
    def bbox(self) -> BBox:
        return union_all([line.bbox for line in self.lines])

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True, slots=True)
class PageLayout:
    page_number: int
    width: float
    height: float
    unit: str
    rotation: int
    source: TextSource
    blocks: tuple[Block, ...] = ()
    text_quality: float = 0.0
    language: str | None = None
    ocr_confidence: float | None = None
    table_density: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def lines(self) -> list[Line]:
        return [line for block in self.blocks for line in block.lines]

    @property
    def words(self) -> list[Word]:
        return [w for line in self.lines for w in line.words]

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "unit": self.unit,
            "rotation": self.rotation,
            "source": self.source.value,
            "text_quality": self.text_quality,
            "language": self.language,
            "ocr_confidence": self.ocr_confidence,
            "table_density": self.table_density,
            "metadata": self.metadata,
            "blocks": [
                {
                    "lines": [
                        {
                            "id": line.id,
                            "words": [
                                {"t": w.text, "b": w.bbox.as_list(), "c": w.confidence}
                                for w in line.words
                            ],
                        }
                        for line in block.lines
                    ]
                }
                for block in self.blocks
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PageLayout:
        blocks = tuple(
            Block(
                lines=tuple(
                    Line(
                        id=line["id"],
                        words=tuple(
                            Word(w["t"], BBox.from_list(w["b"]), w.get("c")) for w in line["words"]
                        ),
                    )
                    for line in block["lines"]
                )
            )
            for block in data["blocks"]
        )
        return cls(
            page_number=data["page_number"],
            width=data["width"],
            height=data["height"],
            unit=data["unit"],
            rotation=data["rotation"],
            source=TextSource(data["source"]),
            blocks=blocks,
            text_quality=data["text_quality"],
            language=data.get("language"),
            ocr_confidence=data.get("ocr_confidence"),
            table_density=data.get("table_density", 0.0),
            metadata=data.get("metadata", {}),
        )


# --- assembly -------------------------------------------------------------------


def group_lines(words: list[Word], page_number: int) -> list[Line]:
    """Group words into reading-order lines by vertical overlap."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (round(w.bbox.cy, 3), w.bbox.x0))
    rows: list[list[Word]] = []
    for word in ordered:
        if rows:
            last = rows[-1]
            ref = union_all([w.bbox for w in last])
            overlap = min(ref.y1, word.bbox.y1) - max(ref.y0, word.bbox.y0)
            if overlap > 0.5 * min(ref.height, word.bbox.height or ref.height):
                last.append(word)
                continue
        rows.append([word])
    rows.sort(key=lambda r: min(w.bbox.y0 for w in r))
    return [
        Line(id=f"p{page_number}-l{i}", words=tuple(sorted(row, key=lambda w: w.bbox.x0)))
        for i, row in enumerate(rows)
    ]


def group_blocks(lines: list[Line]) -> list[Block]:
    """Split lines into blocks at vertical gaps larger than 1.2x the median line height."""
    if not lines:
        return []
    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    median_h = statistics.median(heights) if heights else 0.01
    blocks: list[list[Line]] = [[lines[0]]]
    for prev, line in pairwise(lines):
        gap = line.bbox.y0 - prev.bbox.y1
        if gap > 1.2 * median_h:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return [Block(lines=tuple(b)) for b in blocks]


def segments(line: Line, gap_threshold: float) -> int:
    """Number of horizontally separated cell-like segments in a line."""
    count = 1
    for left, right in zip(line.words, line.words[1:], strict=False):
        if right.bbox.x0 - left.bbox.x1 > gap_threshold:
            count += 1
    return count


def table_density(lines: list[Line]) -> float:
    """Share of lines that look like table rows (>= 3 well-separated segments)."""
    if not lines:
        return 0.0
    widths = [w.bbox.width / max(len(w.text), 1) for line in lines for w in line.words if w.text]
    char_w = statistics.median(widths) if widths else 0.01
    rows = sum(1 for line in lines if segments(line, 2.5 * char_w) >= 3)
    return round(rows / len(lines), 3)


_GOOD_CHAR = re.compile(r"[\w\s.,;:!?()\[\]{}'\"/\\%&@#€$£¥+\-*=<>|_~^°§]", re.UNICODE)


def text_quality(text: str) -> float:
    """Heuristic 0..1 readability of extracted text (garbled encodings score low)."""
    stripped = [c for c in text if not c.isspace()]
    if not stripped:
        return 0.0
    good = sum(1 for c in stripped if _GOOD_CHAR.match(c))
    bad = sum(1 for c in stripped if c == "�" or 0xE000 <= ord(c) <= 0xF8FF)
    tokens = text.split()
    wordlike = sum(1 for t in tokens if sum(ch.isalpha() for ch in t) >= 2)
    word_ratio = wordlike / len(tokens) if tokens else 0.0
    score = (good / len(stripped)) * min(1.0, word_ratio / 0.4) - 5 * bad / len(stripped)
    return round(max(0.0, min(1.0, score)), 3)


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_STOPWORDS: dict[str, frozenset[str]] = {
    "en": _words("the and of to in for is on with this that by from total invoice date"),
    "de": _words("der die das und mit für ist von den zu auf rechnung datum betrag"),
    "fr": _words("le la les et des du pour est avec une facture date montant"),
    "es": _words("el la los las y de del para con una factura fecha importe"),
    "tr": _words("ve bir bu ile için olarak fatura tarih tutar toplam"),
    "nl": _words("de het een en van voor met is op factuur datum bedrag"),
    "it": _words("il lo la gli e di per con una fattura data importo"),
}


def detect_language(text: str) -> str | None:
    """Cheap stopword vote; None when there is not enough signal."""
    tokens = [t.strip(".,:;!?()").lower() for t in text.split()]
    scores = {lang: sum(1 for t in tokens if t in words) for lang, words in _STOPWORDS.items()}
    lang, best = max(scores.items(), key=lambda kv: kv[1])
    return lang if best >= 3 else None
