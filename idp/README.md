# IDP Platform

An extensible Intelligent Document Processing platform: documents arrive from
any channel, get digitized, classified, split, extracted, validated, enriched,
reviewed by humans when needed, and turned into business actions — with
provenance and an audit trail for every step.

The design is **deterministic where possible, AI-assisted where useful, and
human-controlled where necessary**. No OCR engine, LLM, storage or extraction
technique is load-bearing: each sits behind a provider interface and is chosen
per document by a routing engine that respects a local-first data policy.

* Architecture, domain model, lifecycle, provider interfaces, API and roadmap: [`ARCHITECTURE.md`](ARCHITECTURE.md)
* Working agreement for contributors and coding agents: [`CLAUDE.md`](CLAUDE.md)
* Reference flow that inspired the agent stages: [`docs/reference/ocr.jpg`](docs/reference/ocr.jpg)

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 0 | Architecture | ✅ |
| 1 | Foundation: API, worker, Postgres + migrations, Redis, object storage, auth/RBAC, audit, health, UI shell, Compose, CI | ✅ |
| 1.5 | Architecture review fixes | ✅ |
| 2 | Ingestion + execution skeleton: streamed upload, type detection, dedupe, jobs with lease/checkpoints/retries/dead-letter/sweeper/replay, native-vs-scanned page probe, documents UI with processing timeline | ✅ |
| 3 | Digitization: native text + geometry, page rendering, OCR port (Tesseract bundled, labelled mock), document viewer | ✅ |
| 4 | Taxonomy: versioned schemas, templates, rule classifier, page-level splitting, document-type editor | ✅ |
| 5 | Extraction: regex, key/value and table providers, normalisation, confidence, provenance, result contract, extraction panel with source highlighting | ✅ |
| 6 | Validation: rule registry (field + cross-field), required/confidence checks, stored outcomes | ✅ |
| 7 | Human review: review queue, workspace (viewer + fields + validation + evidence), corrections, approve/reject/send-back, audit log UI | ✅ |
| 8 | Routing: signal-based router with policy enforcement, staged fallback, circuit breakers, cost tracking, route trace; evaluation datasets/runs; processing monitor, workflows, providers and evaluation UI | ✅ |
| 9 | LLM: gateway (allow-list, policy, retries, breaker, usage), OpenAI-compatible/Anthropic/Ollama adapters + labelled mock, grounded LLM extraction, LLM classification fallback, tenant processing policy | ✅ |
| 10 | Enrichment: connections (CSV master data, allow-listed REST, labelled mock ERP), deterministic vendor matching, lookup validation rule, connections UI | ✅ |
| 11 | Actions: approval-gated, idempotent business actions (signed webhook, SMTP, labelled mock ERP), transactional outbox with subscribed webhooks, API keys and Inbox | ✅ |
| 12 | Hardening: Postgres RLS + runtime DB role, ClamAV, per-principal rate limits, Prometheus metrics, optional OpenTelemetry, operations dashboard, non-root read-only containers with resource limits, backups, security review, Compose E2E | ✅ |

All roadmap phases are implemented. Screenshots from the Compose end-to-end run are in [`docs/screenshots/`](docs/screenshots).

## Quick start (Docker)

Requirements: Docker with Compose v2.

```bash
cd idp
make env        # writes .env with random secrets and prints the first login
make up         # docker compose up --build -d
```

* UI: <http://localhost:8080> — sign in with the credentials `make env` printed
* API docs (OpenAPI): <http://localhost:8080/api/v1/docs> (the API is reached only through the frontend proxy; its port is not published)
* MinIO console: <http://localhost:9001>

Without `make`: `python3 scripts/gen-env.py && docker compose up --build`.

Services: `postgres`, `redis`, `minio`, `minio-init` (one-shot: bucket plus a
bucket-scoped app user, so the API never holds MinIO root credentials),
`migrate` (one-shot `alembic upgrade head`, receives only `DATABASE_URL`), `bootstrap` (one-shot, creates the first tenant/owner if
`IDP_BOOTSTRAP_PASSWORD` is set; idempotent), `api`, `worker`, `frontend`
(nginx serving the SPA and proxying `/api`). An optional local LLM runtime is
available with `docker compose --profile llm up` (used from phase 9).

Scale workers horizontally with `docker compose up -d --scale worker=3`; each
one shows up on the dashboard with its own heartbeat.

> **MinIO image (development only).** The upstream `minio/minio` image is no
> longer published on Docker Hub, so Compose defaults to the pinned, unmaintained
> `bitnamilegacy/minio` build. Fine for local development; set `MINIO_IMAGE` to
> another S3-compatible server or a mirror you trust. Production should use real
> S3 or a maintained S3-compatible store (see below).

### Building behind a TLS-intercepting proxy

If `pip`/`npm` fail during `docker compose build` with certificate errors, pass
your corporate CA as a BuildKit secret (it is never stored in an image layer):

```bash
EXTRA_CA_CERT=/path/to/corporate-ca.pem \
  docker compose -f docker-compose.yml -f docker-compose.proxy-ca.example.yml up --build
```

## Production deployment notes

The Compose file is a development stack. For production:

* **Object storage:** AWS S3 (or a maintained S3-compatible service) with
  bucket-scoped credentials, SSE (`S3_SSE`) and versioning; not the dev MinIO image.
