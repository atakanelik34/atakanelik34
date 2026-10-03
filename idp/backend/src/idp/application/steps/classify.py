"""`classify` step: page-level classification and splitting into document parts.

Deterministic rules from each published schema (phase 9 adds an LLM classifier
as a routed fallback for unknown pages). Parts are written per job, so earlier
runs keep their own classification.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select

from idp.application.taxonomy import published_candidates
from idp.application.workflows import StepContext, StepResult
from idp.domain.classification import classify_pages, split
from idp.infrastructure.db.models import DocumentPage, DocumentPart
from idp.infrastructure.layout_store import load_layout
from idp.infrastructure.storage.base import ObjectStorageProvider

CLASSIFIER_NAME = "rule-classifier"
CLASSIFIER_VERSION = "1"


class PartStatus:
    CLASSIFIED = "classified"
    UNCLASSIFIED = "unclassified"


class ClassifyStep:
    key = "classify"

    def __init__(self, *, storage: ObjectStorageProvider) -> None:
        self._storage = storage

    async def run(self, ctx: StepContext) -> StepResult:
        document = ctx.document
        pages = (
            await ctx.session.scalars(
                select(DocumentPage)
                .where(DocumentPage.document_id == document.id)
                .order_by(DocumentPage.page_number)
            )
        ).all()
        layouts = await asyncio.gather(
            *(load_layout(self._storage, p.layout_key) for p in pages if p.layout_key)
        )
        texts = [(layout.page_number, layout.text) for layout in layouts]
        candidates = await published_candidates(
            ctx.session, tenant_id=document.tenant_id, project_id=document.project_id
        )
        classified = classify_pages(texts, candidates)
        proposals = split(classified)

        await ctx.session.execute(delete(DocumentPart).where(DocumentPart.job_id == ctx.job.id))
        for index, proposal in enumerate(proposals):
            ctx.session.add(
                DocumentPart(
                    tenant_id=document.tenant_id,
                    document_id=document.id,
                    job_id=ctx.job.id,
                    part_index=index,
                    page_start=proposal.page_start,
                    page_end=proposal.page_end,
                    document_type_id=proposal.candidate.document_type_id
                    if proposal.candidate
                    else None,
                    schema_version_id=proposal.candidate.schema_version_id
                    if proposal.candidate
                    else None,
                    classification_confidence=proposal.confidence,
                    classifier=f"{CLASSIFIER_NAME}@{CLASSIFIER_VERSION}",
                    classification_reasons=proposal.reasons[:50],
                    status=PartStatus.CLASSIFIED if proposal.candidate else PartStatus.UNCLASSIFIED,
                )
            )
        return StepResult(
            provider=CLASSIFIER_NAME,
            provider_version=CLASSIFIER_VERSION,
            metrics={
                "parts": len(proposals),
                "types": [p.candidate.key if p.candidate else "unknown" for p in proposals],
                "unclassified_parts": sum(1 for p in proposals if p.candidate is None),
                "candidate_types": len(candidates),
            },
        )
