"""`review` step: pause the job for a human when validation requires it.

Creates one review task per job with the reasons (failed/uncertain rules per
part) and raises `AwaitingHumanReview`. When a reviewer approves, the review
service marks this step SUCCEEDED and requeues the job, which then continues
with the remaining steps from its checkpoints.
"""

from __future__ import annotations

from sqlalchemy import select

from idp.application.audit import ActorType, AuditAction, AuditEntity, record_audit
from idp.application.workflows import AwaitingHumanReview, StepContext, StepResult
from idp.domain.validation import NEEDS_HUMAN
from idp.infrastructure.db.models import ReviewTask, ValidationResult

OPEN_STATUSES = ("open", "in_progress")


class ReviewStep:
    key = "review"

    async def run(self, ctx: StepContext) -> StepResult:
        blocking = (
            await ctx.session.scalars(
                select(ValidationResult)
                .where(
                    ValidationResult.job_id == ctx.job.id,
                    ValidationResult.outcome.in_([o.value for o in NEEDS_HUMAN]),
                )
                .order_by(ValidationResult.rule_id)
            )
        ).all()
        if not blocking:
            return StepResult(
                provider="review-gate", provider_version="1", metrics={"review": "not_required"}
            )

        reasons = [
            {
                "part_id": str(r.part_id),
                "rule_id": r.rule_id,
                "rule_type": r.rule_type,
                "outcome": r.outcome,
                "fields": r.field_paths,
                "message": r.message,
            }
            for r in blocking
        ]
        existing = await ctx.session.scalar(
            select(ReviewTask).where(
                ReviewTask.job_id == ctx.job.id, ReviewTask.status.in_(OPEN_STATUSES)
            )
        )
        if existing is None:
            task = ReviewTask(
                tenant_id=ctx.document.tenant_id,
                document_id=ctx.document.id,
                job_id=ctx.job.id,
                status="open",
                reasons=reasons,
            )
            ctx.session.add(task)
            await ctx.session.flush()
            record_audit(
                ctx.session,
                action=AuditAction.REVIEW_REQUESTED,
                entity_type=AuditEntity.REVIEW_TASK,
                entity_id=task.id,
                tenant_id=ctx.document.tenant_id,
                actor_type=ActorType.SYSTEM,
                after={"document_id": str(ctx.document.id), "reasons": len(reasons)},
            )
        raise AwaitingHumanReview(reasons)
