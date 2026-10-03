"""Malware scanning port.

Phase 2 ships only `NoMalwareScanner`, which records `not_scanned` truthfully
rather than pretending files are clean. A ClamAV (clamd) adapter is phase 12.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol

from idp.domain.documents import ScanStatus


@dataclass(frozen=True, slots=True)
class ScanVerdict:
    status: ScanStatus
    scanner: str
    signature: str | None = None


class MalwareScanner(Protocol):
    name: str

    async def scan(self, data: BinaryIO) -> ScanVerdict: ...


class NoMalwareScanner:
    """Explicitly no scanning. Documents are marked `not_scanned`."""

    name = "none"

    async def scan(self, data: BinaryIO) -> ScanVerdict:
        return ScanVerdict(status=ScanStatus.NOT_SCANNED, scanner=self.name)
