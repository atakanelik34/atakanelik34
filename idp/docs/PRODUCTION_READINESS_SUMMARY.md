# Production readiness — summary

One page; the detail and evidence are in [GO_LIVE_READINESS.md](GO_LIVE_READINESS.md).

**Status: CONDITIONAL GO** (2026-10-05). All code-level blockers are closed. Four
conditions need production infrastructure, real data or your approval.

## Closed (verified by executed tests)

| Area | Result |
|---|---|
| F1 upload rate limit, F2 retry bound, F14 no duplicate ERP posting, F15 large-document budgets | PASS (regression tests and validation suite) |
| F6 backup/restore | PASS. The drill restores 11,087 rows and 1,896 objects identically (15/15). The backup defect (no objects backed up) was fixed. |
| F12 duplicate-upload race | PASS (409, deterministic) |
| ClamAV behaviour | PASS with a real clamd: clean accepted; EICAR rejected and audited; the API-key path is scanned; fail closed (503) when clamd is down; scanning happens before storage |
| Scanned documents | Always reviewed (`part:ocr`); total = subtotal + tax holds ERP posting until a person resolves it |
| Confidence thresholds | `tax`/`subtotal` at 0.85 (template). On the synthetic set: 0 wrong values at or above threshold, 0 silent errors |
| F17 OCR amounts | Fixed. Scanned field accuracy 76.7 % → 95.9 % on the synthetic set; in the running worker, wrong scanned values 21 → 1 |
| CI | `idp-ci` green on runs 26–36; real Tesseract runs inside the default image |
| Final regression (2026-10-05) | Backend 377 passed / 1 skipped; validation 38 passed / 2 xfailed (F13, F16); ruff, format, mypy clean; frontend 28 tests and build pass; Compose E2E 9/9, ClamAV 6/6, stack benchmark 99 documents/min |

## Conditions before production

1. **ClamAV egress.** Allow HTTPS to `database.clamav.net` (or set a private mirror or
   proxy) and monitor signature age. Exact settings: GO_LIVE_READINESS §0.
2. **Staging restore with production-like data.** Run
   [STAGING_RESTORE_RUNBOOK.md](STAGING_RESTORE_RUNBOOK.md). It needs a read-only
   production database role, a new staging database and bucket, and S3 credentials.
3. **Real-data calibration.** Run at least 300 anonymised documents (at least 30
   observed errors) through `test_accuracy_benchmark` + `calibrate.py`. Republish
   existing tenant schemas with the 0.85 thresholds.
4. **Merge to `main` and run `idp-validation` once** (scheduled and dispatched
   workflows only run from the default branch). Not done: awaiting approval.

## Open, not blocking

* **F13:** abandoned LLM calls are not metered. Fix before enabling an LLM.
* **F16:** a memory-bomb render is retried 3× instead of failing once.
* **F7:** `/metrics` is open without `METRICS_TOKEN`; set the token.
* **No real scanned document** is in the test fixtures; add one (see `backend/tests/fixtures/scans/README.md`).
* **Line items on scans:** cell accuracy is 34 %; scans are always reviewed.
* **One residual scanned `tax` error** (confidence 0.33, below threshold).

## Later (inspected, not implemented)

* **Horizontal scaling.**
  * Already safe for more workers: the API and workers are stateless, jobs use
    Postgres leases with fencing, and the queue (arq/Redis) is shared. The sweeper
    is cluster-wide.
  * Connection budget: each process holds up to 20 Postgres connections
    (`DATABASE_POOL_SIZE` 10 + overflow 10) against `max_connections` 100. More than
    about 3 worker replicas plus the API needs PgBouncer, smaller pools, or more
    connections.
  * Circuit breakers are per process.
  * Redis needs HA (AOF already on).
  * Measured today: one worker (2 CPU, 4 jobs) processes about 100 synthetic
    documents per minute. Scaling has not been measured.
* **LLM extraction.**
  * The adapters, policy enforcement, allow-lists and metering already exist.
  * Requirements:
    * a configured provider (Ollama/OpenAI-compatible/Anthropic) and tenant policy (`allow_llm`, processing mode);
    * F13 fixed;
    * quality, latency and cost measured with the same ground-truth harness;
    * a DPA review before any cloud provider.
