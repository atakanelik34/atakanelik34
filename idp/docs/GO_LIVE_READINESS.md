# Go-live readiness (phase 13, updated by the production-readiness follow-up)

Status: **decision record** · Date: 2026-10-05 · Branch `claude/zealous-bardeen-q9cw6x`
(head at the end of phase 13; see §3 for the commits).
Companion documents: [PRODUCTION_VALIDATION.md](PRODUCTION_VALIDATION.md) (plan,
scenario matrix, findings), [validation/RESULTS.md](validation/RESULTS.md) (raw
measurements), [validation/CALIBRATION.md](validation/CALIBRATION.md) (calibration run).

Status model: **PASS** = verified by an executed test · **CONDITIONAL** = implemented,
but the environment or the data prevented complete verification · **OPEN** = not
implemented or not sufficiently validated · **FAIL** = tested and failed.
Every number below was measured in this phase unless it is marked *estimate*.

---

## 0. Follow-up after phase 13 (production-readiness round)

**Decision: still CONDITIONAL GO.** Every condition that could be closed from the
repository is closed (P0-1, P0-2, F17). The remaining conditions need production
infrastructure or real data that is not available here:

* **ClamAV egress:** clamd needs outbound access in production to download signatures.
* **Staging restore with production-like data:** the runbook is ready; the run needs
  staging resources and credentials.
* **Calibration on real anonymised documents:** only synthetic data has been measured.
* **First run of `idp-validation`:** it needs a merge to `main`, which awaits your
  approval.

None of them can be verified by code changes alone.

| Item | Status | What was done / evidence |
|---|---|---|
| P0-1 calibration thresholds | **PASS** | `subtotal` and `tax` thresholds raised 0.80 → 0.85 in the invoice template (`domain/templates.py`); thresholds are per field, per tenant schema version. `test_invoice_amount_thresholds_follow_calibration`: 0.841 → review, 0.865 → passes. No rejection threshold (insufficient data). |
| P0-2 scanned-document safety | **PASS** | **Gap found and closed:** a scanned invoice with perfect OCR completed automatically, and would have reached an approval-free ERP action. The new part-level rule `part:ocr` (REQUIRES_HUMAN) holds every part with OCR-read pages for review; approval records it as overridden. `test_scanned_documents_always_need_a_human` fails without the change. `test_unbalanced_invoice_never_reaches_the_erp_without_a_human`: with total ≠ subtotal + tax, nothing is posted until a reviewer corrects the value; re-validation passes, then exactly one posting with the corrected total. |
| P0-3 ClamAV | **CONDITIONAL** (config done, egress needed) | Compose passes `FRESHCLAM_CHECKS` (default 12/day; the image default was 1/day); verified running `freshclam --checks=12 --daemon`. `StreamMaxLength` 100 MB is above `MAX_UPLOAD_BYTES` (50 MB). Scan before storage: the structural test passes. Required production configuration is listed below. |
| P0-4 staging restore | **CONDITIONAL** (runbook ready; inputs needed) | [STAGING_RESTORE_RUNBOOK.md](STAGING_RESTORE_RUNBOOK.md) plus the read-only `scripts/restore/fingerprint.sql` and `object_refs.sql`. The database half was rehearsed locally: snapshot-consistent fingerprint and dump, restore into a new database, identical over 29 tables. The rehearsal found that a production dump's grants to `idp_app` would abort a restore into a new instance, so the runbook uses `--no-privileges` followed by `provision-db-roles`. The runbook also requires cutting outbound paths (webhook/ERP/SMTP) before a staging worker starts. |
| P0-5 CI / default branch | **PASS** (CI) / **OPEN** (merge) | `idp-ci` is green on every run from 26 to 36 (36 = the F17 commit; the workflow-only and docs-only commits are outside its path filter). `idp-validation` is defined correctly but has never executed: it can only run from `main`. It was hardened: Tesseract in the fault-injection job, ClamAV plus `clamav_check.py` in the live-stack job. With this documentation commit the branch is 39 commits ahead of `main`, 0 behind (fast-forward possible). **Not merged**: awaiting your approval. |
| F17 (VAT rate as tax) | **PASS** (fixed) | Root cause: OCR splits a visual row into a label line and an amount line. Fix in `providers/extraction/key_value.py`: a number followed by % is never an amount, and the value is read on the same visual row before looking below. Ground-truth benchmark with real Tesseract: scanned field accuracy **76.7 % → 95.9 %**, wrong values **21 → 1** (confidence 0.33, below threshold), `total` missed 8 → 0. Native unchanged at 97.6 %. **0 wrong values at or above threshold, 0 silent errors.** |
| F13 | **OPEN** — genuine defect, not a blocker | An LLM call cancelled by the extract step's `asyncio.wait_for` is never metered: the gateway records usage only when a call finishes. No LLM is configured, so there is no exposure today. Fix before enabling any (especially cloud) LLM tier: record a `cancelled` call with estimated input tokens in a `finally` block. |
| F16 | **OPEN** — genuine defect, not a blocker | `HybridDigitizer._call` catches `BrokenProcessPool` and `TimeoutError` but not `MemoryError`. A render exceeding the child's `RLIMIT_AS` therefore comes back as an unclassified exception, is treated as a retryable `internal_error`, and is retried 3× before dead-letter. The worker stays protected. Fix (about 5 lines): map `MemoryError` to `ProcessingBudgetExceededError(reason="memory_limit")` and turn the strict xfail into a regression test. |

