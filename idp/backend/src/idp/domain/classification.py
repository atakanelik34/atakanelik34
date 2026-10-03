"""Deterministic page classification and document splitting.

Rule classifier: for each page, every published document type is scored by the
share of its keywords present (negative keywords subtract). The best type above
its `min_score` wins; otherwise the page is unknown. Splitting groups
contiguous pages of the same type into parts, starting a new part when a page
carries a first-page marker ("Page 1 of 3", a type's own first-page phrases).
Every decision records human-readable reasons.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from idp.domain.taxonomy import ClassificationRules

_PAGE_NUMBERING = re.compile(
    r"\b(?:page|seite|sayfa|página|pagina)\s*(\d{1,4})\s*(?:of|von|/|de|di|\|)\s*\d{1,4}\b", re.I
)


def page_ordinal(text: str) -> int | None:
    """The k in "Page k of N" (several languages), if the page states it."""
    match = _PAGE_NUMBERING.search(text)
    return int(match.group(1)) if match else None


CONTINUATION_DISCOUNT = 0.85


def normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", folded)


def _contains(haystack: str, needle: str) -> bool:
    phrase = normalize_text(needle).strip()
    if not phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", haystack) is not None


@dataclass(frozen=True, slots=True)
class TypeCandidate:
    document_type_id: uuid.UUID
    key: str
    schema_version_id: uuid.UUID
    rules: ClassificationRules


@dataclass(frozen=True, slots=True)
class PageClassification:
    page_number: int
    candidate: TypeCandidate | None
    confidence: float
    reasons: tuple[str, ...]
    starts_document: bool


@dataclass(slots=True)
class PartProposal:
    page_start: int
    page_end: int
    candidate: TypeCandidate | None
    confidence: float
    reasons: list[str] = field(default_factory=list)


def score(text: str, rules: ClassificationRules) -> tuple[float, list[str]]:
    if not rules.keywords:
        return 0.0, []
    hits = [k for k in rules.keywords if _contains(text, k)]
    negatives = [k for k in rules.negative_keywords if _contains(text, k)]
    # Three distinct keywords are strong evidence; more do not add certainty.
    value = min(1.0, len(hits) / min(3, len(rules.keywords))) - 0.5 * len(negatives)
    reasons = [f"keyword '{k}'" for k in hits] + [f"negative keyword '{k}'" for k in negatives]
    return max(0.0, round(value, 3)), reasons


def classify_pages(
    page_texts: list[tuple[int, str]], candidates: list[TypeCandidate]
) -> list[PageClassification]:
    results: list[PageClassification] = []
    for number, raw in page_texts:
        text = normalize_text(raw)
        # Explicit numbering is the strongest signal: "page 1 of N" starts a
        # document, "page k of N" (k > 1) continues one, whatever markers say.
        ordinal = page_ordinal(text)
        best: tuple[float, TypeCandidate, list[str]] | None = None
        for candidate in candidates:
            value, reasons = score(text, candidate.rules)
            if best is None or value > best[0]:
                best = (value, candidate, reasons)
        if best is not None and best[0] >= best[1].rules.min_score:
            value, candidate, reasons = best
            marker = any(_contains(text, m) for m in candidate.rules.first_page_markers)
            starts = ordinal == 1 if ordinal is not None else marker
            extra = ["first-page marker"] if marker else []
            if ordinal is not None:
                extra.append(f"page numbering {ordinal}")
            results.append(
                PageClassification(
                    page_number=number,
                    candidate=candidate,
                    confidence=value,
                    reasons=tuple(reasons + extra),
                    starts_document=starts,
                )
            )
        else:
            results.append(
                PageClassification(
                    page_number=number,
                    candidate=None,
                    confidence=0.0,
                    reasons=("no document type matched",)
                    if text.strip()
                    else ("no readable text",),
                    starts_document=ordinal == 1,
                )
            )
    return results


def split(pages: list[PageClassification]) -> list[PartProposal]:
    """Group pages into logical documents (parts)."""
    parts: list[PartProposal] = []
    for page in pages:
        current = parts[-1] if parts else None
        if current is not None and not page.starts_document:
            same_type = (
                page.candidate is not None
                and current.candidate is not None
                and (page.candidate.document_type_id == current.candidate.document_type_id)
            )
            # An unclassified page with no start marker continues the previous document
            # (e.g. terms & conditions on page 2 of an invoice).
            continuation = page.candidate is None and current.candidate is not None
            if same_type or continuation or (page.candidate is None and current.candidate is None):
                current.page_end = page.page_number
                if same_type:
                    current.confidence = min(current.confidence, page.confidence)
                elif continuation:
                    current.confidence = round(current.confidence * CONTINUATION_DISCOUNT, 3)
                    current.reasons.append(f"page {page.page_number}: continuation")
                continue
        parts.append(
            PartProposal(
                page_start=page.page_number,
                page_end=page.page_number,
                candidate=page.candidate,
                confidence=page.confidence,
                reasons=[f"page {page.page_number}: {r}" for r in page.reasons],
            )
        )
    return parts
