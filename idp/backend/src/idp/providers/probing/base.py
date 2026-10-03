"""Document probe contract.

A probe answers cheap structural questions before any digitization runs: how
many pages, their geometry, and whether each page already carries a text layer
(native PDF) or is image-only (needs OCR in phase 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class TextLayer(StrEnum):
    NATIVE = "native"  # every page has extractable text
    PARTIAL = "partial"  # some pages do (hybrid / scanned with cover sheet)
    NONE = "none"  # image-only: OCR required


@dataclass(frozen=True, slots=True)
class PageProbe:
    page_number: int
    width: float
    height: float
    unit: str
    rotation: int
    has_text_layer: bool
    char_count: int


@dataclass(frozen=True, slots=True)
class ProbeResult:
    pages: tuple[PageProbe, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text_layer(self) -> TextLayer:
        with_text = sum(1 for p in self.pages if p.has_text_layer)
        if with_text == 0:
            return TextLayer.NONE
        return TextLayer.NATIVE if with_text == len(self.pages) else TextLayer.PARTIAL


class DocumentProber(Protocol):
    name: str
    version: str

    async def probe(self, path: Path, mime_type: str) -> ProbeResult: ...
