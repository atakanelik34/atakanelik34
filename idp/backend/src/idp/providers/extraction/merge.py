"""Provider-agnostic merging of candidates into one result per field."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from idp.domain.taxonomy import FieldType, SchemaDefinition
from idp.providers.extraction.base import SCALAR_ROW, FieldCandidate

MAX_ALTERNATIVES = 3


@dataclass(slots=True)
class MergedField:
    path: str
    row_id: str
    best: FieldCandidate | None  # None => missing
    alternatives: list[FieldCandidate] = field(default_factory=list)


def _array_paths(schema: SchemaDefinition) -> list[str]:
    return [p for p, f in schema.flatten() if f.type is FieldType.ARRAY]


def merge(schema: SchemaDefinition, candidates: list[FieldCandidate]) -> list[MergedField]:
    merged: list[MergedField] = []
    scalars: dict[str, list[FieldCandidate]] = defaultdict(list)
    rows_by_array: dict[str, dict[tuple[str, str], list[FieldCandidate]]] = defaultdict(dict)
    arrays = _array_paths(schema)

    for c in candidates:
        array = next((a for a in arrays if c.path.startswith(f"{a}.")), None)
        if array is None:
            scalars[c.path].append(c)
        else:
            rows_by_array[array].setdefault((c.provider, c.row_id), []).append(c)

    for path, definition in schema.flatten():
        if "[]" in path or definition.type in (FieldType.ARRAY, FieldType.OBJECT):
            continue
        found = sorted(scalars.get(path, []), key=lambda c: c.confidence, reverse=True)
        if not found:
            merged.append(MergedField(path=path, row_id=SCALAR_ROW, best=None))
            continue
        best, seen = found[0], {repr(found[0].value)}
        alternatives: list[FieldCandidate] = []
        for other in found[1:]:
            if repr(other.value) not in seen and len(alternatives) < MAX_ALTERNATIVES:
                seen.add(repr(other.value))
                alternatives.append(other)
        merged.append(
            MergedField(path=path, row_id=SCALAR_ROW, best=best, alternatives=alternatives)
        )

    # Rows: take the provider whose rows have the highest mean confidence.
    for rows in rows_by_array.values():
        by_provider: dict[str, list[list[FieldCandidate]]] = defaultdict(list)
        for (provider, _row), cells in rows.items():
            by_provider[provider].append(cells)

        def quality(row_sets: list[list[FieldCandidate]]) -> float:
            values = [c.confidence for cells in row_sets for c in cells]
            return sum(values) / len(values) if values else 0.0

        best_rows = max(by_provider.values(), key=quality)
        for cells in best_rows:
            for c in cells:
                merged.append(MergedField(path=c.path, row_id=c.row_id, best=c))
    return merged
