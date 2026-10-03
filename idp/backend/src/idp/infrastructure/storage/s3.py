"""S3-compatible storage adapter (AWS S3, MinIO, Ceph RGW, …).

boto3 is synchronous; calls run in a worker thread so the event loop is never
blocked.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from idp.domain.errors import NotFoundError, ProviderError
from idp.domain.health import ComponentHealth, HealthStatus
from idp.infrastructure.storage.base import StoredObject, validate_key

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
_BUCKET_MISSING_CODES = frozenset({"404", "NoSuchBucket", "NotFound"})


class S3ObjectStorage:
    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint_url: str | None = None,
        public_endpoint_url: str | None = None,
        server_side_encryption: str | None = None,
    ) -> None:
        self._bucket = bucket
        self._sse = server_side_encryption
        config = Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
            s3={"addressing_style": "path"},
        )
        common: dict[str, Any] = {
            "region_name": region,
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "config": config,
        }
        self._client: S3Client = boto3.client("s3", endpoint_url=endpoint_url, **common)
        # Signed URLs must be reachable by the browser, which may use a different
        # host than the API container (e.g. localhost:9000 vs minio:9000).
        self._presign_client: S3Client = (
            boto3.client("s3", endpoint_url=public_endpoint_url, **common)
            if public_endpoint_url
            else self._client
        )

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in _BUCKET_MISSING_CODES:
                    raise
                self._client.create_bucket(Bucket=self._bucket)

        try:
            await asyncio.to_thread(_ensure)
        except (BotoCoreError, ClientError) as exc:
            raise ProviderError("Unable to ensure storage bucket exists") from exc

    async def put(self, key: str, data: BinaryIO, *, content_type: str, size: int) -> StoredObject:
        validate_key(key)
        extra: dict[str, Any] = {"ContentType": content_type}
        if self._sse:
            extra["ServerSideEncryption"] = self._sse

        def _put() -> str | None:
            self._client.upload_fileobj(data, self._bucket, key, ExtraArgs=extra)
            head = self._client.head_object(Bucket=self._bucket, Key=key)
            return head.get("ETag", "").strip('"') or None

        try:
            etag = await asyncio.to_thread(_put)
        except (BotoCoreError, ClientError) as exc:
            raise ProviderError("Object storage write failed") from exc
        return StoredObject(key=key, size=size, content_type=content_type, etag=etag)

    async def get(self, key: str) -> bytes:
        validate_key(key)

        def _get() -> bytes:
            body: bytes = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            return body

        try:
            return await asyncio.to_thread(_get)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise NotFoundError("Object not found") from exc
            raise ProviderError("Object storage read failed") from exc
        except BotoCoreError as exc:
            raise ProviderError("Object storage read failed") from exc

    async def delete(self, key: str) -> None:
        validate_key(key)
        try:
            await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise ProviderError("Object storage delete failed") from exc

    async def exists(self, key: str) -> bool:
        validate_key(key)
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                return False
            raise ProviderError("Object storage lookup failed") from exc
        except BotoCoreError as exc:
            raise ProviderError("Object storage lookup failed") from exc
        return True

    async def signed_url(self, key: str, *, expires_in: int, filename: str | None = None) -> str:
        validate_key(key)
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if filename:
            safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        return await asyncio.to_thread(
            self._presign_client.generate_presigned_url,
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )

    async def check(self) -> ComponentHealth:
        started = time.perf_counter()
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        except (BotoCoreError, ClientError) as exc:
            return ComponentHealth(
                name="storage", status=HealthStatus.DOWN, detail=type(exc).__name__
            )
        return ComponentHealth(
            name="storage",
            status=HealthStatus.UP,
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"backend": self.name, "bucket": self._bucket},
        )
