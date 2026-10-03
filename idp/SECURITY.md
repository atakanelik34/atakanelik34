# Security review

Scope: the IDP platform in this directory as of phase 12. This is the
maintainers' review of threats and the controls that answer them, with the
residual risks we accept or defer. Report vulnerabilities privately to the
repository owner; do not open public issues.

## Assets

Documents and their extracted data (often personal and financial data),
credentials (user passwords, API keys, connection secrets, model API keys),
the audit trail, and the integrity of business actions (ERP postings,
webhooks, e-mail).

## Trust boundaries and controls

| Boundary / threat | Controls |
|---|---|
| **Internet → API** (credential stuffing, abuse) | argon2id passwords; generic login errors; login rate limit per IP+email; per-principal API and upload rate limits (Redis, 429 + Retry-After); short-lived JWTs with `token_version` revocation; API not published — only reachable via the nginx edge, which overwrites `X-Forwarded-For`. |
| **Tenant ↔ tenant** (data leakage) | Tenant id only from the principal; every repository filters by tenant; other tenants' rows are 404. **Postgres RLS** on every tenant table: authenticated requests bind `app.tenant_id`, the runtime role (`idp_app`) owns nothing and cannot bypass RLS; unset context sees nothing (fail closed). A test fails if a tenant table lacks RLS. |
| **Privilege escalation** | RBAC in one place (`domain/identity.py`); admins cannot mint owners; API keys carry scopes intersected with their creator's *current* role and cannot manage keys; business actions require `actions:execute`; tenant policy requires `tenant:manage`. |
| **Uploaded files** (malware, parser exploits, zip/pixel bombs) | Streamed, size-capped uploads; magic-byte MIME allow-list; ClamAV (clamd INSTREAM) adapter that fails closed; parsing (pdfium, Pillow) only in a separate process pool with timeouts, page and memory limits; filenames sanitised and never used for storage keys. |
| **Document content → model** (prompt injection) | Prompts mark document text as untrusted; models only *return field candidates*, which are schema-validated, normalised and grounded against the text (ungrounded values are capped at 0.3 and need a human). Models cannot trigger actions: only actions declared in the document type run, after the deterministic workflow (and by default human approval). |
| **Data residency** | `LOCAL_ONLY` by default; enforced twice (router admission and LLM gateway); provider locality is fail-safe (declared-local providers on non-local hosts count as cloud); tenant policies can only be stricter than the deployment ceiling. |
| **Outbound calls** (SSRF, exfiltration) | Separate host allow-lists for LLMs, enrichment REST and webhooks; redirects are never followed; credentials only via `IDP_SECRET_*` environment variables named in configuration, never stored in the database or returned by the API. |
| **Business actions** (duplicates, tampering) | Idempotency key per (job, part, action); outcomes recorded per action under a row lock; `Idempotency-Key` header on every attempt; HMAC-SHA256 webhook signatures over timestamp + body; e-mail recipients only from configuration; mock providers labelled and refused unless explicitly allowed. |
| **Audit** | Every significant change audited in the same transaction; `audit_logs` append-only by trigger *and* by privileges (runtime role has no UPDATE/DELETE); actor type distinguishes users, API keys and the system. |
| **Secrets** | Settings validate secret presence/length at start; `SecretStr` everywhere; secrets never logged (redaction list as a safety net); role-provisioning errors never echo SQL containing passwords; `.env` written with mode 600 and git-ignored. |
| **Browser** | Strict CSP (`script-src 'self'`, `img-src 'self' data: blob:`, `connect-src 'self'`, `frame-ancestors 'none'`); page images served same-origin through the API; JWT kept in sessionStorage (no cookies → no CSRF); no HTML injection sinks in the UI. |
| **Containers** | Backend and nginx run as non-root, read-only root filesystems, tmpfs scratch, all capabilities dropped, `no-new-privileges`, CPU/memory limits; infrastructure ports bound to 127.0.0.1 in development. |
| **Supply chain** | Lockfile for the frontend (`npm ci`); dependency floors for the backend; CI builds images and checks that the runtime image imports every entry point without dev dependencies. |

## Residual risks and follow-ups

* **Image digests**: base images are pinned by tag, not digest. Pin digests in
  your registry mirror for production.
* **Python lockfile**: backend dependencies use floors; generate a hashed lock
  (e.g. `uv pip compile --generate-hashes`) for reproducible production builds.
* **MinIO** in Compose is a development default (and its signed URLs are only
  used for downloads); use a managed object store with SSE in production.
* **Worker system context**: workers run with RLS context `*` (trusted,
  ID-driven code). Per-job tenant binding in workers is a possible tightening.
* **Webhook receivers** must verify the signature and reject stale timestamps;
  the platform cannot enforce that on the receiving side.
* **E-mail actions** send document data to the configured recipients by
  design; restrict who can configure connections (`config:write`).
* **Office documents** (DOCX/XLSX) are not accepted; conversion would add a
  sandboxed converter and is deferred.
