"""OCR engine port. Engines run in the worker process (not the parser pool):
local engines spawn their own process; cloud engines (future) make HTTP calls
after the processing-policy check."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from idp.domain.geometry import Line


@dataclass(frozen=True, slots=True)
class OCRPage:
    lines: tuple[Line, ...]
    mean_confidence: float | None


class OCREngine(Protocol):
    name: str
    version: str
    is_mock: bool
    locality: str  # "local" | "cloud"

    async def recognize(self, image: Path, *, page_number: int) -> OCRPage: ...
