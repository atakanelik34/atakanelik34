"""Label/value extraction: find a field's alias, take the value to its right or below.

Overlapping alias matches are resolved globally per line: a match contained in
a longer match of another field ("date" inside "due date") is discarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from idp.domain.geometry import Line, PageLayout
from idp.domain.normalization import TYPE_VALUE_PATTERNS
from idp.domain.taxonomy import FieldDefinition, FieldType
from idp.providers.extraction.base import (
    SCALAR_ROW,
    ExtractionContext,
    FieldCandidate,
    ProviderInfo,
    Suitability,
)
from idp.providers.extraction.common import alias_regex, aliases_for, candidate
from idp.providers.extraction.text_index import Span, span

_SEPARATORS = " \t:#=-–—|"
_MAX_BELOW_GAP_LINES = 2.5


@dataclass(frozen=True, slots=True)
class _Match:
    path: str
    field: FieldDefinition
    start: int
    end: int
    alias: str


def _scalar_fields(ctx: ExtractionContext) -> list[tuple[str, FieldDefinition]]:
    return [
        (path, f)
        for path, f in ctx.schema.flatten()
        if "[]" not in path and f.type not in (FieldType.ARRAY, FieldType.OBJECT)
    ]


def _value_in(text: str, field: FieldDefinition) -> tuple[int, int] | None:
    """Locate the value inside `text` (offsets), using the type's pattern if any."""
    pattern = TYPE_VALUE_PATTERNS.get(field.type)
    if pattern is None:
        stripped = text.strip(_SEPARATORS)
        if not stripped:
            return None
        start = text.index(stripped)
        return start, start + len(stripped)
    matches = [m for m in re.finditer(pattern, text) if m.group(0).strip()]
    if not matches:
        return None
    match = matches[0]
    if field.type in (FieldType.DECIMAL, FieldType.INTEGER):
        # "VAT 19% 199.50": a number followed by % is a rate, not the amount.
        amounts = [m for m in matches if not text[m.end() :].lstrip().startswith("%")]
        match = amounts[0] if amounts else match
    start = match.start() + (len(match.group(0)) - len(match.group(0).lstrip()))
    return start, match.end() - (len(match.group(0)) - len(match.group(0).rstrip()))


def _line_matches(line: Line, fields: list[tuple[str, FieldDefinition]]) -> list[_Match]:
    found: list[_Match] = []
    for path, field in fields:
        for alias in aliases_for(field):
            for m in alias_regex(alias).finditer(line.text):
                found.append(_Match(path, field, m.start(), m.end(), alias))
    # Drop matches strictly inside a longer match (e.g. "date" within "due date").
    return [
        m
        for m in found
        if not any(
            o is not m
            and o.start <= m.start
            and m.end <= o.end
            and (o.end - o.start) > (m.end - m.start)
            for o in found
        )
    ]


class KeyValueExtractor:
    info = ProviderInfo(name="key-value-extractor", version="1", method="key_value")

    def assess(self, ctx: ExtractionContext) -> Suitability:
        fields = _scalar_fields(ctx)
        words = sum(len(layout.words) for layout in ctx.layouts)
        return Suitability(
            can_handle=bool(fields) and words > 0,
            expected_confidence=0.8,
            estimated_cost=0.0,
            reasons=(f"{len(fields)} scalar fields", f"{words} words of text"),
        )

    async def extract(self, ctx: ExtractionContext) -> list[FieldCandidate]:
        fields = _scalar_fields(ctx)
        out: list[FieldCandidate] = []
        for layout in ctx.layouts:
            lines = layout.lines
            for index, line in enumerate(lines):
                matches = _line_matches(line, fields)
                ends = sorted({m.start for m in matches})
                for m in matches:
                    # The value region ends where the next label on the line begins.
                    region_end = next((s for s in ends if s >= m.end), len(line.text))
                    bonus = min(0.05, len(m.alias) / 200)
                    located = _value_in(line.text[m.end : region_end], m.field)
                    if located is not None and m.field.extraction_hints.position != "below":
                        start, end = m.end + located[0], m.end + located[1]
                        out.append(
                            self._make(m, span(line, start, end), layout, "key_value:right", bonus)
                        )
                        continue
                    if m.field.extraction_hints.position == "right":
                        continue
                    below = self._below(lines, index, line, m)
                    if below is not None:
                        out.append(self._make(m, below, layout, "key_value:below", bonus))
        if ctx.layouts:
            out.extend(self._first_line_heuristic(ctx.layouts[0], fields, out))
        return out

    def _below(self, lines: list[Line], index: int, label_line: Line, m: _Match) -> Span | None:
        label = span(label_line, m.start, m.end)
        if label.bbox is None:
            return None
        height = max(label_line.bbox.height, 1e-3)
        for nxt in lines[index + 1 : index + 4]:
            if nxt.bbox.y0 - label_line.bbox.y1 > _MAX_BELOW_GAP_LINES * height:
                break
            # Words of the next line horizontally under the label (with some slack).
            slack = label.bbox.width
            idx = [
                i
                for i, w in enumerate(nxt.words)
                if w.bbox.x1 >= label.bbox.x0 - slack / 2 and w.bbox.x0 <= label.bbox.x1 + slack * 3
            ]
            if not idx:
                continue
            text_start = sum(len(w.text) + 1 for w in nxt.words[: idx[0]])
            text_end = sum(len(w.text) + 1 for w in nxt.words[: idx[-1] + 1]) - 1
            located = _value_in(nxt.text[text_start:text_end], m.field)
            if located is None:
                continue
            return span(nxt, text_start + located[0], text_start + located[1])
        return None

    def _make(
        self, m: _Match, value: Span, layout: PageLayout, key: str, bonus: float
    ) -> FieldCandidate:
        return candidate(
            path=m.path,
            row_id=SCALAR_ROW,
            field=m.field,
            span=value,
            layout=layout,
            method_key=key,
            info=self.info,
            bonus=bonus,
        )

    def _first_line_heuristic(
        self,
        layout: PageLayout,
        fields: list[tuple[str, FieldDefinition]],
        found: list[FieldCandidate],
    ) -> list[FieldCandidate]:
        """For string fields hinted `below` with no label found (typically the issuer
        name at the top of page 1): propose the first line, at low confidence."""
        if not layout.lines:
            return []
        have = {c.path for c in found}
        out = []
        first = layout.lines[0]
        for path, field in fields:
            if path in have or field.type is not FieldType.STRING:
                continue
            if field.extraction_hints.position != "below":
                continue
            out.append(
                candidate(
                    path=path,
                    row_id=SCALAR_ROW,
                    field=field,
                    span=span(first, 0, len(first.text)),
                    layout=layout,
                    method_key="heuristic:first_line",
                    info=self.info,
                )
            )
        return out
