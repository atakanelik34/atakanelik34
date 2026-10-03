import io
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from moto import mock_aws

from idp.domain.errors import NotFoundError, ValidationError
from idp.domain.health import HealthStatus
from idp.infrastructure.storage.base import build_key, validate_key
from idp.infrastructure.storage.local import LocalFilesystemStorage
from idp.infrastructure.storage.s3 import S3ObjectStorage

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.parametrize(
    "key",
    [
        "../etc/passwd",
        "/abs/path",
        "tenants/../../x",
        "Tenants/UPPER",
        "a//b",
        "a/b/",
        "a\\b",
        "",
        "x" * 600,
    ],
)
def test_unsafe_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValidationError):
        validate_key(key)


def test_build_key_is_tenant_prefixed() -> None:
    key = build_key(TENANT, "documents", "abc", "original")
    assert key == f"tenants/{TENANT}/documents/abc/original"


@pytest.fixture
def local(tmp_path: Path) -> LocalFilesystemStorage:
    return LocalFilesystemStorage(str(tmp_path), signing_secret="s" * 32, url_base="/dl")


async def test_local_roundtrip(local: LocalFilesystemStorage) -> None:
    key = build_key(TENANT, "documents", "d1", "original")
    stored = await local.put(key, io.BytesIO(b"%PDF-1.7"), content_type="application/pdf", size=8)
    assert stored.size == 8
    assert await local.exists(key)
    sink = io.BytesIO()
    assert await local.download(key, sink) == 8
    assert sink.getvalue() == b"%PDF-1.7"
    await local.delete(key)
    assert not await local.exists(key)
    with pytest.raises(NotFoundError):
        await local.download(key, io.BytesIO())


async def test_local_put_rejects_size_mismatch(local: LocalFilesystemStorage) -> None:
    key = build_key(TENANT, "documents", "d9", "original")
    with pytest.raises(ValidationError):
        await local.put(key, io.BytesIO(b"longer than declared"), content_type="x/y", size=4)
    assert not await local.exists(key)


async def test_local_signed_url_verifies_and_expires(local: LocalFilesystemStorage) -> None:
    key = build_key(TENANT, "documents", "d1", "original")
    url = urlparse(await local.signed_url(key, expires_in=60))
    params = parse_qs(url.query)
    expires, signature = int(params["expires"][0]), params["signature"][0]
    assert local.verify(key, expires, signature)
    assert not local.verify(key, expires, "0" * 64)
    assert not local.verify(build_key(TENANT, "other"), expires, signature)
    expired = int(time.time()) - 1
    assert not local.verify(key, expired, local.sign(key, expired))


async def test_local_health(local: LocalFilesystemStorage) -> None:
    assert (await local.check()).status is HealthStatus.UP


@pytest.fixture
def s3() -> Iterator[S3ObjectStorage]:
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="docs")
        yield S3ObjectStorage(
            bucket="docs",
            region="us-east-1",
            access_key_id="test",
            secret_access_key="test",
        )


async def test_s3_roundtrip_and_signed_url(s3: S3ObjectStorage) -> None:
    key = build_key(TENANT, "documents", "d2", "original")
    await s3.put(key, io.BytesIO(b"hello"), content_type="text/plain", size=5)
    assert await s3.exists(key)
    sink = io.BytesIO()
    assert await s3.download(key, sink) == 5
    assert sink.getvalue() == b"hello"
    url = await s3.signed_url(key, expires_in=60, filename='inv"oice.pdf')
    assert "X-Amz-Signature" in url
    assert "invoice.pdf" in url
    await s3.delete(key)
    assert not await s3.exists(key)
    with pytest.raises(NotFoundError):
        await s3.download(key, io.BytesIO())


async def test_s3_health_reports_missing_bucket() -> None:
    with mock_aws():
        storage = S3ObjectStorage(
            bucket="missing",
            region="us-east-1",
            access_key_id="test",
            secret_access_key="test",
        )
        assert (await storage.check()).status is HealthStatus.DOWN
        await storage.ensure_bucket()
        assert (await storage.check()).status is HealthStatus.UP
