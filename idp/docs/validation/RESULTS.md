# Validation results

## Fault-injection suite (`pytest -m validation`) — 2026-10-05T16:44:08.583526+00:00

| Scenario | Measurements |
|---|---|
| P13 accuracy dataset | {"documents": 84, "statuses": {"WAITING_FOR_HUMAN": 46, "COMPLETED": 35, "FAILED": 3}, "report": "calibration-report.json"} |
| 10 concurrent duplicate deliveries | {"deliveries": 5, "executions": 1} |
| 10 concurrent identical uploads | {"uploads": 20, "documents": 1, "jobs": 1, "statuses": ["201", "409"]} |
| 10 identical uploads: race losers (F12 fixed) | {"statuses": ["201", "409"], "documents": 1, "duplicate_rejections_audited": 12} |
| 11 dead-letter + replay | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "attempts": 3, "dead_letter_audited": true} |
| 11 non-retryable | {"attempts": 1} |
| 3 large PDF near limit | {"size_mb": 46.0, "upload_s": 0.518, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 17, "digitize": 5554, "enrich": 7, "extract": 13, "probe": 95, "validate": 9}, "wall_s": 5.82, "cpu_s": 5.8, "avg_cores_busy": 1.0, "baseline_rss_mib": 475, "peak_rss_mib": 544, "peak_scratch_mib": 0.0} |
| 3 over-limit PDF | {"size_mb": 85.4, "status": 413} |
| 4 high page count (100) | {"pages": 100, "size_mb": 0.04, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 78, "digitize": 10959, "enrich": 13, "extract": 80, "probe": 24, "validate": 16}, "wall_s": 11.32, "cpu_s": 11.23, "avg_cores_busy": 0.99, "baseline_rss_mib": 481, "peak_rss_mib": 482, "peak_scratch_mib": 0.0} |
| 4 high page count (500) | {"pages": 500, "size_mb": 0.18, "outcomes": ["waiting_for_review"], "step_ms": {"validate": 20, "classify": 532, "digitize": 54834, "extract": 364, "enrich": 12, "probe": 57}, "wall_s": 56.05, "cpu_s": 55.69, "avg_cores_busy": 0.99, "baseline_rss_mib": 438, "peak_rss_mib": 441, "peak_scratch_mib": 0.0} |
| 4 high page count (1000) | {"pages": 1000, "size_mb": 0.35, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 846, "digitize": 109522, "enrich": 9, "extract": 848, "probe": 309, "validate": 31}, "wall_s": 111.91, "cpu_s": 111.49, "avg_cores_busy": 1.0, "baseline_rss_mib": 442, "peak_rss_mib": 448, "peak_scratch_mib": 0.0} |
| 4 high page count (1200) | {"pages": 1200, "size_mb": 0.43, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 789, "digitize": 131305, "enrich": 7, "extract": 821, "probe": 139, "validate": 26}, "wall_s": 133.42, "cpu_s": 132.89, "avg_cores_busy": 1.0, "baseline_rss_mib": 448, "peak_rss_mib": 453, "peak_scratch_mib": 0.0} |
| 4 page limit exceeded | {"pages": 2001, "outcome": "failed", "error": "document_error"} |
| 4/F2 scanned pages vs lease | {"pages": 40, "simulated_ocr_s_per_page": 0.05, "digitize_s": 7.38, "non_ocr_s_per_page": 0.134, "max_pages_within_900s_lease_at_10s_ocr": 88, "max_pages_within_900s_lease_at_30s_ocr": 29, "outcomes": ["waiting_for_review"], "wall_s": 7.659} |
| 12 png decompression bomb | {"size_kb": 0.5, "outcomes": ["failed"], "final": "FAILED", "error": "document_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.09, "cpu_s": 0.06, "avg_cores_busy": 0.67, "baseline_rss_mib": 454, "peak_rss_mib": 454, "peak_scratch_mib": 0.0} |
| 12 giant page (render bomb) | {"size_kb": 0.6, "outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "final": "DEAD_LETTERED", "error": "internal_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.18, "cpu_s": 0.12, "avg_cores_busy": 0.67, "baseline_rss_mib": 456, "peak_rss_mib": 456, "peak_scratch_mib": 0.0} |
| 12 corrupted pdf | {"size_kb": 0.1, "outcomes": ["failed"], "final": "FAILED", "error": "document_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.23, "cpu_s": 0.2, "avg_cores_busy": 0.87, "baseline_rss_mib": 456, "peak_rss_mib": 456, "peak_scratch_mib": 0.0} |
| 13 CPU/RAM, 12 documents, 4 concurrent | {"documents": 12, "finals": ["waiting_for_review"], "retries": 0, "docs_per_min": 37.7, "wall_s": 19.11, "cpu_s": 39.41, "avg_cores_busy": 2.06, "baseline_rss_mib": 488, "peak_rss_mib": 608, "peak_scratch_mib": 37.4} |
| 12/F16 render memory bomb | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "final": "DEAD_LETTERED", "error": "internal_error", "attempts": 3} |
| 4/F15 render time budget (fixed) | {"pages": 80, "budget_s": 3, "outcomes": ["failed"], "final": "FAILED", "error": "processing_budget_exceeded", "wall_s": 3.103} |
| 5 slow OCR | {"pages": 4, "ocr_delay_s": 0.5, "wall_s": 2.767, "outcome": "waiting_for_review"} |
| 5 OCR transient failure | {"ocr_calls": 6, "recovered": true} |
| 6/8/9 OCR exceeds lease | {"lease_s": 2, "ocr_s_per_page": 0.8, "pages": 4, "ocr_calls": 8, "wasted_ocr_calls": 4, "outcomes": ["lost_lease", "waiting_for_review"], "finish_s": 3.985} |
| 6/8 heartbeat keeps lease (F15) | {"lease_s": 1, "heartbeat_s": 0.25, "ocr_calls": 4, "contenders": ["not_claimed", "not_claimed", "not_claimed", "not_claimed", "not_claimed"], "outcome": "waiting_for_review", "attempts": 1} |
| 8 lease expiry, no contention | {"outcome": "waiting_for_review"} |
| 9 sweeper re-dispatch | {"redispatched": 1} |
| 7 worker crash during OCR | {"steps": [["probe", "SUCCEEDED", null], ["digitize", "FAILED", "worker_lost"], ["digitize", "SUCCEEDED", null], ["classify", "SUCCEEDED", null], ["extract", "SUCCEEDED", null], ["enrich", "SUCCEEDED", null], ["validate", "SUCCEEDED", null], ["review", "WAITING", null]], "recovered": true} |
| 6/9 step always outlasts lease (F2 fixed) | {"claims": 3, "max_attempts": 3, "attempts": 3, "outcomes": ["killed", "killed", "killed", "dead_lettered", "not_claimed"], "final": "DEAD_LETTERED", "error": "lease_expired"} |
| 15 LLM timeouts retried | {"server_calls": 3, "attempts": 3, "outcome": "succeeded"} |
| 15 LLM retries vs provider timeout | {"attempt": {"error": "TimeoutError", "status": "timeout", "provider": "llm:ollama", "duration_ms": 1502}, "server_calls": 2, "job_wall_s": 1.863} |
| 15 abandoned LLM call metering | {"server_calls": 2, "provider_call_rows": 0} |
| 15 LLM breaker | {"per_document": ["failed", "failed", "circuit open after repeated failures", "circuit open after repeated failures"], "server_calls": 6} |
| 16 ERP timeout then success | {"outcomes": ["retry_scheduled", "succeeded"], "receiver_requests": 2, "distinct_keys": 1} |
| 16 ERP timeouts exhaust retries | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "run_status": "approved", "run_attempts": 3, "receiver_requests": 3, "document": "FAILED"} |
| 16 ERP rejection | {"run_status": "failed", "error": "http_400"} |
| 16 replay after ERP timeouts (F14 fixed) | {"requests": 4, "distinct_keys": 1, "runs": [["approved", 3], ["succeeded", 1]]} |
| 1 upload rate limit (F1 fixed) | {"limit_per_minute": 5, "statuses": [201, 201, 201, 201, 201, 429, 429, 429]} |
| 1 API limit bounds uploads | {"statuses": [201, 201, 201, 201, 201, 429, 429, 429, 429, 429, 429, 429, 429, 429]} |
| 2 concurrent upload flood | {"uploads": 200, "ok": 200, "other": [], "documents": 200, "jobs": 200, "dispatched": 200, "wall_s": 3.28, "uploads_per_s": 60.9, "p50_s": 2.844, "p95_s": 2.968, "max_s": 3.257, "mean_s": 2.831} |
| 14 tenant isolation under load (RLS forced) | {"requests": 230, "cross_tenant_reads": 60, "cross_tenant_leaks": 0, "list_leaks": 0, "jobs_processed_in_parallel": 10} |

