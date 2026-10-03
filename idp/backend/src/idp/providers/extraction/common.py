"""Confidence model and helpers shared by the deterministic extractors.

confidence = method base x normalisation factor x OCR word confidence (if OCR).
Bases are deliberately conservative and are calibrated against ground truth by
the evaluation framework (phase 8); thresholds per field decide review.
"""

from __future__ import annotations

import re

from idp.domain.geometry import PageLayout
from idp.domain.normalization import Normalized, normalize
from idp.domain.taxonomy import FieldDefinition
from idp.providers.extraction.base import FieldCandidate, ProviderInfo
from idp.providers.extraction.text_index import Span

BASE_CONFIDENCE = {
    "regex": 0.92,
    "key_value:right": 0.85,
    "key_value:below": 0.75,
    "table": 0.82,
    "heuristic:first_line": 0.45,
}
NORMALIZATION_FAILED_FACTOR = 0.5


def humanize(name: str) -> str:
    return name.replace("_", " ")


def aliases_for(field: FieldDefinition) -> list[str]:
    """Explicit aliases plus the humanised field name, longest first."""
    values = {a.strip().lower() for a in field.aliases if a.strip()}
    values.add(humanize(field.name).lower())
    return sorted(values, key=len, reverse=True)


def alias_regex(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w]){re.escape(alias)}(?![\w])", re.IGNORECASE)


def score(base_key: str, normalized: Normalized, span: Span, bonus: float = 0.0) -> float:
    value = BASE_CONFIDENCE[base_key] + bonus
    if not normalized.ok:
        value *= NORMALIZATION_FAILED_FACTOR
    if span.word_confidence is not None:
        value *= span.word_confidence
    return round(max(0.0, min(value, 0.99)), 3)


def candidate(
    *,
    path: str,
    row_id: str,
    field: FieldDefinition,
    span: Span,
    layout: PageLayout,
    method_key: str,
    info: ProviderInfo,
    bonus: float = 0.0,
) -> FieldCandidate:
    normalized = normalize(span.text, field)
    return FieldCandidate(
        path=path,
        row_id=row_id,
        raw_text=span.text,
        value=normalized.value,
        normalized=normalized.ok,
        confidence=score(method_key, normalized, span, bonus),
        page=layout.page_number,
        bbox=span.bbox,
        method=f"{method_key}:{layout.source.value}",
        provider=info.name,
        provider_version=info.version,
        reason=normalized.reason,
    )