* **Database:** managed or replicated PostgreSQL. Run migrations as the owner
  role and the API/workers as the runtime role created by
  `idp provision-db-roles` (no table ownership, subject to row-level security,
  `audit_logs` insert/select only) — the Compose file already does this.
* **Network:** only the reverse proxy is public; set `FORWARDED_ALLOW_IPS` on
  the API to the proxy's address/range so client IPs (rate limits, audit) can't
  be spoofed. Terminate TLS at the ingress.
* **Secrets:** injected per service by your secret manager — each service gets
  only what it needs, as in the Compose file.
* **Malware scanning:** set `MALWARE_SCANNER=clamav` and run clamd
  (`docker compose --profile av up -d` in development). Uploads fail closed
  (503) while the scanner is unreachable.
* **Observability:** scrape `http://api:8000/metrics` (set `METRICS_TOKEN`) and
  each worker on port 9100; set `OTEL_EXPORTER_OTLP_ENDPOINT` and install the
  `otel` extra for tracing.
* **Backups:** `make backup` (`scripts/backup.sh`) writes a Postgres dump and a
  mirror of the bucket with checksums; `scripts/restore.sh <dir>` restores
  (destructive, asks for confirmation). Encrypt and ship backups off-host and
  test restores regularly.
* **Hardening already in the images/Compose:** non-root processes, read-only
  root filesystems, tmpfs scratch, dropped capabilities, `no-new-privileges`,
  CPU/memory limits. Pin image digests in your registry. See
  [SECURITY.md](SECURITY.md) for the threat model and residual risks.

### Upgrading an existing installation to phase 12

Add `POSTGRES_APP_PASSWORD` to `.env` (any long random value); the `migrate`
service creates the runtime role and the API/workers switch to it.

## Local development (without Docker)

Backend (Python ≥ 3.11, PostgreSQL 16, Redis 7):

```bash
cd idp/backend
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
export DATABASE_URL=postgresql+asyncpg://idp:idp@localhost:5432/idp
.venv/bin/alembic upgrade head                                   # needs only DATABASE_URL
export JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
export REDIS_URL=redis://localhost:6379/0 STORAGE_BACKEND=local LOG_FORMAT=console
IDP_BOOTSTRAP_PASSWORD='choose-a-strong-one' .venv/bin/idp bootstrap \
  --tenant-slug demo --tenant-name "Demo" --email admin@demo.local --name "Admin"
.venv/bin/uvicorn idp.main:create_app --factory --reload        # API on :8000
.venv/bin/arq idp.workers.main.WorkerSettings                      # worker
```

Frontend (Node 22):

```bash
cd idp/frontend
npm ci
npm run dev        # http://localhost:5173, proxies /api to localhost:8000
```

## Quality gates

```bash
make backend-check     # ruff lint + format check + mypy --strict
make backend-test      # pytest; set TEST_DATABASE_URL and TEST_REDIS_URL for integration tests
make frontend-check    # oxlint, tsc, vitest, production build
```

Integration tests run against real PostgreSQL and Redis (never SQLite or
mocks). Without `TEST_DATABASE_URL`/`TEST_REDIS_URL` they are reported as
skipped, not silently passed. CI (`.github/workflows/idp-ci.yml`) runs all of
the above plus a migration drift check and Docker image builds.

## Configuration

All configuration is environment variables (see [`.env.example`](.env.example)),
validated at startup by `backend/src/idp/config.py`:

* `ENVIRONMENT=development|test|production`. Production refuses local-disk
  storage and wildcard CORS.
* `JWT_SECRET` must be ≥ 32 characters; the process will not start otherwise.
* Secrets are never logged; `.env` is git-ignored and written with mode `600`.

### Processing policy and LLM providers

* `PROCESSING_MODE` (default `LOCAL_ONLY`) is the deployment ceiling; tenants
  can set a stricter policy under *Providers & models*. In `LOCAL_ONLY`, no
  document content is sent to a cloud provider — the router and the LLM gateway
  both refuse it.
* LLMs are optional. Without one, extraction is deterministic and anything
  uncertain goes to human review. To add a local model:

  ```bash
  # .env: OLLAMA_BASE_URL=http://ollama:11434  OLLAMA_MODEL=llama3.1:8b
  docker compose --profile llm up -d
  docker compose exec ollama ollama pull llama3.1:8b
  docker compose up -d worker
  ```

* Cloud models additionally need their host in `LLM_ALLOWED_HOSTS`
  (e.g. `api.anthropic.com`) and a tenant policy that allows cloud processing.
  Usage and cost per provider are shown on the *Providers & models* page.

## Security notes

See [SECURITY.md](SECURITY.md) for the full review. Highlights:

### Foundation (phase 1)

* Passwords: argon2id; generic login errors; constant-time path for unknown
  emails; login rate-limited per IP + email in Redis.
* Tokens: short-lived HS256 JWTs carrying a `token_version`; deactivating a
  user or bumping the version revokes outstanding tokens.
* RBAC with roles `owner > admin > operator > reviewer > viewer`; admins cannot
  mint owners. Tenant scope always comes from the token, never the request.
* `audit_logs` is append-only, enforced by a database trigger.
* Responses carry security headers and an `X-Correlation-ID`; errors are RFC 9457
  problem+json with an error category and never include stack traces or
  submitted values.
* Encryption at rest: enable S3 SSE with `S3_SSE`; Postgres volume encryption is
  provided by your infrastructure (encrypted disks / managed database).
  TLS terminates at your ingress or load balancer.
