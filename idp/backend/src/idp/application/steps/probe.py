"""`probe` step: page count, geometry and text-layer presence per page.

Deterministic, no AI. Results are upserted per (document, page), so re-running
the step after a crash converges to the same rows.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import delete

from idp.application.workflows import StepContext, StepResult
from idp.infrastructure.db.models import DocumentPage
from idp.infrastructure.storage.base import ObjectStorageProvider
from idp.providers.probing.base import DocumentProber


class ProbeStep:
    key = "probe"

    def __init__(
        self, *, storage: ObjectStorageProvider, prober: DocumentProber, tmp_dir: str | None
    ) -> None:
        self._storage = storage
        self._prober = prober
        self._tmp_dir = tmp_dir

    async def run(self, ctx: StepContext) -> StepResult:
        document = ctx.document
        with tempfile.TemporaryDirectory(prefix="idp-probe-", dir=self._tmp_dir) as workdir:
            path = Path(workdir) / "original"
            with path.open("wb") as fh:
                await self._storage.download(document.storage_key, fh)
            result = await self._prober.probe(path, document.detected_mime_type)

        await ctx.session.execute(
            delete(DocumentPage).where(DocumentPage.document_id == document.id)
        )
        ctx.session.add_all(
            DocumentPage(
                tenant_id=document.tenant_id,
                document_id=document.id,
                page_number=page.page_number,
                width=page.width,
                height=page.height,
                unit=page.unit,
                rotation=page.rotation,
                has_text_layer=page.has_text_layer,
                char_count=page.char_count,
            )
            for page in result.pages
        )
        document.page_count = result.page_count
        return StepResult(
            provider=self._prober.name,
            provider_version=self._prober.version,
            metrics={
                "page_count": result.page_count,
                "pages_with_text": sum(1 for p in result.pages if p.has_text_layer),
                "text_layer": result.text_layer.value,
            },
        )
