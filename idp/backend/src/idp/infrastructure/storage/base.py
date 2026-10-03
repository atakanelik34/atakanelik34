"""Object storage port.

Binary documents live in object storage; Postgres stores only metadata and an
opaque storage key. Keys are always generated server-side (see `build_key`) and
validated, so user-controlled input can never become a path.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import BinaryIO, Protocol

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

    async def get(self, key: str) -> bytes: ...

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
