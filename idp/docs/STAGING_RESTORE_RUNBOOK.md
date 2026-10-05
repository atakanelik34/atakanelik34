# Staging restore drill (production-like data) — runbook

Purpose: prove that a backup of the **production** database and object store restores
completely into a **separate staging environment** (go-live condition 4 in
[GO_LIVE_READINESS.md](GO_LIVE_READINESS.md)).
`scripts/validation/restore_drill.py` already proves the procedure on Compose. It
seeds data and destroys its own stack, so it must **never** be pointed at production.

**Guardrails**
* Every command that touches production is **read-only**:
  * a `READ ONLY` transaction;
  * `pg_dump`;
  * object reads (`aws s3 sync` with production as the *source* only).
* Restores go only into **newly created** staging resources: a new database instance
  and a new bucket. Never restore into the production database or bucket, or into any
  existing staging data you want to keep.
* The staging copy contains production documents and personal data. Give it the same
  access controls, encryption and retention as production, and delete it at the end
  (step 7).

## Inputs required (not available in the validation sandbox)

| Input | Example | Who provides it |
|---|---|---|
| `PROD_RO_URL` | `postgresql://idp_readonly@prod-db:5432/idp?sslmode=require` — a role with `SELECT` on all tables (owner or read-only replica). With an RLS-bound role the scripts set `app.tenant_id='*'`. | DBA |
| `PROD_BUCKET` | `s3://idp-documents-prod` — read-only credentials | Platform |
| `STAGING_OWNER_URL` | `postgresql://idp@staging-restore-db:5432/idp` — **new, empty** Postgres 16 database, owner role | DBA |
| `POSTGRES_APP_PASSWORD` (staging) | password for the staging `idp_app` runtime role | Secret manager (never in the repo) |
| `STAGING_BUCKET` | `s3://idp-restore-drill-YYYYMMDD` — **new, empty** bucket, same SSE/KMS settings as production | Platform |
| Staging app configuration | the production `.env` with the database, S3 endpoint/bucket and secrets pointed at staging; `ENVIRONMENT=staging` | Platform |
| Tools | `psql`/`pg_dump`/`pg_restore` 16, `aws` CLI v2, this repository at the release commit | Operator |

If the production backup mechanism is a managed snapshot rather than `pg_dump`
(RDS/Cloud SQL), restore that snapshot into the new staging instance in step 4 instead.
In step 2, take the fingerprint from a read replica stopped at the snapshot time, or
skip the row-hash comparison and rely on steps 5b–5d.

Rehearsed in the validation sandbox (2026-10-05) for the database half:
* snapshot export, fingerprint and `pg_dump --snapshot`, then restore into a new database;
* `diff` of the fingerprints: identical across 29 tables.

The object-store half (`aws s3`) needs real S3 and was not rehearsed.

## 1. Prepare

```bash
export PROD_RO_URL=... PROD_BUCKET=... STAGING_OWNER_URL=... STAGING_BUCKET=...
mkdir -p drill && cd drill
psql "$STAGING_OWNER_URL" -Atc "select count(*) from pg_tables where schemaname='public'"   # must be 0
aws s3 ls "$STAGING_BUCKET" --recursive | wc -l                                              # must be 0
```

## 2. Consistent backup and fingerprint of production (read-only)

Session A holds one snapshot open; the fingerprint and the dump both use it.

```bash
# terminal A — keep open until step 2 finishes
psql "$PROD_RO_URL" -X
  BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
  SELECT pg_export_snapshot();          -- e.g. 00000003-0000001B-1
```

```bash
# terminal B
export SNAP=00000003-0000001B-1
{ echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;"; echo "SET TRANSACTION SNAPSHOT '$SNAP';";
  cat ../scripts/restore/fingerprint.sql; echo "COMMIT;"; } \
  | psql "$PROD_RO_URL" -X -q -tA -F '|' > prod-fingerprint.txt
{ echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;"; echo "SET TRANSACTION SNAPSHOT '$SNAP';";
  cat ../scripts/restore/object_refs.sql; echo "COMMIT;"; } \
  | psql "$PROD_RO_URL" -X -q -tA | sort -u > prod-object-refs.txt
time pg_dump "$PROD_RO_URL" --snapshot="$SNAP" --format=custom --no-owner -f prod.dump
sha256sum prod.dump > prod.dump.sha256
```