**ClamAV production configuration (exact).** No credentials are involved.

1. Egress from the `clamav` container:
   * TCP 443 to `database.clamav.net`. It is served by a CDN, so allow by hostname or proxy, not by IP.
   * DNS resolution of `current.cvd.clamav.net` (TXT record, used for the version check).
2. Optional, through an HTTP proxy. Override file:

   ```yaml
   services:
     clamav:
       environment:
         FRESHCLAM_CONF_HTTPProxyServer: proxy.internal
         FRESHCLAM_CONF_HTTPProxyPort: "3128"
   ```

3. Optional, from a private mirror (e.g. a `cvdupdate` server). Override file:

   ```yaml
   services:
     clamav:
       environment:
         FRESHCLAM_CONF_PrivateMirror: https://clamav-mirror.internal
   ```

4. Keep the `clamav-db` volume persistent, and keep `FRESHCLAM_CHECKS` between 12 and 24.
5. Monitor signature age, and alert above 24 h:

   ```bash
   docker compose exec clamav clamscan --version
   ```

   This prints `ClamAV 1.4.x/<daily version>/<date>`. Also alert on freshclam errors
   in `docker compose logs clamav`.
6. Keep `MALWARE_SCANNER=clamav` (fail closed: 503 while clamd is unreachable). If
   `MAX_UPLOAD_BYTES` is ever raised above 100 MB, also set `CLAMD_CONF_StreamMaxLength`.

**Final regression on the follow-up code (2026-10-05, HEAD 37b0897 plus these docs):**

| Suite | Result |
|---|---|
| `ruff check`, `ruff format --check`, `mypy` (156 files) | clean |
| backend `pytest` (default) | **377 passed, 1 skipped** |
| backend `pytest -m validation` | **38 passed, 2 xfailed** (F13, F16: strict xfails, documented above) |
| frontend lint, typecheck, 28 tests, build | pass |
| `stack_validation.py --uploads 100 --pages 500` (rebuilt images, OCR and ClamAV on) | **PASS 9/9.** Flood 125 uploads at 25.8/s, p95 4.8 s; isolation 0 leaks (API and `idp_app`); 46 MB accepted in 8.7 s; 85 MB → 413; 500 pages; OCR page `source=ocr`, confidence 0.94, held for review; 103 documents, 69.9/min, 0 failed jobs |
| `clamav_check.py` | **PASS 6/6** (EICAR rejected before storage: document count unchanged; scanner down → 503; recovers) |
| `stack_benchmark.py` (84 documents, live worker) | 99.0 documents/min, processing p95 4.3 s; status mix unchanged (35 completed, 46 review, 3 failed by design). Scanned fields in the running worker: correct **112 → 140**, wrong **21 → 1**, missed 13 → 5; native unchanged (571 correct, 0 wrong) |
| `idp-ci` run 36 | green |

**Open conditions (unchanged from §1.1 unless noted):**
1. Calibration on real data is still required. The thresholds have been applied to the
   template. **Existing tenant schemas** created before this change keep 0.80 until the
   tenant publishes a new version:
   * In the UI: Document types → invoice → edit draft → `tax`/`subtotal` confidence threshold 0.85 → publish.
   * Through the API: `PUT /document-types/{id}/draft`, then `POST /document-types/{id}/publish`.
2. ClamAV egress: the configuration above.
3. Review capacity for scans: now **guaranteed 100 % review** by `part:ocr`, rather than observed.
4. Staging restore: follow the runbook.
5. `idp-validation`: merge to `main`, then trigger it once.

---

## 1. Decision (phase 13)

**CONDITIONAL GO.**

