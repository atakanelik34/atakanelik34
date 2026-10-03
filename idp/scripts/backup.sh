#!/usr/bin/env sh
# Back up the Compose stack: a Postgres custom-format dump plus a mirror of the
# document bucket, with checksums. Run from the repository's idp/ directory.
#
#   scripts/backup.sh [target-dir]        (default: backups/<UTC timestamp>)
#
# The database and the bucket are dumped one after the other; objects written in
# between are harmless (orphans), and the dump never references objects that are
# missing because storage keys are written before their database rows.
# Encrypt and ship the result off-host; it contains all documents.
set -eu

TARGET=${1:-backups/$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$TARGET"
TARGET=$(cd "$TARGET" && pwd)
umask 077

echo "postgres -> $TARGET/postgres.dump"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' \
  > "$TARGET/postgres.dump"

echo "objects  -> $TARGET/objects/"
docker compose run --rm --no-deps -T -v "$TARGET:/backup" --entrypoint /bin/sh minio-init -c \
  'mc alias set src http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null &&
   mc mirror --quiet --overwrite "src/$BUCKET" /backup/objects'

(cd "$TARGET" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS)
echo "done: $TARGET"
