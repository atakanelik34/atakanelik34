# Validation results

## Fault-injection suite (`pytest -m validation`) — 2026-10-05T12:33:36.577623+00:00

| Scenario | Measurements |
|---|---|
| 10 concurrent duplicate deliveries | {"deliveries": 5, "executions": 1} |
| 10 concurrent identical uploads | {"uploads": 20, "documents": 1, "jobs": 1, "statuses": ["201", "409"]} |
| 11 dead-letter + replay | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "attempts": 3, "dead_letter_audited": true} |
| 11 non-retryable | {"attempts": 1} |
| 3 large PDF near limit | {"size_mb": 46.0, "upload_s": 0.558, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 22, "digitize": 5952, "enrich": 10, "extract": 25, "probe": 197, "validate": 13}, "wall_s": 6.37, "cpu_s": 6.28, "avg_cores_busy": 0.99, "baseline_rss_mib": 343, "peak_rss_mib": 515, "peak_scratch_mib": 0.0} |
| 3 over-limit PDF | {"size_mb": 85.4, "status": 413} |
| 4 high page count (100) | {"pages": 100, "size_mb": 0.04, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 82, "digitize": 11424, "enrich": 10, "extract": 87, "probe": 27, "validate": 15}, "wall_s": 11.84, "cpu_s": 11.68, "avg_cores_busy": 0.99, "baseline_rss_mib": 397, "peak_rss_mib": 398, "peak_scratch_mib": 0.0} |
| 4 high page count (500) | {"pages": 500, "size_mb": 0.18, "outcomes": ["waiting_for_review"], "step_ms": {"validate": 25, "classify": 348, "digitize": 56130, "extract": 378, "enrich": 13, "probe": 83}, "wall_s": 57.24, "cpu_s": 57.0, "avg_cores_busy": 1.0, "baseline_rss_mib": 399, "peak_rss_mib": 401, "peak_scratch_mib": 0.0} |
| 4 high page count (1000) | {"pages": 1000, "size_mb": 0.35, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 762, "digitize": 110707, "enrich": 41, "extract": 804, "probe": 128, "validate": 50}, "wall_s": 112.84, "cpu_s": 112.17, "avg_cores_busy": 0.99, "baseline_rss_mib": 402, "peak_rss_mib": 407, "peak_scratch_mib": 0.0} |
| 4 high page count (1200) | {"pages": 1200, "size_mb": 0.43, "outcomes": ["waiting_for_review"], "step_ms": {"classify": 861, "digitize": 133480, "enrich": 10, "extract": 946, "probe": 324, "validate": 33}, "wall_s": 136.03, "cpu_s": 135.28, "avg_cores_busy": 0.99, "baseline_rss_mib": 408, "peak_rss_mib": 411, "peak_scratch_mib": 0.0} |
| 4 page limit exceeded | {"pages": 2001, "outcome": "failed", "error": "document_error"} |
| 4/F2 scanned pages vs lease | {"pages": 40, "simulated_ocr_s_per_page": 0.05, "digitize_s": 7.66, "non_ocr_s_per_page": 0.142, "max_pages_within_900s_lease_at_10s_ocr": 88, "max_pages_within_900s_lease_at_30s_ocr": 29, "outcomes": ["waiting_for_review"], "wall_s": 7.972} |
| 12 png decompression bomb | {"size_kb": 0.5, "outcomes": ["failed"], "final": "FAILED", "error": "document_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.09, "cpu_s": 0.06, "avg_cores_busy": 0.67, "baseline_rss_mib": 412, "peak_rss_mib": 412, "peak_scratch_mib": 0.0} |
| 12 giant page (render bomb) | {"size_kb": 0.6, "outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "final": "DEAD_LETTERED", "error": "internal_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.23, "cpu_s": 0.15, "avg_cores_busy": 0.65, "baseline_rss_mib": 414, "peak_rss_mib": 414, "peak_scratch_mib": 0.0} |
| 12 corrupted pdf | {"size_kb": 0.1, "outcomes": ["failed"], "final": "FAILED", "error": "document_error", "worker_still_healthy": "waiting_for_review", "wall_s": 0.07, "cpu_s": 0.04, "avg_cores_busy": 0.57, "baseline_rss_mib": 414, "peak_rss_mib": 414, "peak_scratch_mib": 0.0} |
| 13 CPU/RAM, 12 documents, 4 concurrent | {"documents": 12, "finals": ["waiting_for_review"], "retries": 0, "docs_per_min": 34.1, "wall_s": 21.11, "cpu_s": 43.02, "avg_cores_busy": 2.04, "baseline_rss_mib": 490, "peak_rss_mib": 618, "peak_scratch_mib": 36.4} |
| 12/F16 render memory bomb | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "final": "DEAD_LETTERED", "error": "internal_error", "attempts": 3} |
| 4/F15 render time budget (fixed) | {"pages": 80, "budget_s": 3, "outcomes": ["failed"], "final": "FAILED", "error": "processing_budget_exceeded", "wall_s": 3.106} |
| 5 slow OCR | {"pages": 4, "ocr_delay_s": 0.5, "wall_s": 2.833, "outcome": "waiting_for_review"} |
| 5 OCR transient failure | {"ocr_calls": 6, "recovered": true} |
| 6/8/9 OCR exceeds lease | {"lease_s": 2, "ocr_s_per_page": 0.8, "pages": 4, "ocr_calls": 8, "wasted_ocr_calls": 4, "outcomes": ["lost_lease", "waiting_for_review"], "finish_s": 3.997} |
| 6/8 heartbeat keeps lease (F15) | {"lease_s": 1, "heartbeat_s": 0.25, "ocr_calls": 4, "contenders": ["not_claimed", "not_claimed", "not_claimed", "not_claimed", "not_claimed"], "outcome": "waiting_for_review", "attempts": 1} |
| 8 lease expiry, no contention | {"outcome": "waiting_for_review"} |
| 9 sweeper re-dispatch | {"redispatched": 1} |
| 7 worker crash during OCR | {"steps": [["probe", "SUCCEEDED", null], ["digitize", "FAILED", "worker_lost"], ["digitize", "SUCCEEDED", null], ["classify", "SUCCEEDED", null], ["extract", "SUCCEEDED", null], ["enrich", "SUCCEEDED", null], ["validate", "SUCCEEDED", null], ["review", "WAITING", null]], "recovered": true} |
| 6/9 step always outlasts lease (F2 fixed) | {"claims": 3, "max_attempts": 3, "attempts": 3, "outcomes": ["killed", "killed", "killed", "dead_lettered", "not_claimed"], "final": "DEAD_LETTERED", "error": "lease_expired"} |
| 15 LLM timeouts retried | {"server_calls": 3, "attempts": 3, "outcome": "succeeded"} |
| 15 LLM retries vs provider timeout | {"attempt": {"error": "TimeoutError", "status": "timeout", "provider": "llm:ollama", "duration_ms": 1501}, "server_calls": 2, "job_wall_s": 2.067} |
| 15 abandoned LLM call metering | {"server_calls": 2, "provider_call_rows": 0} |
| 15 LLM breaker | {"per_document": ["failed", "failed", "circuit open after repeated failures", "circuit open after repeated failures"], "server_calls": 6} |
| 16 ERP timeout then success | {"outcomes": ["retry_scheduled", "succeeded"], "receiver_requests": 2, "distinct_keys": 1} |
| 16 ERP timeouts exhaust retries | {"outcomes": ["retry_scheduled", "retry_scheduled", "dead_lettered"], "run_status": "approved", "run_attempts": 3, "receiver_requests": 3, "document": "FAILED"} |
| 16 ERP rejection | {"run_status": "failed", "error": "http_400"} |
| 16 replay after ERP timeouts (F14 fixed) | {"requests": 4, "distinct_keys": 1, "runs": [["approved", 3], ["succeeded", 1]]} |
| 1 upload rate limit (F1 fixed) | {"limit_per_minute": 5, "statuses": [201, 201, 201, 201, 201, 429, 429, 429]} |
| 1 API limit bounds uploads | {"statuses": [201, 201, 201, 201, 201, 429, 429, 429, 429, 429, 429, 429, 429, 429]} |
| 2 concurrent upload flood | {"uploads": 200, "ok": 200, "other": [], "documents": 200, "jobs": 200, "dispatched": 200, "wall_s": 3.82, "uploads_per_s": 52.4, "p50_s": 3.373, "p95_s": 3.604, "max_s": 3.754, "mean_s": 3.331} |
| 14 tenant isolation under load (RLS forced) | {"requests": 230, "cross_tenant_reads": 60, "cross_tenant_leaks": 0, "list_leaks": 0, "jobs_processed_in_parallel": 10} |