None of the go-live blockers is open:
* F1, F2, F14, F15 and F6 are PASS.
* There is no open security-critical finding.
* No duplicate ERP execution was observed.
* Backups restore completely.

The decision is conditional because two areas could not be fully verified with the
data and environment available:
* **Confidence calibration (F9):** only synthetic documents were available.
* **Real-scan OCR (F4):** no real scanned document could be included.

The conditions in §1.1 make that residual risk explicit and bounded.

| Go-live blocker | Status | Evidence |
|---|---|---|
| F1 upload rate limit | **PASS** | `test_upload_rate_limit_is_enforced_per_principal` (default suite and validation suite). With a limit of 2 the responses are `[201, 201, 429]`. |
| F2 max_attempts bound | **PASS** | 5 regression tests in `test_job_execution.py`, plus validation scenario 6/9. A worker killed on every attempt gets exactly 3 attempts, then the job is dead-lettered. |
| F14 duplicate ERP posting | **PASS** | 4 regression tests in `test_actions.py`, plus validation scenario 16. Lost acknowledgement followed by retry or replay → 1 posting. The restore drill executes each action exactly once. |
| F15 large documents / timeouts | **PASS** | `test_processing_budgets.py`, plus validation scenario 4. 1,200 pages complete in one attempt; a budget breach fails once. |
| F6 backup / restore | **PASS** (after a defect was found and fixed here) | Restore drill 15/15, twice: 193 rows / 10 objects, and 11,087 rows / 1,896 objects / 79 MB. |
| Security-critical findings | **none open** | See §6. |
| Duplicate ERP execution | **none observed** | F14 tests; drill: `action_runs` executed once, `succeeded` after restore. |
| Unrecoverable backup or restore | **none** (after fix) | §7. |

### 1.1 Conditions for go-live

1. **Calibrate before trusting straight-through processing.** Before go-live, apply
   the provisional thresholds in §8.4: `tax` and `subtotal` at 0.85 in the tenant's
   invoice schema. Keep the cross-field sum rule at REQUIRES_HUMAN.
   * Within the hypercare period, run `test_accuracy_benchmark` and
     `scripts/validation/calibrate.py` on **at least 300 real, anonymised documents
     with at least 30 observed field errors** (§8.5).
   * Until then, the measured straight-through rate (41.7 %) is a property of
     synthetic data, not a forecast.
2. **ClamAV signature updates must work in production.** clamd must reach
   `database.clamav.net` or a mirror, and freshclam must succeed. In the sandbox the
   bundled database was 8 days old and could not be updated (§6).
3. **Review capacity for scanned documents.** In the measurement, 0 of 15 scanned
   invoices completed automatically: 100 % went to review. Staff the review queue for
   the expected share of scans.
4. **Repeat the restore drill once on staging** with the production managed Postgres
   and object store. The drill here used the Compose Postgres and MinIO (§7.4).
5. **Run `idp-validation` from the default branch.** It has never executed (§4).
   Merge this branch, trigger it once with `workflow_dispatch`, and keep it nightly.

---

## 2. Test scope

| Area | In scope | Out of scope (and why) |
|---|---|---|
| Backup / restore | `scripts/backup.sh` and `scripts/restore.sh` on the Compose stack: destroy every volume, restore into a fresh environment, verify data and behaviour | Managed-database PITR, cross-region copies (provider features, not this repository) |
| Calibration / accuracy | 84 synthetic ground-truth documents (60 native and 15 scanned invoices, 6 non-invoices, 3 corrupted) through the real pipeline | Real customer documents (none available) |
| OCR | Tesseract inside the **true default image in CI**. Tesseract inside the running worker container in the sandbox (default image plus a conda-forge Tesseract layer). Host Tesseract for the test suites. | A real scanned document (§5) |
| ClamAV | A real clamd 1.4.6 in Compose: clean, EICAR, outage, recovery, the API-key path, no bypass | Signature updates (blocked egress) |
| F12 | A deterministic race test, run 5 times, plus a default-suite test | — |
| Regression | Backend (ruff, format, mypy, 370 + 40 tests), validation suite, frontend (lint, typecheck, tests, build), Compose E2E, security tests, load and capacity | Browser E2E (Playwright) — not part of this repository's suites |
| Benchmark | The same 84 documents through nginx → API → worker on Compose with OCR and ClamAV on | LLM extraction (no model configured) |

## 3. Environment

* **Sandbox host:** Linux 6.18, x86-64, 4 vCPU, 15 GiB RAM, Docker 29.6 / Compose 5.3.
  Outbound access is restricted:
  * `deb.debian.org`, `database.clamav.net` and public scan sources are blocked;
  * Docker Hub, PyPI, npm, conda-forge and the Ubuntu archive are reachable.