## Live Compose stack (`stack_validation.py`) — 2026-10-05T17:09:39.862356+00:00

| Check | Result | Measurements |
|---|---|---|
| 2 concurrent upload flood | PASS | {"uploads": 125, "non_201": [], "uploads_per_s": 25.8, "p50_s": 3.06, "p95_s": 4.761, "max_s": 4.81} |
| 14 tenant isolation under load (API) | PASS | {"cross_reads": 75, "non_404": [], "list_leaks": 0} |
| 14 tenant isolation (database, runtime role idp_app) | PASS | {"no_context": 0, "alpha_context_foreign_rows": 0, "alpha_context_rows": 100, "audit_update_refused": true} |
| 3 large PDF accepted | PASS | {"size_mb": 46.0, "upload_s": 8.68} |
| 3 over-limit PDF refused at the edge | PASS | {"size_mb": 85.4, "status": 413} |
| 4 high page count processed | PASS | {"pages": 500, "status": "WAITING_FOR_HUMAN"} |
| OCR reads a scanned page (Tesseract, default image) | PASS | {"status": "WAITING_FOR_HUMAN", "source": "ocr", "ocr_confidence": 0.939} |
| 13 CPU/RAM under concurrent documents | PASS | {"documents": 103, "statuses": {"WAITING_FOR_HUMAN": 103}, "unfinished": 0, "processing_wall_s": 88.5, "docs_per_min": 69.9, "containers": {"api": {"peak_cpu_pct": 74.7, "mean_cpu_pct": 5.1, "peak_mem_mib": 168, "mem_limit_mib": 1024, "peak_mem_pct_of_limit": 16.4, "samples": 39}, "worker": {"peak_cpu_pct": 198.6, "mean_cpu_pct": 110.0, "peak_mem_mib": 615, "mem_limit_mib": 3072, "peak_mem_pct_of_limit": 20.0, "samples": 39}, "postgres": {"peak_cpu_pct": 85.6, "mean_cpu_pct": 8.1, "peak_mem_mib": 167, "mem_limit_mib": 2048, "peak_mem_pct_of_limit": 8.1, "samples": 39}, "frontend": {"peak_cpu_pct": 5.7, "mean_cpu_pct": 0.6, "peak_mem_mib": 50, "mem_limit_mib": 256, "peak_mem_pct_of_limit": 19.7, "samples": 39}}} |
| no failed or dead-lettered jobs | PASS | {"failed_or_dead": 0} |

