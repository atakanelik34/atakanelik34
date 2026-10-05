-- Read-only fingerprint of every table: row count + MD5 over the ordered row text.
-- Run against the source (inside the backup's snapshot) and against the restored
-- copy; the outputs must be identical.  Usage:
--   psql "$DB_URL" -X -q -tA -F '|' -f scripts/restore/fingerprint.sql > fp.txt
-- Never writes: SET only changes this session's tenant context ('*' = all tenants,
-- needed when the role is subject to row-level security).
\set ON_ERROR_STOP on
SET app.tenant_id = '*';
SELECT format(
  'SELECT %L, count(*), coalesce(md5(string_agg(x::text, %L ORDER BY x::text)), %L) FROM %I x',
  tablename, '|', '', tablename)
FROM pg_tables
WHERE schemaname = current_schema()
ORDER BY tablename
\gexec