* **Compose stack** (production-like; `docker-compose.yml` plus a sandbox override):
  * postgres 16 (2 GiB limit), redis 7, MinIO;
  * API on the default image; worker (2 CPU / 3 GiB, `WORKER_MAX_JOBS=4`) on the default image plus a Tesseract layer (`scripts/validation/sandbox-ocr/`, labelled sandbox-only);
  * nginx frontend;
  * clamav/clamav 1.4 (database 28136 of 2026-09-27).
  * Settings: `MALWARE_SCANNER=clamav`, `OCR_ENGINE=tesseract`; for load and benchmark runs only, `UPLOAD_RATE_LIMIT_PER_MINUTE=100000` (F1 is tested separately).
* **Test databases:**
  * host Postgres 16 and Redis 7;
  * a CI-identical `postgres:16-alpine` container, in which the test user is a superuser.
* **CI:** GitHub Actions `ubuntu-latest`, Python 3.12, Node 22. The `images` job builds the
  **true default image** (Debian, `tesseract-ocr` 5.5.0) and OCRs a page inside it.
* **LLM:** none configured. All extraction is deterministic; LLM calls = 0, cost = 0 (measured).

Phase 13 commits, in order:

| Commit | Change |
|---|---|
| `b73293e` | F12 |
| `754d6e8` | CI stub dependency |
| `22d3a4f` | F9 tooling |
| `3dff444` | CI RLS test |
| `d4711b9` | ClamAV |
| `c031b0c` | F6 fix and drill |
| `244c51c` | Tesseract thread limit |
| `8ed450d` | Benchmark tooling |

These follow the pre-phase fixes `e68b82d` (F14), `3f4c598` (F2), `f2549b8` (F15),
`e0bc822` (F1), `5ac3b0d` (Postgres limit) and `7cf8c33` (OCR CI path).

## 4. Commands executed and results

| # | Command / test | Result |
|---|---|---|
| 1 | `ruff check src tests migrations`, `ruff format --check …`, `mypy` (backend) | PASS (226 files; mypy: 156 files, no issues) |
| 2 | `pytest` (default suite, host DB) | **PASS — 370 passed, 1 skipped** (the skip is the real-scan parametrisation, which has no fixture yet) |
| 3 | `pytest` in a fresh venv installed only from `pyproject.toml`, against a CI-identical superuser Postgres | PASS — 367 passed (before the last 3 tests were added); this reproduced and fixed both CI failures |
| 4 | `pytest -m validation` (fault injection, load, capacity, accuracy) | **PASS — 38 passed, 2 expected failures** (F13, F16; open, not blockers) |
| 5 | `npm run lint && npm run typecheck && npm test && npm run build` (frontend) | PASS — 28 tests |
| 6 | GitHub Actions `idp-ci` | **PASS on every run from 26 to 30 (the last code commit). Every earlier run (1–25) had failed**: mypy (missing `types-boto3[s3]` stubs) from run 1, then an RLS test that cannot pass against a superuser database. Pytest, image builds and the OCR check had **never run in CI before phase 13**. |
| 7 | GitHub Actions `idp-validation` | **Never executed.** Scheduled and `workflow_dispatch` workflows run only from the default branch, and this branch has not been merged. |
| 8 | `scripts/validation/stack_validation.py --uploads 100 --pages 500` (Compose, OCR and ClamAV on) | **PASS 9/9.** Flood: 125 uploads, 23.9/s, p95 5.2 s. Isolation: 0 leaks at the API and as `idp_app`. 46 MB PDF accepted in 8.2 s with ClamAV (0.9 s without). 85 MB → 413. 500 pages processed. OCR page `source=ocr`, confidence 0.94. 103 documents, 59.3/min, no failed jobs. |
| 9 | `scripts/validation/clamav_check.py` | **PASS 6/6** (§6.1) |
| 10 | `scripts/validation/restore_drill.py` (twice) | **PASS 15/15 and 15/15** (§7) |
| 11 | `scripts/validation/stack_benchmark.py` (twice: before and after the OMP fix) | §9 |
| 12 | `test_accuracy_benchmark` + `scripts/validation/calibrate.py` | §8 |
| 13 | CI `images` job: `ocr_check.py` inside the default image | **PASS:** Tesseract 5.5.0, scan-like page, confidence 0.95, 1.65 s |

## 5. OCR status — **CONDITIONAL**

