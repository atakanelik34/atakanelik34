-- Read-only: every object key the database references (originals, page layouts and
-- page images). After a restore, each one must exist in the restored bucket.  Usage:
--   psql "$DB_URL" -X -q -tA -f scripts/restore/object_refs.sql | sort -u > refs.txt
\set ON_ERROR_STOP on
SET app.tenant_id = '*';
SELECT storage_key FROM documents
UNION SELECT layout_key FROM document_pages WHERE layout_key IS NOT NULL
UNION SELECT image_key FROM document_pages WHERE image_key IS NOT NULL;
