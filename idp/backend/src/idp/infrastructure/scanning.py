"""Malware scanning port.

`NoMalwareScanner` records `not_scanned` truthfully rather than pretending
files are clean. `ClamdScanner` streams the file to clamd (INSTREAM); if the
scanner is configured but unreachable, uploads fail closed (503) — a file is
never accepted as clean without a verdict.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from idp.domain.documents import ScanStatus
from idp.domain.errors import ProviderError


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


class ScannerUnavailableError(ProviderError):
    code = "malware_scanner_unavailable"
    http_status = 503  # fail closed: the upload is refused, try again later


class ClamdScanner:
    """clamd over TCP using the INSTREAM protocol (length-prefixed chunks)."""

    name = "clamav"
    CHUNK = 64 * 1024

    def __init__(self, host: str, port: int = 3310, *, timeout_seconds: float = 60.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_seconds

    async def _exchange(self, data: BinaryIO) -> bytes:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            writer.write(b"zINSTREAM\0")
            data.seek(0)
            while chunk := await asyncio.to_thread(data.read, self.CHUNK):
                writer.write(struct.pack(">I", len(chunk)) + chunk)
                await writer.drain()
            writer.write(struct.pack(">I", 0))
            await writer.drain()
            return await reader.readuntil(b"\0")
        finally:
            writer.close()
            await writer.wait_closed()

    async def scan(self, data: BinaryIO) -> ScanVerdict:
        try:
            raw = await asyncio.wait_for(self._exchange(data), self._timeout)
        except (
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as exc:
            raise ScannerUnavailableError("Malware scanner unavailable") from exc
        finally:
            data.seek(0)
        reply = raw.rstrip(b"\0").decode("utf-8", "replace").strip()
        if reply.endswith("OK"):
            return ScanVerdict(status=ScanStatus.CLEAN, scanner=self.name)
        if reply.endswith("FOUND"):
            signature = reply.removeprefix("stream:").removesuffix("FOUND").strip()
            return ScanVerdict(
                status=ScanStatus.INFECTED, scanner=self.name, signature=signature[:200]
            )
        # e.g. "INSTREAM size limit exceeded. ERROR": no verdict, so not accepted.
        raise ScannerUnavailableError("Malware scanner returned no verdict")
