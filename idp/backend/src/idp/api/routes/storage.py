"""Serves signed URLs for the local filesystem storage backend (development only).

With S3/MinIO, signed URLs point at the object store directly and this router
is not mounted. Authorization happens when the URL is issued; the HMAC
signature + expiry is the capability.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from idp.api.deps import ContainerDep
from idp.domain.errors import AuthorizationError
from idp.infrastructure.storage.local import LocalFilesystemStorage

router = APIRouter(prefix="/storage/local", tags=["storage"], include_in_schema=False)


@router.get("/{key:path}")
async def download(
    key: str,
    container: ContainerDep,
    expires: Annotated[int, Query()],
    signature: Annotated[str, Query(min_length=64, max_length=64)],
) -> FileResponse:
    storage = container.storage
    if not isinstance(storage, LocalFilesystemStorage) or not storage.verify(
        key, expires, signature
    ):
        raise AuthorizationError("Invalid or expired download link")
    # FileResponse streams from disk in chunks.
    return FileResponse(
        storage.path_for(key),
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment"},
    )
