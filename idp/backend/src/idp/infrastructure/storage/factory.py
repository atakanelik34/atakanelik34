from __future__ import annotations

import hashlib
import hmac

from idp.config import Settings, StorageBackend
from idp.domain.errors import ConfigurationError
from idp.infrastructure.storage.base import ObjectStorageProvider
from idp.infrastructure.storage.local import LocalFilesystemStorage
from idp.infrastructure.storage.s3 import S3ObjectStorage


def create_storage(settings: Settings) -> ObjectStorageProvider:
    if settings.storage_backend is StorageBackend.S3:
        if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
            raise ConfigurationError("S3 storage requires access key credentials")
        return S3ObjectStorage(
            bucket=settings.storage_bucket,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id.get_secret_value(),
            secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            endpoint_url=settings.s3_endpoint_url,
            public_endpoint_url=settings.s3_public_endpoint_url,
            server_side_encryption=settings.s3_sse,
        )
    # Derive a purpose-specific key so URL signatures can never double as JWTs.
    signing_secret = hmac.new(
        settings.jwt_secret.get_secret_value().encode(), b"idp:local-storage-urls", hashlib.sha256
    ).hexdigest()
    return LocalFilesystemStorage(
        settings.storage_local_root,
        signing_secret=signing_secret,
        url_base=f"{settings.api_prefix}/storage/local",
    )
