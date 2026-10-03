# CLAUDE.md — working agreement for this codebase

Read `ARCHITECTURE.md` before changing anything structural. It is the source of
truth; update it in the same change when a decision moves.

## Non-negotiables

1. **Deterministic core.** Validation, permissions, money math, DB writes,
   workflow execution and external actions are plain code. LLMs may classify,
   extract, interpret and *recommend*; they never execute actions or bypass the
   workflow engine.
2. **Ports and adapters.** Orchestration code depends on `Protocol`s, never on a
   vendor SDK. New OCR/LLM/storage/extraction engines are new adapters, not edits
   to the orchestrator.
3. **No fake AI.** An unconfigured provider reports `not_configured`. Development
   doubles are named `Mock*`, set `is_mock=True`, and are labelled in API and UI.
4. **Local-first.** Never send document content to a cloud provider unless the
   tenant's `ProcessingPolicy` allows it. Enforce in the router *and* the gateway.
5. **Postgres is the system of record.** Queue messages carry IDs only; workers
   reload state and must be idempotent.
6. **Every important change is audited** in the same transaction
   (`application/audit.py`). `audit_logs` is append-only (DB trigger).
7. **Tenant isolation.** Tenant id comes from the `Principal` only. Repositories
   for tenant-owned data require `tenant_id`. Other tenants' rows are `404`.
8. **Human review is a state, not an error** (`WAITING_FOR_HUMAN`).
9. **Versioned configuration.** Schemas, workflows, provider configs are
   immutable once a run references them; reprocessing creates a new run.

## Layout and layering

```
backend/src/idp/
  api/             routers + DTOs + deps (auth, permission guards). No business logic.
  application/     use cases; own transactions; call domain + ports
  domain/          pure Python: errors, identity/RBAC, lifecycle, (taxonomy, rules…)
  infrastructure/  SQLAlchemy, Redis, S3, JWT, hashing, logging
  providers/       processing adapters (phase 3+)
  workers/         arq entrypoints → application services
  container.py     the only place adapters are chosen (plus workers/main.py)
frontend/src/
  components/ui      shadcn-style primitives      components/layout   shell, nav
  features/<area>/   api hooks + pages per product area
  lib/               api client (problem+json → ApiError), session, utils
```

`domain` imports nothing from other layers. Routes never touch ORM sessions for
writes except through services. API responses are DTOs, never ORM objects.

## Conventions

* **Errors:** raise a subclass of `idp.domain.errors.IDPError` with the right
  `ErrorCategory`. Messages must be safe for clients (no document text, secrets,
  paths). The API maps categories to status codes in `api/errors.py`.
* **Schema changes:** only via Alembic (`alembic revision --autogenerate`),
  reviewed by hand, with a working `downgrade()`. CI runs `alembic check`.
  Index/constraint names come from the naming convention in `db/base.py`.
* **Logging:** `get_logger(__name__)`, structured key/values, IDs only. Never log
  document content, passwords or tokens (the redaction list in
  `infrastructure/logging.py` is a safety net, not permission).
* **Storage keys:** always from `build_key()`; never derived from filenames.
* **Config:** add fields to `config.Settings` and `.env.example` together; secrets
  are `SecretStr`.
* **Frontend:** server state via TanStack Query hooks in `features/<area>/api.ts`;
  no secrets or API origins in the bundle; show `ApiError.category` and
  `correlationId` on failures; planned features appear as disabled nav entries,
  never as mock pages.
* **Workflow steps** implement `StepHandler` (`application/workflows.py`): do slow
  I/O first, then write through `ctx.session`; never commit; be safe to re-run.
  The runner commits results with the step record after re-checking the lease.
* **Parsing untrusted files** happens in the probe/provider process pool, never
  in the API process or a worker thread.
* No `TODO` without a phase reference (e.g. `# Phase 9: …`).

## Before you push

```bash
# backend (from idp/backend) — integration tests need real Postgres + Redis
.venv/bin/ruff check src tests migrations && .venv/bin/ruff format --check src tests migrations
.venv/bin/mypy
TEST_DATABASE_URL=postgresql+asyncpg://idp:idp@localhost:5432/idp_test \
TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest

# frontend (from idp/frontend)
npm run lint && npm run typecheck && npm test && npm run build
```

A feature is done when it is implemented, tested (unit + integration/API where
it touches I/O), wired into API/UI, documented, error-handled, observable and
secure — not when it compiles.
