import asyncio
import io
import struct

import pytest

from idp.domain.documents import ScanStatus
from idp.infrastructure.scanning import ClamdScanner, ScannerUnavailableError

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


async def _fake_clamd(reply_for) -> tuple[asyncio.Server, int, list[bytes]]:  # type: ignore[no-untyped-def]
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        data = b""
        while True:
            (size,) = struct.unpack(">I", await reader.readexactly(4))
            if size == 0:
                break
            data += await reader.readexactly(size)
        received.append(data)
        writer.write(reply_for(data))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], received


async def test_clamd_instream_verdicts() -> None:
    server, port, received = await _fake_clamd(
        lambda d: b"stream: Eicar-Test-Signature FOUND\0" if b"EICAR" in d else b"stream: OK\0"
    )
    async with server:
        scanner = ClamdScanner("127.0.0.1", port, timeout_seconds=5)
        scanner.CHUNK = 16  # exercise chunking
        clean = io.BytesIO(b"%PDF-1.7 harmless content" * 3)
        verdict = await scanner.scan(clean)
        assert verdict.status is ScanStatus.CLEAN
        assert clean.tell() == 0  # rewound for the caller
        infected = await scanner.scan(io.BytesIO(EICAR))
        assert (infected.status, infected.signature) == (
            ScanStatus.INFECTED,
            "Eicar-Test-Signature",
        )
    assert received[0] == b"%PDF-1.7 harmless content" * 3


async def test_clamd_errors_fail_closed() -> None:
    server, port, _ = await _fake_clamd(lambda d: b"INSTREAM size limit exceeded. ERROR\0")
    async with server:
        with pytest.raises(ScannerUnavailableError):
            await ClamdScanner("127.0.0.1", port, timeout_seconds=5).scan(io.BytesIO(b"x"))
    with pytest.raises(ScannerUnavailableError):
        await ClamdScanner("127.0.0.1", 1, timeout_seconds=1).scan(io.BytesIO(b"x"))
