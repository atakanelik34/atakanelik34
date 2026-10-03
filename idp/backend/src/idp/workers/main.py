"""Worker composition root (arq).

Run with:  arq idp.workers.main.WorkerSettings

Workers are stateless and horizontally scalable: every job loads its state from
Postgres by id, claims it with a lease, and checkpoints per step. Retry policy
lives in the database (arq's own retries are disabled), and a cluster-wide
sweeper recovers lost messages and dead workers.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings

from idp.application.jobs import JobRunner, JobScheduler, JobSweeper
from idp.application.outbox import OutboxRelay
from idp.application.policy import DbPolicyResolver, default_policy
from idp.application.steps.action import ActionStep, ApproveActionsStep
from idp.application.steps.classify import ClassifyStep
from idp.application.steps.digitize import DigitizeStep
from idp.application.steps.enrich import EnrichStep
from idp.application.steps.extract import ExtractStep
from idp.application.steps.probe import ProbeStep
from idp.application.steps.review import ReviewStep
from idp.application.steps.validate import ValidateStep
from idp.application.workflows import StepHandler
from idp.config import Settings, get_settings
from idp.infrastructure.db.session import create_engine, create_session_factory
from idp.infrastructure.logging import configure_logging, get_logger
from idp.infrastructure.queue.jobs import PROCESS_JOB_FUNCTION, ArqJobQueue
from idp.infrastructure.queue.presence import PresencePublisher
from idp.infrastructure.queue.redis import WorkerHeartbeat, arq_redis_settings, create_redis
from idp.infrastructure.storage.factory import create_storage
from idp.providers.actions.factory import create_action_providers, create_webhook_sender
from idp.providers.catalog import extraction_providers
from idp.providers.digitization.local import HybridDigitizer
from idp.providers.enrichment.factory import (
    close_enrichment_providers,
    create_enrichment_providers,
)
from idp.providers.llm.classifier import LLMClassifier
from idp.providers.llm.factory import create_llm_gateway
from idp.providers.ocr.factory import create_ocr_engine
from idp.providers.ocr.tesseract import TesseractOCREngine
from idp.providers.probing.local import LocalDocumentProber
from idp.providers.resilience import BreakerRegistry

log = get_logger(__name__)

_settings = get_settings()


async def build_handlers(settings: Settings, ctx: dict[str, Any]) -> dict[str, StepHandler]:
    prober = LocalDocumentProber(
        workers=settings.worker_max_jobs,
        timeout_seconds=settings.probe_timeout_seconds,
        max_pages=settings.probe_max_pages,
        memory_limit_mb=settings.probe_memory_limit_mb,
    )
    ocr = create_ocr_engine(settings)
    if isinstance(ocr, TesseractOCREngine):
        await ocr.detect_version()
    digitizer = HybridDigitizer(
        ocr=ocr,
        workers=settings.worker_max_jobs,
        timeout_seconds=settings.probe_timeout_seconds,
        max_pages=settings.probe_max_pages,
        memory_limit_mb=settings.probe_memory_limit_mb,
        render_dpi=settings.render_dpi,
        ocr_dpi=settings.ocr_dpi,
        min_native_quality=settings.native_text_min_quality,
    )
    ctx["closables"] = [prober, digitizer]
    gateway = create_llm_gateway(settings)
    ctx["llm_gateway"] = gateway
    policy = DbPolicyResolver(default_policy(settings))
    enrichment = create_enrichment_providers(settings)
    ctx["enrichment_providers"] = enrichment
    sender = create_webhook_sender(settings)
    ctx["webhook_sender"] = sender
    actions = create_action_providers(settings, sender)
    log.info("worker.llm_providers", providers=gateway.provider_names)
    log.info("worker.ocr_engine", engine=ocr.name if ocr else "not_configured")
    steps: list[StepHandler] = [
        ProbeStep(storage=ctx["storage"], prober=prober, tmp_dir=settings.worker_tmp_dir),
        DigitizeStep(storage=ctx["storage"], digitizer=digitizer, tmp_dir=settings.worker_tmp_dir),
        ClassifyStep(
            storage=ctx["storage"],
            llm=LLMClassifier(gateway) if gateway.provider_names else None,
            policy=policy if gateway.provider_names else None,
        ),
        ExtractStep(
            storage=ctx["storage"],
            providers=extraction_providers(settings, gateway),
            policy=policy,
            breakers=BreakerRegistry(
                failure_threshold=settings.breaker_failure_threshold,
                reset_after_seconds=settings.breaker_reset_seconds,
            ),
            provider_timeout_seconds=settings.provider_timeout_seconds,
        ),
        EnrichStep(
            providers=enrichment,
            policy=policy,
            timeout_seconds=settings.enrichment_timeout_seconds,
        ),
        ValidateStep(),
        ReviewStep(),
        ApproveActionsStep(providers=actions, policy=policy),
        ActionStep(providers=actions, policy=policy),
    ]
    return {step.key: step for step in steps}


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(_settings.log_level, _settings.log_format)
    # arq's CLI installs its own plain-text handler; route through ours only.
    logging.getLogger("arq").handlers.clear()

    engine = create_engine(_settings)
    session_factory = create_session_factory(engine)
    redis = create_redis(_settings)
    ctx.update(engine=engine, app_redis=redis, storage=create_storage(_settings))
    scheduler = JobScheduler(ArqJobQueue(redis), _settings)
    handlers = await build_handlers(_settings, ctx)
    ctx["runner"] = JobRunner(session_factory, scheduler, handlers, _settings)
    ctx["sweeper"] = JobSweeper(session_factory, scheduler, _settings)
    ctx["outbox"] = OutboxRelay(
        session_factory,
        ctx["webhook_sender"],
        batch_size=_settings.outbox_batch_size,
        max_attempts=_settings.outbox_max_attempts,
    )

    hostname = socket.gethostname()
    now = time.time()
    presence = PresencePublisher(
        redis,
        WorkerHeartbeat(
            worker_id=f"{hostname}:{os.getpid()}",
            hostname=hostname,
            pid=os.getpid(),
            version=_settings.pipeline_version,
            max_jobs=_settings.worker_max_jobs,
            started_at=now,
            last_seen_at=now,
        ),
        interval_seconds=_settings.worker_heartbeat_seconds,
    )
    await presence.start()
    ctx["presence"] = presence
    log.info("worker.started", worker_id=presence.worker_id)


async def shutdown(ctx: dict[str, Any]) -> None:
    presence: PresencePublisher = ctx["presence"]
    await presence.stop()
    for closable in ctx.get("closables", []):
        closable.close()
    if "llm_gateway" in ctx:
        await ctx["llm_gateway"].aclose()
    if "enrichment_providers" in ctx:
        await close_enrichment_providers(ctx["enrichment_providers"])
    if "webhook_sender" in ctx:
        await ctx["webhook_sender"].aclose()
    await ctx["app_redis"].aclose()
    await ctx["engine"].dispose()
    log.info("worker.stopped", worker_id=presence.worker_id)


async def process_job(ctx: dict[str, Any], job_id: str) -> str:
    runner: JobRunner = ctx["runner"]
    outcome = await runner.run(uuid.UUID(job_id))
    return outcome.value


async def relay_outbox(ctx: dict[str, Any]) -> int:
    relay: OutboxRelay = ctx["outbox"]
    return await relay.relay()


async def sweep_jobs(ctx: dict[str, Any]) -> int:
    sweeper: JobSweeper = ctx["sweeper"]
    return await sweeper.sweep()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        # max_tries=1: retries are decided by the database-backed policy, not arq.
        # keep_result=0: a finished message must never block a later re-dispatch.
        func(
            process_job,
            name=PROCESS_JOB_FUNCTION,
            max_tries=1,
            keep_result=0,
            timeout=_settings.job_lease_seconds,
        ),
    ]
    # Cluster-wide (arq de-duplicates cron runs by id): one sweep per tick.
    cron_jobs: ClassVar[list[Any]] = [
        cron(sweep_jobs, second={0, 30}, run_at_startup=True, keep_result=0, max_tries=1),
        # Event delivery; claims are leased with SKIP LOCKED, so overlap is harmless.
        cron(relay_outbox, second={5, 15, 25, 35, 45, 55}, keep_result=0, max_tries=1),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings: RedisSettings = arq_redis_settings(_settings)
    max_jobs = _settings.worker_max_jobs
    job_timeout = _settings.job_lease_seconds
    health_check_interval = 30
