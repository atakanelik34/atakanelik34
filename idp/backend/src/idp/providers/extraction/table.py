"""Line-item tables: header detection from column aliases, rows by x-position.

For every array-of-object field, the header is the first line matching aliases
of at least two child fields. Each following line becomes a row until a stop
line (totals/taxes) or a large vertical gap. Words are assigned to the column
whose header span they overlap, else the nearest header centre. Every row gets
a stable row id.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from idp.domain.geometry import Line, PageLayout, Word, union_all
from idp.domain.taxonomy import FieldDefinition, FieldType
from idp.providers.extraction.base import (
    ExtractionContext,
    FieldCandidate,
    ProviderInfo,
    Suitability,
    new_row_id,
)
from idp.providers.extraction.common import alias_regex, aliases_for, candidate
from idp.providers.extraction.text_index import Span, span

_STOP = re.compile(
    r"^\s*(sub\s*total|subtotal|total|summe|zwischensumme|gesamt|netto|net amount"
    r"|vat|tax|mwst|ust|kdv|toplam)\b",
    re.IGNORECASE,
)
_MAX_ROW_GAP = 3.0  # in median line heights
_NUMERIC = (FieldType.DECIMAL, FieldType.INTEGER)


@dataclass(frozen=True, slots=True)
class _Column:
    name: str
    field: FieldDefinition
    x0: float
    x1: float

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


def _header_columns(line: Line, children: list[FieldDefinition]) -> list[_Column]:
    columns: list[_Column] = []
    taken: list[tuple[int, int]] = []
    for child in children:
        for alias in aliases_for(child):
            match = next(
                (
                    m
                    for m in alias_regex(alias).finditer(line.text)
                    if not any(m.start() < e and m.end() > s for s, e in taken)
                ),
                None,
            )
            if match is None:
                continue
            located = span(line, match.start(), match.end())
            if located.bbox is None:
                continue
            taken.append((match.start(), match.end()))
            columns.append(_Column(child.name, child, located.bbox.x0, located.bbox.x1))
            break
    return sorted(columns, key=lambda c: c.x0)


def _assign(words: tuple[Word, ...], columns: list[_Column]) -> dict[str, list[Word]]:
    cells: dict[str, list[Word]] = {c.name: [] for c in columns}
    for word in words:
        overlapping = [c for c in columns if word.bbox.x0 < c.x1 and word.bbox.x1 > c.x0]
        if overlapping:
            target = overlapping[0]
        else:
            cx = (word.bbox.x0 + word.bbox.x1) / 2
            # Words left of every column belong to the first (usually description) column.
            target = (
                columns[0] if cx < columns[0].x0 else min(columns, key=lambda c: abs(c.center - cx))
            )
        cells[target.name].append(word)
    return cells


class TableExtractor:
    info = ProviderInfo(name="table-extractor", version="1", method="table")

    def assess(self, ctx: ExtractionContext) -> Suitability:
        arrays = [p for p, f in ctx.schema.flatten() if f.type is FieldType.ARRAY]
        density = max((layout.table_density for layout in ctx.layouts), default=0.0)
        return Suitability(
            can_handle=bool(arrays),
            expected_confidence=0.8 if density > 0.2 else 0.5,
            estimated_cost=0.0,
            reasons=(f"{len(arrays)} line-item fields", f"table density {density:.2f}"),
        )

    async def extract(self, ctx: ExtractionContext) -> list[FieldCandidate]:
        out: list[FieldCandidate] = []
        for path, field in ctx.schema.flatten():
            if field.type is not FieldType.ARRAY or field.item is None:
                continue
            if field.item.type is not FieldType.OBJECT:
                continue
            for layout in ctx.layouts:
                out.extend(self._table(path, field.item.children, layout))
        return out

    def _table(
        self, path: str, children: list[FieldDefinition], layout: PageLayout
    ) -> list[FieldCandidate]:
        lines = layout.lines
        header_index, columns = None, []
        for i, line in enumerate(lines):
            cols = _header_columns(line, children)
            if len(cols) >= 2:
                header_index, columns = i, cols
                break
        if header_index is None:
            return []
        heights = [ln.bbox.height for ln in lines if ln.bbox.height > 0]
        median_h = statistics.median(heights) if heights else 0.01
        numeric = [c for c in columns if c.field.type in _NUMERIC]
        out: list[FieldCandidate] = []
        previous = lines[header_index]
        for line in lines[header_index + 1 :]:
            if _STOP.match(line.text) or line.bbox.y0 - previous.bbox.y1 > _MAX_ROW_GAP * median_h:
                break
            previous = line
            cells = _assign(line.words, columns)
            if numeric and not any(cells[c.name] for c in numeric):
                continue  # not a row (e.g. a wrapped description line)
            row_id = new_row_id()
            for column in columns:
                words = cells[column.name]
                if not words:
                    continue
                confidences = [w.confidence for w in words if w.confidence is not None]
                value_span = Span(
                    text=" ".join(w.text for w in words),
                    bbox=union_all([w.bbox for w in words]),
                    word_confidence=sum(confidences) / len(confidences) if confidences else None,
                )
                out.append(
                    candidate(
                        path=f"{path}.{column.name}",
                        row_id=row_id,
                        field=column.field,
                        span=value_span,
                        layout=layout,
                        method_key="table",
                        info=self.info,
                    )
                )
        return out
