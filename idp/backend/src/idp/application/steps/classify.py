"""`classify` step: page-level classification and splitting into document parts.

Deterministic rules from each published schema (phase 9 adds an LLM classifier
as a routed fallback for unknown pages). Parts are written per job, so earlier
runs keep their own classification.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select

from idp.application.policy import PolicyResolver
from idp.application.taxonomy import published_candidates
from idp.application.workflows import StepContext, StepResult
from idp.domain.classification import PartProposal, TypeCandidate, classify_pages, split
from idp.infrastructure.db.models import DocumentPage, DocumentPart, ProviderCall
from idp.infrastructure.layout_store import load_layout
from idp.infrastructure.storage.base import ObjectStorageProvider
from idp.providers.llm.classifier import LLMClassifier
from idp.providers.llm.gateway import CallRecord

CLASSIFIER_NAME = "rule-classifier"
CLASSIFIER_VERSION = "1"


class PartStatus:
    CLASSIFIED = "classified"
    UNCLASSIFIED = "unclassified"


class ClassifyStep:
    key = "classify"

    def __init__(
        self,
        *,
        storage: ObjectStorageProvider,
        llm: LLMClassifier | None = None,
        policy: PolicyResolver | None = None,
    ) -> None:
        if (llm is None) != (policy is None):
            raise ValueError("the LLM fallback needs a policy resolver")
        self._storage = storage
        self._llm = llm
        self._policy = policy

    async def _fallback(
        self,
        ctx: StepContext,
        proposals: list[PartProposal],
        texts: dict[int, str],
        candidates: list[TypeCandidate],
    ) -> dict[int, str]:
        """Ask an LLM about unclassified parts. Returns {proposal index: provider}."""
        if self._llm is None or self._policy is None or not candidates:
            return {}
        if all(p.candidate for p in proposals):
            return {}
        policy = await self._policy.resolve(ctx.session, ctx.document.tenant_id)
        calls: list[CallRecord] = []
        resolved: dict[int, str] = {}
        for index, proposal in enumerate(proposals):
            if proposal.candidate is not None:
                continue
            text = "\n".join(
                texts.get(n, "") for n in range(proposal.page_start, proposal.page_end + 1)
            )
            answer = await self._llm.classify(text, candidates, policy=policy, sink=calls)
            if answer is not None:
                proposal.candidate = answer.candidate
                proposal.confidence = answer.confidence
                proposal.reasons = [
                    f"rule classifier found no type; LLM fallback ({answer.provider}) chose "
                    f"{answer.candidate.key}",
                    *proposal.reasons,
                ]
                resolved[index] = answer.provider
        for call in calls:
            ctx.session.add(
                ProviderCall(
                    tenant_id=ctx.document.tenant_id,
                    document_id=ctx.document.id,
                    job_id=ctx.job.id,
                    provider=call.provider,
                    model=call.model,
                    locality=call.locality,
                    purpose=call.purpose,
                    status=call.status,
                    attempts=call.attempts,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    cost=call.cost,
                    latency_ms=call.latency_ms,
                    error_code=call.error_code,
                )
            )
        return resolved

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
        llm_resolved = await self._fallback(ctx, proposals, dict(texts), candidates)

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
                    classifier=f"llm:{llm_resolved[index]}"
                    if index in llm_resolved
                    else f"{CLASSIFIER_NAME}@{CLASSIFIER_VERSION}",
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
                "llm_classified_parts": len(llm_resolved),
            },
        )