| Check | Status | Evidence |
|---|---|---|
| Tesseract in the true default image (Debian packages), production digitizer | **PASS** | CI run 26, `images` job: engine `tesseract 5.5.0`, synthetic scan-like page read with confidence 0.95, expected text found. |
| Real OCR on the runtime path inside the running worker (upload → probe → digitize → TesseractOCREngine subprocess → API) | **PASS** (sandbox image variant) | Stack check: page `source=ocr`, confidence 0.94. 15 scanned invoices through the stack (§9). The image is the default image plus conda-forge Tesseract 5.3.4, because the Debian archive is blocked here. |
| Tesseract tests in the backend suite | **PASS** | They run on the CI runner (Ubuntu Tesseract) and on the sandbox host (Tesseract 5.3.4). They were skipped in CI before phase 13 (CI never reached pytest). |
| Concurrent OCR capacity | **PASS** after a fix | Found here: 4 jobs × 4 OpenMP threads on a 2-CPU worker. **14 of 15 scanned invoices** timed out and were dead-lettered. With `OMP_THREAD_LIMIT=1` (commit `244c51c`): 0 failures. See §9. |
| **Real scanned document** | **CONDITIONAL** | None is in the repository: there is no scanner here, and public-domain scan sources are blocked. All scans are synthetic (rendered, skewed, noised, blurred, JPEG). `backend/tests/fixtures/scans/README.md` defines how to add one (no personal data); CI and the tests pick it up automatically. |
| Scanned extraction accuracy | **CONDITIONAL** (measured, weak) | Field accuracy 76.7 %, document accuracy 0 %, line items 34 %. Two wrong-value patterns: `tax` reads the VAT rate (`7`/`20`) instead of the amount, and `subtotal` reads a neighbouring amount. All 15 scanned documents went to review (§8). |

## 6. Security status — **PASS** (no open security-critical finding)

| Control | Status | Evidence |
|---|---|---|
| Malware scanning, real clamd | **PASS** | §6.1 |
| No bypass of scanning | **PASS** | One ingestion path (`POST /documents`, users and API keys). A structural test asserts `IngestionService` is built only there and scans before storing. An API-key upload of EICAR → 422. Other uploads: the master-data CSV import is parsed into rows and never stored as a file. |
| Tenant isolation (RLS) | **PASS** | The RLS test now also runs on a superuser database; under load: 0 leaks at the API and in the database as `idp_app`, and again after restore. |
| Audit append-only | **PASS** | An `UPDATE audit_logs` as `idp_app` is refused, before and after restore. |
| Upload and API rate limits | **PASS** | F1 tests; per principal. |
| Fail closed when the scanner is down | **PASS** | 503 `malware_scanner_unavailable`; nothing stored. |
| Secrets, headers, auth | **PASS** | `test_api_auth.py`, `test_security.py`, `test_hardening.py` (default suite). |
| Signature freshness | **CONDITIONAL** | The sandbox clamd used the bundled database (28136, 2026-09-27). freshclam could not download, and ClamAV warned that the database was older than 7 days. Production needs egress to the ClamAV mirror (condition 2). |
| Open, not security-critical | — | F7: `/metrics` is open when `METRICS_TOKEN` is empty (low). F16: a memory-bomb render is retried 3× before dead-letter (medium; the worker stays healthy). F13: abandoned LLM calls are not metered (medium; no LLM configured). |

### 6.1 ClamAV checks (real clamd, through nginx → API)

| Check | Result |
|---|---|
| Clean PDF | 201, `scan_status=clean` |
| PDF carrying EICAR as an embedded file | 422, audited (`document.malware_rejected`, signature `Eicar-Signature`), no document stored |
| The same via an API key | 422 |
| A bare EICAR file | 415, refused by the type allow-list before scanning. ClamAV matches EICAR only at offset 0, so a scanner test needs a PDF carrier: `files.eicar_pdf()`. |
| clamd stopped | 503 `malware_scanner_unavailable` (fail closed) |
| clamd started again | 201 |

Scanning cost: a 46 MB upload took 8.2 s with clamd, compared with 0.9 s without.

## 7. Backup / restore status — **PASS**

### 7.1 Defect found and fixed (would have been a go-live blocker)

The first drill found that **`scripts/backup.sh` had never backed up a single document
object**.
* The `mc` container ran as uid 1001 against a host directory it could not write.
* It reported a successful transfer and exited 0.
* Every backup therefore contained the database dump only. Originals and page images
  would have been unrecoverable.