## ClamAV (clamav_check.py) — 2026-10-05T17:09:56.619002+00:00

| Check | Result | Measurements |
|---|---|---|
| 1 clean upload accepted and scanned | PASS | {"status": 201, "scan_status": "clean"} |
| 2 EICAR-in-PDF rejected, audited, not stored | PASS | {"status": 422, "signature": "Eicar-Signature", "documents_before": 100, "documents_after": 100} |
| 3 API-key upload of EICAR rejected | PASS | {"status": 422} |
| 4 bare EICAR refused by type allow-list | PASS | {"status": 415} |
| 5 scanner unavailable: upload fails closed | PASS | {"status": 503, "code": "malware_scanner_unavailable"} |
| 6 scanner back: uploads accepted again | PASS | {"status": 201} |

## Backup/restore drill (restore_drill.py), small run — 2026-10-05T13:14:06.904457+00:00

Run: {"backup_dir": "drill-131242", "backup_bytes": 239769, "timings": {"backup_s": 1.1, "fresh_environment_s": 19.0, "restore_s": 6.7, "restart_s": 7.1}, "restore_output_tail": ["runtime role 'idp_app' updated", "restored from /home/user/atakanelik34/idp/backups/drill-131242; start the stack: docker compose up -d"]}

| Check | Result | Measurements |
|---|---|---|
| backup contains every stored object (originals and page images) | PASS | {"objects_in_bucket": 10, "objects_in_backup": 10} |
| database identical after restore (row counts + content hash, every table) | PASS | {"tables": 29, "rows": 193, "differing": [], "rows_tenants": 2, "rows_users": 2, "rows_documents": 4, "rows_processing_jobs": 4, "rows_review_tasks": 2, "rows_review_actions": 3, "rows_action_runs": 2, "rows_audit_logs": 41, "rows_document_types": 1, "rows_schema_versions": 1, "rows_connections": 1, "rows_api_keys": 1, "rows_processing_policies": 1} |
| object store identical after restore (SHA-256 per object) | PASS | {"objects": 10, "missing": [], "changed": []} |
| logins of both tenants work (password hashes restored) | PASS | {} |
| straight-through document restored with its executed action | PASS | {"status": "COMPLETED", "action": "succeeded"} |
| straight-through page image served from restored bucket | PASS | {"bytes": 33736} |
| reviewed document restored with its executed action | PASS | {"status": "COMPLETED", "action": "succeeded"} |
| reviewed page image served from restored bucket | PASS | {"bytes": 33140} |
| review decision and correction restored | PASS | {"field": "corrected", "task": "approved"} |
| audit trail restored | PASS | {"entries_page": 35} |
| seeded API key still authenticates | PASS | {"status": 200} |
| second tenant's data restored and isolated | PASS | {"beta_documents": 1} |
| job QUEUED at backup time completes after restore (sweeper) | PASS | {"status": "WAITING_FOR_HUMAN"} |
| new upload processes end to end after restore | PASS | {"status": "WAITING_FOR_HUMAN"} |
| runtime role: RLS and append-only audit still enforced | PASS | {"no_context": 0, "alpha_context_foreign_rows": 0, "alpha_context_rows": 4, "audit_update_refused": true} |