## Live Compose stack (`stack_validation.py`) — 2026-10-05T12:14:02.322638+00:00

| Check | Result | Measurements |
|---|---|---|
| 2 concurrent upload flood | PASS | {"uploads": 125, "non_201": [], "uploads_per_s": 31.1, "p50_s": 2.534, "p95_s": 3.889, "max_s": 4.0} |
| 14 tenant isolation under load (API) | PASS | {"cross_reads": 75, "non_404": [], "list_leaks": 0} |
| 14 tenant isolation (database, runtime role idp_app) | PASS | {"no_context": 0, "alpha_context_foreign_rows": 0, "alpha_context_rows": 205, "audit_update_refused": true} |
| 3 large PDF accepted | PASS | {"size_mb": 46.0, "upload_s": 0.94} |
| 3 over-limit PDF refused at the edge | PASS | {"size_mb": 85.4, "status": 413} |
| 4 high page count processed | PASS | {"pages": 500, "status": "WAITING_FOR_HUMAN"} |
| 13 CPU/RAM under concurrent documents | PASS | {"documents": 102, "statuses": {"WAITING_FOR_HUMAN": 102}, "unfinished": 0, "processing_wall_s": 105.6, "docs_per_min": 58.0, "containers": {"api": {"peak_cpu_pct": 88.8, "mean_cpu_pct": 11.3, "peak_mem_mib": 148, "mem_limit_mib": 1024, "peak_mem_pct_of_limit": 14.5, "samples": 40}, "worker": {"peak_cpu_pct": 192.0, "mean_cpu_pct": 99.8, "peak_mem_mib": 570, "mem_limit_mib": 3072, "peak_mem_pct_of_limit": 18.6, "samples": 40}, "postgres": {"peak_cpu_pct": 80.4, "mean_cpu_pct": 8.9, "peak_mem_mib": 149, "mem_limit_mib": 2048, "peak_mem_pct_of_limit": 7.3, "samples": 40}, "frontend": {"peak_cpu_pct": 6.4, "mean_cpu_pct": 0.8, "peak_mem_mib": 9, "mem_limit_mib": 256, "peak_mem_pct_of_limit": 3.5, "samples": 40}}} |
| no failed or dead-lettered jobs | PASS | {"failed_or_dead": 0} |