Fix (`c031b0c`):
* `mc` runs as root, and the copy is handed back to the invoking user.
* Backup and restore both **verify the object count against the bucket and fail loudly**
  on a mismatch.
* A second defect was fixed in the same commit: `minio-init` failed intermittently on
  fresh volumes (MinIO healthy but refusing connections), which broke stack start-up and
  therefore restores. It now retries for up to a minute.

### 7.2 Procedure (`scripts/validation/restore_drill.py`, reproducible)

1. **Seed through the API:**
   * a second tenant;
   * the processing policy;
   * a mock-ERP connection;
   * an invoice type with an approval-gated action;
   * an API key;
   * a document routed to review, claimed, corrected and approved, with its action approved and executed;
   * a straight-through document with its action approved and executed;
   * a document whose job was still **QUEUED** at backup time (worker stopped).
2. **Fingerprint:** row count and MD5 over the ordered row text of **every table**, plus SHA-256 of **every object**.
3. **`scripts/backup.sh`**.
4. **Disaster:** `docker compose down -v`, which deletes the database, object store and Redis volumes.
5. **Fresh environment:** `docker compose up -d` (new volumes, migrations, bootstrap); stop api/worker; run `scripts/restore.sh`.
6. **Compare** fingerprints, start the application, and check behaviour.

### 7.3 Results

| Check | Small run | Large run |
|---|---|---|
| Backup contains every object | 10/10 | 1,896/1,896 |
| Database identical (29 tables) | 193 rows, 0 differing | 11,087 rows, 0 differing (308 documents, 308 jobs, 214 review tasks, 1,526 audit rows, 3 tenants) |
| Objects identical (SHA-256) | 10 | 1,896 |
| Logins of both tenants (password hashes) | PASS | PASS |
| Reviewed and straight-through documents: COMPLETED, action `succeeded` with its ERP reference | PASS | PASS |
| Page images served from the restored bucket | PASS | PASS |
| Review decision and field correction | PASS | PASS |
| Audit trail | PASS | PASS |
| Seeded API key authenticates | PASS | PASS |
| Second tenant restored and isolated | PASS | PASS |
| Job QUEUED at backup time completes after restore (sweeper; Redis is not backed up) | PASS | PASS |
| New upload processes end to end | PASS | PASS |
| `idp_app`: RLS and append-only audit still enforced | PASS | PASS |
| **Total** | **15/15** | **15/15** |

Timings for the large run (measured; backup 79 MB):

| Step | Time |
|---|---|
| Backup | 3.8 s |
| Fresh environment | 21.2 s |
| Restore | 9.6 s |
| Application restart | 7.1 s |

Restore time at production volume is an *estimate* that scales with data size.
Time it on staging (condition 4).

### 7.4 Not covered

* Restores into a managed database or S3, and point-in-time recovery.
* Restore of a backup taken while uploads are in flight. The script documents why
  objects written between the dump and the mirror are harmless orphans.
* Encryption and off-host shipping of backups (operator responsibility, documented in
  the README).

## 8. Confidence calibration status — **CONDITIONAL**

### 8.1 Dataset and limitation

84 deterministic **synthetic** documents generated by `tests/fixtures/benchmark.py`
(seed 2026), with ground truth:
* fictional vendors and valid-checksum fictional IBANs, no personal data;
* English and German labels, two number formats, two date formats;
* labelled and unlabelled supplier, optional fields, 1–5 line items;
* 15 scanner-degraded copies, 6 non-invoices, 3 corrupted files.

**This is not a sample of any real document population.** The numbers show how the
pipeline behaves on these layouts; they do not predict accuracy on customer documents.
**The data is insufficient to validate an auto-accept threshold:** only 21 wrong values
were observed, against the procedure's minimum of 30, and all of them come from 15
scanned pages.

### 8.2 Measured (in-process and on the stack; identical results)

| Measure | Native (60) | Scanned (15) |
|---|---|---|
| Field accuracy (fields present on the page) | **97.6 %** | **76.7 %** |
| Document accuracy (every header field right) | 76.7 % | 0 % |
| Line-item cell accuracy | 100 % | 34.1 % |
| Straight-through (COMPLETED without review) | 35/60 | 0/15 |

All 84 documents:

| Outcome | Rate |
|---|---|
| Straight-through | 41.7 % |
| Review | 54.8 %; all 6 non-invoices were routed to review, correctly |
| Failure | 3.6 %; the 3 corrupted files, failed once and not retried |

* **Silent errors** (completed automatically with a wrong or invented value): **0**.
* **Documents with a wrong value: 15, and every one went to review.** The safety net was
  the cross-field rule `total = subtotal + tax` (REQUIRES_HUMAN), **not** the field
  thresholds.
