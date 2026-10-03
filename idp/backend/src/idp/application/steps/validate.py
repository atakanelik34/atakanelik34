"""`validate` step: deterministic rules for every part (no AI involved)."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from idp.application.validation import validate_part
from idp.application.workflows import StepContext, StepResult
from idp.domain.validation import NEEDS_HUMAN
from idp.infrastructure.db.models import DocumentPage, DocumentPart


class ValidateStep:
    key = "validate"

    async def run(self, ctx: StepContext) -> StepResult:
        parts = (
            await ctx.session.scalars(select(DocumentPart).where(DocumentPart.job_id == ctx.job.id))
        ).all()
        pages = (
            await ctx.session.scalars(
                select(DocumentPage).where(DocumentPage.document_id == ctx.document.id)
            )
        ).all()
        outcomes: Counter[str] = Counter()
        parts_needing_review = 0
        for part in parts:
            issues = await validate_part(ctx.session, part, pages)
            outcomes.update(i.outcome.value for i in issues)
            if any(i.outcome in NEEDS_HUMAN for i in issues):
                parts_needing_review += 1
        return StepResult(
            provider="rule-engine",
            provider_version="1",
            metrics={
                "parts": len(parts),
                "parts_needing_review": parts_needing_review,
                **{k.lower(): v for k, v in sorted(outcomes.items())},
            },
        )
