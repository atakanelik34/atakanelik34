"""Extraction contract shared by every provider (deterministic, local LLM, cloud LLM).

Providers return `FieldCandidate`s with provenance; merging and persistence are
provider-agnostic. Nothing downstream knows which provider produced a value.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from idp.domain.geometry import BBox, Line, PageLayout
from idp.domain.taxonomy import SchemaDefinition

SCALAR_ROW = ""  # row_id for non-repeating fields


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    name: str
    version: str
    method: str  # regex | key_value | table | heuristic | llm
    locality: str = "local"
    is_mock: bool = False


@dataclass(frozen=True, slots=True)
class FieldCandidate:
    path: str
    row_id: str
    raw_text: str
    value: Any
    normalized: bool
    confidence: float
    page: int | None
    bbox: BBox | None
    method: str
    provider: str
    provider_version: str
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    schema: SchemaDefinition
    layouts: tuple[PageLayout, ...]  # pages of the part, in order

    def lines(self) -> list[tuple[PageLayout, Line]]:
        return [(layout, line) for layout in self.layouts for line in layout.lines]


@dataclass(frozen=True, slots=True)
class Suitability:
    can_handle: bool
    expected_confidence: float
    estimated_cost: float
    reasons: tuple[str, ...]


class ExtractionProvider(Protocol):
    info: ProviderInfo

    def assess(self, ctx: ExtractionContext) -> Suitability: ...

    async def extract(self, ctx: ExtractionContext) -> list[FieldCandidate]: ...


def new_row_id() -> str:
    """Stable identity for a repeating-group row (never an array index)."""
    return f"r_{uuid.uuid4().hex[:12]}"