Then `ROLLBACK;` in terminal A.

## 3. Copy the objects (production is the source only)

Copy **after** the dump, so every object the dump references already exists.
Storage keys are written before their database rows.

```bash
time aws s3 sync "$PROD_BUCKET" "$STAGING_BUCKET" --only-show-errors
aws s3 ls "$PROD_BUCKET" --recursive | awk '{print $4}' | sort > prod-objects.txt
```

If production backups are an S3 replica or a versioned backup bucket, sync from that
instead. That also exercises the real backup copy.

## 4. Restore into staging

```bash
# --no-privileges: the dump's GRANTs name idp_app, which a new instance does not have yet;
# provision-db-roles below creates the role and applies the grants (incl. audit_logs REVOKE).
time pg_restore --no-owner --no-privileges --exit-on-error --dbname "$STAGING_OWNER_URL" prod.dump
# runtime role + grants (same as the Compose `migrate` service), then confirm the schema head
docker run --rm -e DATABASE_URL="${STAGING_OWNER_URL/postgresql:/postgresql+asyncpg:}" \
  -e POSTGRES_APP_PASSWORD idp-backend:<release> \
  sh -c 'alembic current && idp provision-db-roles --role idp_app'
```

`alembic current` must print the release head (0013 at the time of writing).

## 5. Verify

```bash
# a) every table identical (row count + content hash)
psql "$STAGING_OWNER_URL" -X -q -tA -F '|' -f ../scripts/restore/fingerprint.sql > staging-fingerprint.txt
diff prod-fingerprint.txt staging-fingerprint.txt && echo "DATABASE IDENTICAL"

# b) every object the database references exists in the restored bucket
aws s3 ls "$STAGING_BUCKET" --recursive | awk '{print $4}' | sort > staging-objects.txt
comm -23 prod-object-refs.txt staging-objects.txt > missing-objects.txt
wc -l < missing-objects.txt          # must be 0

# c) object set identical to production at copy time
diff prod-objects.txt staging-objects.txt && echo "OBJECTS IDENTICAL"

# d) content check on a sample (sizes/ETags can differ for multipart copies; compare bytes)
shuf -n 50 prod-object-refs.txt | while read -r key; do
  a=$(aws s3 cp "$PROD_BUCKET/$key" - | sha256sum); b=$(aws s3 cp "$STAGING_BUCKET/$key" - | sha256sum)
  [ "$a" = "$b" ] || echo "DIFFERS $key"; done
```

Start the staging application (api + worker) on the restored database and bucket, then check:

| Check | How | Expected |
|---|---|---|
| Login | an operator account known to exist | 200 |
| Documents, review tasks, actions, audit | UI or `GET /documents`, `/reviews`, `/documents/{id}/actions`, `/audit-logs` | Same counts as `prod-fingerprint.txt` |
| Page images | open three documents in the viewer | Images render (objects resolved through the restored bucket) |
| New processing | upload a test PDF into a **test tenant** created on staging | Reaches a terminal state |
| RLS as `idp_app` | `PGPASSWORD=… psql -h … -U idp_app -c "select count(*) from documents"` without a tenant context | `0` |
| Audit append-only | as `idp_app`: `UPDATE audit_logs SET action='x'` (staging only) | Refused |
| Queued work | jobs that were `QUEUED`/`RUNNING` in the dump | Completed or dead-lettered by the sweeper within minutes; none stuck |

**Cut every outbound path before the staging worker starts.** Otherwise approved
but unexecuted actions, and undelivered outbox events, in the copy would post to real
ERP, webhook and e-mail endpoints:

1. In the staging configuration, set `WEBHOOK_ALLOWED_HOSTS=`, `ENRICHMENT_ALLOWED_HOSTS=`
   and `SMTP_HOST=` (empty). The allow-lists then refuse every host.
2. On staging only:
   `UPDATE connections SET is_active = false WHERE kind <> 'mock_erp';`

## 6. Record

Copy into GO_LIVE_READINESS.md §7:
* dump size and duration;
* object count, bytes and sync duration;
* `pg_restore` duration;
* time from start to first successful login (RTO for this data volume);
* the snapshot time (RPO reference);
* the result of every check in step 5.

## 7. Tear down

Delete the staging database instance, the staging bucket and `drill/` (dump,
listings). They contain production data. Record the deletion.
