"""Persistence of page geometry (layout JSON) and page images in object storage.

Geometry is large and read-mostly, so it lives next to the original; Postgres
keeps per-page summaries and the keys.
"""

from __future__ import annotations

import json
import uuid

from idp.domain.geometry import PageLayout
from idp.infrastructure.storage.base import (
    ObjectStorageProvider,
    build_key,
    read_bytes,
    write_bytes,
)

MAX_LAYOUT_BYTES = 32 * 1024 * 1024


def layout_key(tenant_id: uuid.UUID, document_id: uuid.UUID, page: int, job_id: uuid.UUID) -> str:
    # Keyed by job: a reprocess never overwrites geometry an older run referenced.
    return build_key(
        tenant_id, "documents", str(document_id), "jobs", str(job_id), f"layout-{page}.json"
    )


def image_key(tenant_id: uuid.UUID, document_id: uuid.UUID, page: int, job_id: uuid.UUID) -> str:
    return build_key(
        tenant_id, "documents", str(document_id), "jobs", str(job_id), f"page-{page}.webp"
    )


async def save_layout(storage: ObjectStorageProvider, key: str, layout: PageLayout) -> None:
    data = json.dumps(layout.to_dict(), separators=(",", ":")).encode()
    await write_bytes(storage, key, data, content_type="application/json")


async def load_layout(storage: ObjectStorageProvider, key: str) -> PageLayout:
    raw = await read_bytes(storage, key, max_bytes=MAX_LAYOUT_BYTES)
    return PageLayout.from_dict(json.loads(raw))