* **Native document-accuracy misses** are mostly `currency` on German documents. The
  fixture writes the label as "Waehrung", because its PDF writer cannot encode umlauts,
  while the template alias is "währung". This is partly a fixture artefact.
* **Completed with a missed (optional) value:** 6 documents.

### 8.3 Confidence vs correctness

* **Calibration error:** 704 machine values, 21 wrong; expected calibration error 0.126.
* **Confidence bands:**
  * 0.85–0.95: 595 values, 100 % correct (Wilson lower bound 0.991 / 0.978).
  * 0.80–0.85: 59 values, 76 % correct.
  * Below 0.5: 29 values, 97 % correct. These are almost all `vendor_name` from the first-line fallback (confidence 0.43–0.45, always correct here), so they cause needless review.
* **With the current thresholds** (0.80; 0.85 for `invoice_number` and `total`):
  * **14 wrong values sit at or above their threshold** (`tax` up to 0.841);
  * 55 correct values fall below it.
* **Wrong-value confidence ranges:** `tax` 0.33–0.84 and `subtotal` 0.73–0.76, against correct `tax` 0.865–0.87.

### 8.4 Thresholds (provisional; synthetic evidence)

| Band | Threshold | Basis |
|---|---|---|
| Automatic acceptance | Field confidence ≥ its threshold **and** no REQUIRES_HUMAN rule fails. Thresholds: **0.85 for `tax` and `subtotal`** (was 0.80), `total` and `invoice_number` 0.85 (unchanged), others 0.80 (unchanged). | With `tax`/`subtotal` at 0.85, wrong-at-or-above-threshold goes from 14 to **0**, at the cost of 1 more field review (55 → 56). Setting every field to 0.85 adds 33 needless reviews for no further gain on this set. |
| Human review | Below the field threshold, or any REQUIRES_HUMAN rule fails. Keep `sum` (`total = subtotal + tax`) at REQUIRES_HUMAN: it caught 15 of 15 wrong-value documents. | Measured. |
| Rejection / escalation | **No evidence-based threshold.** Only 1 value fell in the "more often wrong than right" region, and low-confidence values were mostly correct (`vendor_name` fallback). | Treat the document as unclassified or unreadable, which the router already sends to review; do not auto-reject on confidence. |

These are tenant schema settings (versioned and audited), not code changes. They were
**not** changed in the shipped template, because synthetic evidence is not enough to
change a product default.

### 8.5 Reproducible procedure (for real data)

1. Put anonymised documents and their ground truth in the shape of
   `tests/fixtures/benchmark.py`'s `Case`, or export approved reviews as an evaluation
   dataset (`/evaluation/datasets/{id}/import-reviews`).
2. Run `pytest -m validation tests/integration/validation/test_accuracy_benchmark.py`
   with `CALIBRATION_REPORT=…`, then `python3 scripts/validation/calibrate.py
   calibration-report.json --out CALIBRATION.md`.
3. The analysis reports, for each field, the confidence ranges of correct and wrong
   values; the reliability table with Wilson intervals; the effect of the current
   thresholds; and a recommendation. It refuses to call the data sufficient below 30
   errors. Set each field threshold above its highest wrong value that the
   cross-field rules do not catch, and re-run until silent errors = 0 at the required
   confidence.

## 9. Performance results (Compose stack, 84-document set, all uploaded at once)

| Measure | Before `OMP_THREAD_LIMIT=1` | **After** (`244c51c`) |
|---|---|---|
| Documents finished / failed | 84 / 17 (14 scanned invoices dead-lettered by OCR timeouts, plus 3 corrupted) | 84 / **3** (corrupted only) |
| Straight-through / review / failure | 41.7 % / 38.1 % / 20.2 % | **41.7 % / 54.8 % / 3.6 %** |
| Throughput | 3.4 documents/min | **100.9 documents/min** (84 in 50 s) |
| End-to-end latency, upload → terminal (queueing included) | mean 271 s, P50 15.9 s, P95 1,393 s, P99 1,460 s | **mean 29.4 s, P50 16.4 s, P95 48.9 s, P99 48.9 s** |
| Pipeline time, sum of steps | mean 5.6 s, P50 0.78 s, P95 1.6 s, P99 316 s | **mean 1.34 s, P50 0.67 s, P95 4.4 s, P99 5.0 s** |
| Queue depth (max queued / running) | 72 / 4 | 80 / 4 |
| Worker CPU (2-CPU limit), mean / peak | 204 % / 218 % (saturated for 24 min) | 120 % / 203 % |
| Worker RAM, peak of 3 GiB | 748 MiB | 652 MiB |
| Postgres CPU peak / RAM peak (of 2 GiB) | 38 % / 169 MiB | 70 % / 153 MiB |
| API RAM peak (of 1 GiB) | 161 MiB | 129 MiB |
| LLM calls / cost | 0 / 0 | 0 / 0 |

