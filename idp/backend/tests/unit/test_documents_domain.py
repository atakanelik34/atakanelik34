import random
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from idp.application.documents import decode_cursor, encode_cursor
from idp.domain.documents import (
    DEFAULT_FILENAME,
    JPEG,
    PDF,
    PNG,
    TIFF,
    detect_mime_type,
    sanitize_filename,
)
from idp.domain.errors import ErrorCategory, ValidationError
from idp.domain.lifecycle import JobStatus
from idp.domain.retry import RetryPolicy
from tests.fixtures import files


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (files.native_pdf(), PDF),
        (b"\n\n  junk before header %PDF-1.4 ...", PDF),
        (files.png(), PNG),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", JPEG),
        (files.multipage_tiff(), TIFF),
        (b"MM\x00*\x00\x00\x00\x08", TIFF),
        (b"PK\x03\x04 docx is a zip", None),
        (b"<html><script>", None),
        (b"MZ\x90\x00 windows executable", None),
        (b"", None),
    ],
)
def test_mime_detection_uses_content_not_names(data: bytes, expected: str | None) -> None:
    assert detect_mime_type(data[:2048]) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("invoice.pdf", "invoice.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\x\\scan 01.tif", "scan 01.tif"),
        ("bad\x00name\r\n.pdf", "badname.pdf"),
        (".hidden", "hidden"),
        ("", DEFAULT_FILENAME),
        (None, DEFAULT_FILENAME),
        ("/", DEFAULT_FILENAME),
    ],
)
def test_filenames_are_display_only_and_safe(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_long_filenames_keep_their_extension() -> None:
    name = sanitize_filename("a" * 400 + ".pdf")
    assert len(name) == 255
    assert name.endswith(".pdf")


POLICY = RetryPolicy(max_attempts=3, base_seconds=10, max_seconds=60, jitter_ratio=0.0)


def test_backoff_is_exponential_and_capped() -> None:
    assert [POLICY.backoff_seconds(n) for n in (1, 2, 3, 4, 5)] == [10, 20, 40, 60, 60]


def test_backoff_jitter_stays_within_bounds() -> None:
    policy = RetryPolicy(max_attempts=3, base_seconds=10, max_seconds=60, jitter_ratio=0.2)
    rng = random.Random(7)  # noqa: S311 — deterministic jitter for the test
    assert all(8 <= policy.backoff_seconds(1, rng=rng) <= 12 for _ in range(200))


@pytest.mark.parametrize(
    ("category", "attempts", "expected"),
    [
        (ErrorCategory.PROVIDER_ERROR, 1, JobStatus.RETRY_SCHEDULED),
        (ErrorCategory.SYSTEM_ERROR, 2, JobStatus.RETRY_SCHEDULED),
        (ErrorCategory.PROVIDER_ERROR, 3, JobStatus.DEAD_LETTERED),
        (ErrorCategory.DOCUMENT_ERROR, 1, JobStatus.FAILED),
        (ErrorCategory.CONFIGURATION_ERROR, 1, JobStatus.FAILED),
    ],
)
def test_retry_outcome(category: ErrorCategory, attempts: int, expected: JobStatus) -> None:
    assert POLICY.outcome(category, attempts) is expected


def test_cursor_roundtrip_and_rejects_garbage() -> None:
    created, doc_id = datetime(2026, 10, 3, 12, 0, tzinfo=UTC), uuid4()
    assert decode_cursor(encode_cursor(created, doc_id)) == (created, doc_id)
    with pytest.raises(ValidationError):
        decode_cursor("not-a-cursor")
