"""`digitize` step: text + geometry for every page (native first, OCR if needed).

Re-runnable: page summaries are upserted, and geometry/images are written under
job-scoped keys, so a re-run overwrites only its own artefacts.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import select

from idp.application.workflows import StepContext, StepResult
from idp.domain.geometry import OcrStatus, TextSource
from idp.infrastructure.db.models import DocumentPage
from idp.infrastructure.layout_store import image_key, layout_key, save_layout
from idp.infrastructure.storage.base import ObjectStorageProvider
from idp.providers.digitization.local import HybridDigitizer


class DigitizeStep:
    key = "digitize"

    def __init__(
        self, *, storage: ObjectStorageProvider, digitizer: HybridDigitizer, tmp_dir: str | None
    ) -> None:
        self._storage = storage
        self._digitizer = digitizer
        self._tmp_dir = tmp_dir

    async def run(self, ctx: StepContext) -> StepResult:
        document, job = ctx.document, ctx.job
        with tempfile.TemporaryDirectory(prefix="idp-digitize-", dir=self._tmp_dir) as tmp:
            workdir = Path(tmp)
            original = workdir / "original"
            with original.open("wb") as fh:
                await self._storage.download(document.storage_key, fh)
            result = await self._digitizer.digitize(original, document.detected_mime_type, workdir)

            keys: dict[int, tuple[str, str]] = {}
            for page in result.pages:
                number = page.layout.page_number
                l_key = layout_key(document.tenant_id, document.id, number, job.id)
                i_key = image_key(document.tenant_id, document.id, number, job.id)
                await save_layout(self._storage, l_key, page.layout)
                with page.view_image.open("rb") as fh:
                    await self._storage.put(
                        i_key, fh, content_type="image/webp", size=page.view_image.stat().st_size
                    )
                keys[number] = (l_key, i_key)

        existing = {
            p.page_number: p
            for p in (
                await ctx.session.scalars(
                    select(DocumentPage).where(DocumentPage.document_id == document.id)
                )
            ).all()
        }
        for page in result.pages:
            layout = page.layout
            row = existing.get(layout.page_number)
            if row is None:
                row = DocumentPage(
                    tenant_id=document.tenant_id,
                    document_id=document.id,
                    page_number=layout.page_number,
                    has_text_layer=layout.source is TextSource.NATIVE,
                    char_count=len(layout.text),
                )
                ctx.session.add(row)
            row.width, row.height, row.unit, row.rotation = (
                layout.width,
                layout.height,
                layout.unit,
                layout.rotation,
            )
            row.text_source = layout.source.value
            row.ocr_status = page.ocr_status.value
            row.text_quality = layout.text_quality
            row.ocr_confidence = layout.ocr_confidence
            row.language = layout.language
            row.table_density = layout.table_density
            row.word_count = len(layout.words)
            row.layout_key, row.image_key = keys[layout.page_number]
            row.image_width, row.image_height = page.view_size
            row.digitized_by_job_id = job.id
        document.page_count = len(result.pages)

        by_source = {s.value: 0 for s in TextSource}
        for page in result.pages:
            by_source[page.layout.source.value] += 1
        ocr_confidences = [
            p.layout.ocr_confidence for p in result.pages if p.layout.ocr_confidence is not None
        ]
        return StepResult(
            provider=self._digitizer.name,
            provider_version=self._digitizer.version,
            metrics={
                "pages": len(result.pages),
                "pages_native": by_source["native"],
                "pages_ocr": by_source["ocr"],
                "pages_unreadable": by_source["none"],
                "ocr_engine": result.ocr_engine or "not_configured",
                "ocr_engine_is_mock": result.ocr_engine_is_mock,
                "ocr_not_configured_pages": sum(
                    1 for p in result.pages if p.ocr_status is OcrStatus.NOT_CONFIGURED
                ),
                "ocr_mean_confidence": round(sum(ocr_confidences) / len(ocr_confidences), 3)
                if ocr_confidences
                else None,
                "ocr_seconds": result.ocr_seconds,
                "max_table_density": max((p.layout.table_density for p in result.pages), default=0),
            },
        )
