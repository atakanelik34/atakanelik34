# Validation results

## Fault-injection suite (`pytest -m validation`) — 2026-10-05T00:36:51.744047+00:00

| Scenario | Measurements |
|---|---|
| 10 concurrent duplicate deliveries | {"deliveries": 5, "executions": 1} |
| 10 concurrent identical uploads | {"uploads": 20, "documents": 1, "jobs": 1, "statuses": ["201", "409", "MissingGreenlet"]} |
| 10 identical uploads: race losers | {"statuses": ["201", "409", "MissingGreenlet"]} |
| 11 dead-letter + replay | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "attempts": 3, "dead_letter_audited": true} |
| 11 non-retryable | {"attempts": 1} |
| 3 large PDF near limit | {"size_mb": 46.0, "upload_s": 0.414, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 14, "digitize": 5872, "enrich": 6, "extract": 15, "probe": 157, "validate": 8}, "wall_s": 6.18, "cpu_s": 6.15, "avg_cores_busy": 1.0, "baseline_rss_mib": 315, "peak_rss_mib": 491, "peak_scratch_mib": 0.0} |
| 3 over-limit PDF | {"size_mb": 85.4, "status": 413} |
| 4 high page count (100) | {"pages": 100, "size_mb": 0.04, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 62, "digitize": 10372, "enrich": 5, "extract": 179, "probe": 25, "validate": 10}, "wall_s": 10.78, "cpu_s": 10.74, "avg_cores_busy": 1.0, "baseline_rss_mib": 418, "peak_rss_mib": 421, "peak_scratch_mib": 0.0} |
| 4 high page count (500) | {"pages": 500, "size_mb": 0.18, "outcomes": ["waiting_for_review"], "step_ms": {"validate": 12, "classify": 361, "digitize": 52132, "extract": 241, "enrich": 7, "probe": 58}, "wall_s": 52.99, "cpu_s": 53.24, "avg_cores_busy": 1.0, "baseline_rss_mib": 377, "peak_rss_mib": 382, "peak_scratch_mib": 0.0} |
| 4 high page count (1000) | {"pages": 1000, "size_mb": 0.35, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 655, "digitize": 105004, "enrich": 6, "extract": 577, "probe": 91, "validate": 19}, "wall_s": 106.62, "cpu_s": 107.1, "avg_cores_busy": 1.0, "baseline_rss_mib": 383, "peak_rss_mib": 394, "peak_scratch_mib": 0.0} |
| 4 page limit exceeded | {"pages": 2001, "outcome": "failed", "error": "document_error"} |
| 4/F2 scanned pages vs lease | {"pages": 40, "simulated_ocr_s_per_page": 0.05, "digitize_s": 7.19, "non_ocr_s_per_page": 0.13, "max_pages_within_900s_lease_at_10s_ocr": 88, "max_pages_within_900s_lease_at_30s_ocr": 29, "outcomes": ["waiting_for_review"], "wall_s": 7.39} |
| 12 png decompression bomb | {"size_kb": 0.5, "outcomes": ["failed"], "final": "FAILED", "error": "document_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.06, "cpu_s": 0.04, "avg_cores_busy": 0.67, "baseline_rss_mib": 395, "peak_rss_mib": 395, "peak_scratch_mib": 0.0} |
| 12 giant page (render bomb) | {"size_kb": 0.6, "outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "final": "DEAD_LETTERED", "error": "internal_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.13, "cpu_s": 0.09, "avg_cores_busy": 0.69, "baseline_rss_mib": 397, "peak_rss_mib": 397, "peak_scratch_mib": 0.0} |
| 12 corrupted pdf | {"size_kb": 0.1, "outcomes": ["failed"], "final": "FAILED", "error": "document_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.04, "cpu_s": 0.04, "avg_cores_busy": 1.0, "baseline_rss_mib": 397, "peak_rss_mib": 397, "peak_scratch_mib": 0.0} |
| 13 CPU/RAM, 12 documents, 4 concurrent | {"documents": 12, "finals": ["waiting_for_review"], "retries": 0, "docs_per_min": 36.6, "wall_s": 19.66, "cpu_s": 39.31, "avg_cores_busy": 2.0, "baseline_rss_mib": 481, "peak_rss_mib": 608, "peak_scratch_mib": 36.4} |
| 12/F16 render memory bomb | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "final": "DEAD_LETTERED", "error": "internal_error", "attempts": 3} |
| 4/F15 render time budget | {"pages": 80, "budget_s": 3, "outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "final": "DEAD_LETTERED", "wall_s": 9.159} |
| 5 slow OCR | {"pages": 4, "ocr_delay_s": 0.5, "wall_s": 2.688, "outcome": "waiting_for_review"} |
| 5 OCR transient failure | {"ocr_calls": 6, "recovered": true} |
| 6/8/9 OCR exceeds lease | {"lease_s": 2, "ocr_s_per_page": 0.8, "pages": 4, "ocr_calls": 8, "wasted_ocr_calls": 4, "outcomes": ["lost_lease", "waiting_for_review"], "finish_s": 3.908} |
| 8 lease expiry, no contention | {"outcome": "waiting_for_review"} |
| 9 sweeper re-dispatch | {"redispatched": 1} |
| 7 worker crash during OCR | {"steps": [["probe", "SUCCEEDED", null], ["digitize", "FAILED", "worker_lost"], ["digitize", "SUCCEEDED", null], ["classify", "SUCCEEDED", null], ["extract", "SUCCEEDED", null], ["enrich", "SUCCEEDED", null], ["validate", "SUCCEEDED", null], ["review", "WAITING", null]], "recovered": true} |
| 6/9 step always outlasts lease | {"claims": 5, "max_attempts": 3, "final": "RUNNING"} |
| 15 LLM timeouts retried | {"server_calls": 3, "attempts": 3, "outcome": "succeeded"} |
| 15 LLM retries vs provider timeout | {"attempt": {"error": "TimeoutError", "status": "timeout", "provider": "llm:ollama", "duration_ms": 1503}, "server_calls": 2, "job_wall_s": 1.769} |
| 15 abandoned LLM call metering | {"server_calls": 2, "provider_call_rows": 0} |
| 15 LLM breaker | {"per_document": ["failed", "failed", "circuit open after repeated failures", "circuit open after repeated failures"], "server_calls": 6} |
| 16 ERP timeout then success | {"outcomes": ["retry_scheduled", "succeeded"], "receiver_requests": 2, "distinct_keys": 1} |
| 16 ERP timeouts exhaust retries | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "run_status": "approved", "run_attempts": 3, "receiver_requests": 3, "document": "FAILED"} |
| 16 ERP rejection | {"run_status": "failed", "error": "http_400"} |
| 16 replay after ERP timeouts | {"requests": 4, "distinct_keys": 2} |
| 1 upload rate limit | {"limit_per_minute": 5, "statuses": [201, 201, 201, 201, 201, 201, 201, 201]} |
| 1 API limit bounds uploads | {"statuses": [201, 201, 201, 201, 201, 201, 201, 201, 201, 201, 429, 429, 429, 429]} |
| 2 concurrent upload flood | {"uploads": 200, "ok": 200, "other": [], "documents": 200, "jobs": 200, "dispatched": 200, "wall_s": 2.62, "uploads_per_s": 76.4, "p50_s": 2.357, "p95_s": 2.512, "max_s": 2.578, "mean_s": 2.329} |
| 14 tenant isolation under load (RLS forced) | {"requests": 230, "cross_tenant_reads": 60, "cross_tenant_leaks": 0, "list_leaks": 0, "jobs_processed_in_parallel": 10} |

## Live Compose stack (`stack_validation.py`) — 2026-10-05T00:31:54.933700+00:00

| Check | Result | Measurements |
|---|---|---|
| 2 concurrent upload flood | PASS | {"uploads": 125, "non_201": [], "uploads_per_s": 40.5, "p50_s": 2.044, "p95_s": 2.997, "max_s": 3.06} |
| 14 tenant isolation under load (API) | PASS | {"cross_reads": 75, "non_404": [], "list_leaks": 0} |
| 14 tenant isolation (database, runtime role idp_app) | PASS | {"no_context": 0, "alpha_context_foreign_rows": 0, "alpha_context_rows": 103, "audit_update_refused": true} |
| 3 large PDF accepted | PASS | {"size_mb": 46.0, "upload_s": 0.42} |
| 3 over-limit PDF refused at the edge | PASS | {"size_mb": 85.4, "status": 413} |
| 4 high page count processed | PASS | {"pages": 500, "status": "WAITING_FOR_HUMAN"} |
| 13 CPU/RAM under concurrent documents | PASS | {"documents": 102, "statuses": {"WAITING_FOR_HUMAN": 102}, "unfinished": 0, "processing_wall_s": 101.6, "docs_per_min": 60.2, "containers": {"api": {"peak_cpu_pct": 88.3, "mean_cpu_pct": 8.8, "peak_mem_mib": 166, "mem_limit_mib": 1024, "peak_mem_pct_of_limit": 16.2, "samples": 37}, "worker": {"peak_cpu_pct": 197.3, "mean_cpu_pct": 97.0, "peak_mem_mib": 580, "mem_limit_mib": 3072, "peak_mem_pct_of_limit": 18.9, "samples": 37}, "postgres": {"peak_cpu_pct": 53.0, "mean_cpu_pct": 5.9, "peak_mem_mib": 125, "mem_limit_mib": 16097, "peak_mem_pct_of_limit": 0.8, "samples": 37}, "frontend": {"peak_cpu_pct": 2.8, "mean_cpu_pct": 0.5, "peak_mem_mib": 8, "mem_limit_mib": 256, "peak_mem_pct_of_limit": 3.3, "samples": 37}}} |
| no failed or dead-lettered jobs | PASS | {"failed_or_dead": 0} |