### Restore drill — large run (verbatim console output, 2026-10-05)

```
seeded: 11087 rows in 29 tables, 1896 objects
[PASS] backup contains every stored object (originals and page images)  {"objects_in_bucket": 1896, "objects_in_backup": 1896}
[PASS] database identical after restore (row counts + content hash, every table)  {"tables": 29, "rows": 11087, "differing": [], "rows_tenants": 3, "rows_users": 3, "rows_documents": 308, "rows_processing_jobs": 308, "rows_review_tasks": 214, "rows_review_actions": 6, "rows_action_runs": 5, "rows_audit_logs": 1526, "rows
[PASS] object store identical after restore (SHA-256 per object)  {"objects": 1896, "missing": [], "changed": []}
[PASS] logins of both tenants work (password hashes restored)  {}
[PASS] straight-through document restored with its executed action  {"status": "COMPLETED", "action": "succeeded"}
[PASS] straight-through page image served from restored bucket  {"bytes": 33784}
[PASS] reviewed document restored with its executed action  {"status": "COMPLETED", "action": "succeeded"}
[PASS] reviewed page image served from restored bucket  {"bytes": 33182}
[PASS] review decision and correction restored  {"field": "corrected", "task": "approved"}
[PASS] audit trail restored  {"entries_page": 100}
[PASS] seeded API key still authenticates  {"status": 200}
[PASS] second tenant's data restored and isolated  {"beta_documents": 28}
[PASS] job QUEUED at backup time completes after restore (sweeper)  {"status": "WAITING_FOR_HUMAN"}
[PASS] new upload processes end to end after restore  {"status": "WAITING_FOR_HUMAN"}
[PASS] runtime role: RLS and append-only audit still enforced  {"no_context": 0, "alpha_context_foreign_rows": 0, "alpha_context_rows": 113, "audit_update_refused": true}

15/15 checks passed; timings {'backup_s': 3.8, 'fresh_environment_s': 21.2, 'restore_s': 9.6, 'restart_s': 7.1}
```


## Stack benchmark (stack_benchmark.py) — 2026-10-05T17:11:05.999628+00:00

| Measure | Value |
|---|---|
| documents | 84 |
| statuses | {"WAITING_FOR_HUMAN": 46, "COMPLETED": 35, "FAILED": 3} |
| straight_through_rate | 0.4167 |
| review_rate | 0.5476 |
| failure_rate | 0.0357 |
| unfinished | 0 |
| upload_wall_s | 6.21 |
| total_wall_s | 50.89 |
| documents_per_minute | 99.0 |
| latency_s | {"mean": 24.61, "p50": 14.682, "p95": 49.821, "p99": 49.855} |
| processing_s | {"mean": 1.15, "p50": 0.608, "p95": 4.346, "p99": 4.71} |
| queue_depth | {"max_queued": 68, "max_running": 4, "samples": 41} |
| containers | {"api": {"peak_cpu_pct": 48.0, "mean_cpu_pct": 21.2, "peak_mem_mib": 146, "mem_limit_mib": 1024, "peak_mem_pct_of_limit": 14.3, "samples": 17}, "worker": {"peak_cpu_pct": 204.2, "mean_cpu_pct": 107.0, "peak_mem_mib": 647, "mem_limit_mib": 3072, "peak_mem_pct_of_limit": 21.1, "samples": 17}, "postgres": {"peak_cpu_pct": 30.6, "mean_cpu_pct": 14.1, "peak_mem_mib": 192, "mem_limit_mib": 2048, "peak_mem_pct_of_limit": 9.4, "samples": 17}, "redis": {"peak_cpu_pct": 3.5, "mean_cpu_pct": 1.1, "peak_mem_mib": 7, "mem_limit_mib": 16097, "peak_mem_pct_of_limit": 0.0, "samples": 17}, "frontend": {"peak_cpu_pct": 3.3, "mean_cpu_pct": 0.9, "peak_mem_mib": 15, "mem_limit_mib": 256, "peak_mem_pct_of_limit": 5.8, "samples": 17}} |
| llm_calls_and_cost | 0 calls, cost 0 |
