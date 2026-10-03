"""Local filesystem storage — development and tests only (refused in production)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from idp.domain.errors import InternalError, NotFoundError
from idp.domain.health import ComponentHealth, HealthStatus
from idp.infrastructure.storage.base import StoredObject, validate_key

_CHUNK = 1024 * 1024


class LocalFilesystemStorage:
    name = "local"

    def __init__(self, root: str, *, signing_secret: str, url_base: str) -> None:
        self._root = Path(root).resolve()
        self._secret = signing_secret.encode()
        self._url_base = url_base.rstrip("/")

    def _path(self, key: str) -> Path:
        path = (self._root / validate_key(key)).resolve()
        # Defense in depth: key validation already forbids traversal.
        if not path.is_relative_to(self._root):
            raise InternalError("Storage key escapes storage root")
        return path

    async def put(self, key: str, data: BinaryIO, *, content_type: str, size: int) -> StoredObject:
        path = self._path(key)

        def _write() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.md5(usedforsecurity=False)
            tmp = path.with_suffix(path.suffix + ".partial")
            with tmp.open("wb") as fh:
                while chunk := data.read(_CHUNK):
                    digest.update(chunk)
                    fh.write(chunk)
            tmp.replace(path)
            return digest.hexdigest()

        etag = await asyncio.to_thread(_write)
        return StoredObject(key=key, size=size, content_type=content_type, etag=etag)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise NotFoundError("Object not found") from exc

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    def sign(self, key: str, expires_at: int) -> str:
        message = f"{key}:{expires_at}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def verify(self, key: str, expires_at: int, signature: str) -> bool:
        if expires_at < int(time.time()):
            return False
        return hmac.compare_digest(self.sign(key, expires_at), signature)

    async def signed_url(self, key: str, *, expires_in: int, filename: str | None = None) -> str:
        validate_key(key)
        expires_at = int(time.time()) + expires_in
        signature = self.sign(key, expires_at)
        return f"{self._url_base}/{quote(key)}?expires={expires_at}&signature={signature}"

    async def check(self) -> ComponentHealth:
        started = time.perf_counter()

        def _probe() -> None:
            self._root.mkdir(parents=True, exist_ok=True)
            probe = self._root / ".healthcheck"
            probe.write_bytes(b"ok")
            probe.unlink()

        try:
            await asyncio.to_thread(_probe)
        except OSError as exc:
            return ComponentHealth(
                name="storage", status=HealthStatus.DOWN, detail=type(exc).__name__
            )
        return ComponentHealth(
            name="storage",
            status=HealthStatus.UP,
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"backend": self.name},
        )
