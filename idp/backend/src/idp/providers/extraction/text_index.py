"""Map character spans in a line's text back to word boxes (for provenance)."""

from __future__ import annotations

from dataclasses import dataclass

from idp.domain.geometry import BBox, Line, union_all


@dataclass(frozen=True, slots=True)
class Span:
    text: str
    bbox: BBox | None
    word_confidence: float | None  # mean OCR word confidence; None for native text


def _offsets(line: Line) -> list[tuple[int, int]]:
    offsets, cursor = [], 0
    for word in line.words:
        offsets.append((cursor, cursor + len(word.text)))
        cursor += len(word.text) + 1  # Line.text joins words with single spaces
    return offsets


def span(line: Line, start: int, end: int) -> Span:
    words = [
        w for w, (s, e) in zip(line.words, _offsets(line), strict=True) if s < end and e > start
    ]
    text = line.text[start:end].strip()
    if not words:
        return Span(text=text, bbox=None, word_confidence=None)
    confidences = [w.confidence for w in words if w.confidence is not None]
    return Span(
        text=text,
        bbox=union_all([w.bbox for w in words]),
        word_confidence=sum(confidences) / len(confidences) if confidences else None,
    )


def word_index_at(line: Line, char_pos: int) -> int | None:
    for i, (s, e) in enumerate(_offsets(line)):
        if s <= char_pos < e:
            return i
    return None
