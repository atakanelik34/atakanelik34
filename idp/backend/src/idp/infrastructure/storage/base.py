"""Object storage port.

Binary documents live in object storage; Postgres stores only metadata and an
opaque storage key. Keys are always generated server-side (see `build_key`) and
validated, so user-controlled input can never become a path.
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol

from idp.domain.errors import ValidationError
from idp.domain.health import ComponentHealth

# Lower-case segments of [a-z0-9-_.], no "..", no leading slash.
_KEY_PATTERN = re.compile(r"^(?!.*\.\.)[a-z0-9][a-z0-9\-_.]*(/[a-z0-9][a-z0-9\-_.]*)*$")
MAX_KEY_LENGTH = 512


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size: int
    content_type: str
    etag: str | None = None


class ObjectStorageProvider(Protocol):
    name: str

    async def put(
        self, key: str, data: BinaryIO, *, content_type: str, size: int
    ) -> StoredObject: ...

    async def download(self, key: str, dest: BinaryIO) -> int:
        """Stream the object into `dest`; return bytes written. Never buffers it whole."""
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def signed_url(
        self, key: str, *, expires_in: int, filename: str | None = None
    ) -> str: ...

    async def check(self) -> ComponentHealth: ...


def validate_key(key: str) -> str:
    if len(key) > MAX_KEY_LENGTH or not _KEY_PATTERN.fullmatch(key):
        raise ValidationError("Invalid storage key")
    return key


def build_key(tenant_id: uuid.UUID, *segments: str) -> str:
    """Build a tenant-prefixed storage key, e.g. tenants/<id>/documents/<id>/original."""
    return validate_key("/".join(["tenants", str(tenant_id), *segments]))


class _LimitedSink(io.BytesIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self._limit = limit

    def write(self, data: Any) -> int:
        if self.tell() + len(data) > self._limit:
            raise ValidationError("Stored object exceeds the expected size")
        return super().write(data)


async def read_bytes(storage: ObjectStorageProvider, key: str, *, max_bytes: int) -> bytes:
    """Read a small object (layout JSON, metadata) fully, with a hard size cap."""
    sink = _LimitedSink(max_bytes)
    await storage.download(key, sink)
    return sink.getvalue()


async def write_bytes(
    storage: ObjectStorageProvider, key: str, data: bytes, *, content_type: str
) -> StoredObject:
    return await storage.put(key, io.BytesIO(data), content_type=content_type, size=len(data))