In-process, sequential, one document at a time (host):
* native 0.44 s mean (P50 0.43 s);
* scanned 2.4 s mean (P50 2.5 s).

Other measured capacity (validation suite and stack checks):
* 1,000-page native PDF digitized in 117 s, and 1,200 pages in 144 s, in one attempt;
* 46 MB PDF accepted with a ClamAV scan in 8.2 s;
* 125 concurrent uploads at 23.9/s with p95 5.2 s;
* 103 mixed documents at 59.3/min.

P99 values come from 84 samples. Treat them as indicative.

## 10. Known limitations and open risks

| # | Item | Severity | Status | Mitigation |
|---|---|---|---|---|
| R1 | Calibration and accuracy are measured on synthetic data only (F9) | High | CONDITIONAL | Condition 1; procedure in §8.5 |
| R2 | No real scanned document validated; scanned accuracy is weak (76.7 % field, 0 % straight-through) | High | CONDITIONAL | Condition 3; add a real scan; F17 below |
| R3 | **F17 (new):** on scanned pages `tax` takes the VAT *rate* and `subtotal` a neighbouring amount, at confidence up to 0.84 | Medium | OPEN | The sum rule routes them to review (15/15 caught); provisional `tax`/`subtotal` thresholds of 0.85; needs an extractor fix (a product change, not done in a validation phase) |
| R4 | ClamAV signatures cannot be refreshed from the sandbox | Medium | CONDITIONAL | Condition 2 |
| R5 | `idp-validation` (nightly fault injection plus live stack) has never run in CI | Medium | OPEN | Condition 5 |
| R6 | Restore verified on Compose Postgres and MinIO only | Medium | CONDITIONAL | Condition 4 |
| R7 | F16 memory-bomb retries; F13 LLM metering; F7 `/metrics` without a token; F3 tmpfs counts against worker RAM; F8 synchronous evaluation runs | Low–Medium | OPEN | Documented in PRODUCTION_VALIDATION.md; none blocks go-live |
| R8 | One worker container measured (4 concurrent jobs, 2 CPUs); horizontal scaling not measured | Medium | OPEN | Add workers; throughput per worker is in §9 |
| R9 | No LLM provider measured (cost, latency, quality) | Low (none configured) | N/A | Measure before enabling an LLM tier |

## 11. Rollback considerations

* **Database migration 0013 (F14):** `alembic downgrade 0012` restores per-job keys and
  the global unique index. The downgrade is exercised by every test session, whose
  fixture runs `downgrade base` then `upgrade head`, and was run by hand on a database
  holding data. After rollback, replays send per-job keys again, which reintroduces the
  F14 risk; prefer a forward fix.
* **Take a verified backup before upgrading.** Use the fixed `scripts/backup.sh`, which
  now fails if objects are missing. **Do not rely on backups made with the old
  script:** they contain no documents.
* **Behaviour and defaults changed in this release:**

  | Setting | Change |
  |---|---|
  | `JOB_LEASE_SECONDS` | 900 → 300, renewed by a 30 s heartbeat |
  | New settings | `JOB_TIMEOUT_SECONDS`, `DIGITIZE_TIMEOUT_SECONDS`, `DIGITIZE_PAGE_TIMEOUT_SECONDS`, `JOB_HEARTBEAT_SECONDS` |
  | Upload rate limit | Now enforced (`UPLOAD_RATE_LIMIT_PER_MINUTE`, default 120/min) |
  | Tesseract | Runs single-threaded unless `OMP_THREAD_LIMIT` is set |
  | Postgres container memory | Limited to 2 GiB |

  The settings validator refuses incoherent budgets at start-up, so a bad rollback of
  the configuration fails fast rather than misbehaving.
* **Images:** pin the image digests from the release build. Rolling back the image
  without rolling back migration 0013 is safe: the old code uses only the
  `idempotency_key` column, and the new partial unique index admits its per-job keys.
* **In-flight work:** jobs are durable in Postgres. Stop workers, deploy, start workers;
  the sweeper re-dispatches QUEUED, RETRY_SCHEDULED and expired-lease jobs. The drill
  verified a job queued across a full restore.
