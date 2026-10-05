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
# Runs as root inside the mc container: the image's default user (uid 1001)
# cannot write into a host directory owned by the operator, and mc then reported
# success while writing nothing (found by the phase 13 restore drill). The copy is
# verified against the bucket listing, and handed back to the invoking user.
docker compose run --rm --no-deps -T -u 0:0 -v "$TARGET:/backup" \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" --entrypoint /bin/sh minio-init -c \
  'set -eu
   mc alias set src http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
   mkdir -p /backup/objects
   mc mirror --quiet --overwrite "src/$BUCKET" /backup/objects
   expected=$(mc ls --recursive "src/$BUCKET" | wc -l)
   actual=$(find /backup/objects -type f | wc -l)
   chown -R "$HOST_UID:$HOST_GID" /backup/objects
   echo "objects: bucket=$expected backup=$actual"
   [ "$expected" -eq "$actual" ] || { echo "object backup incomplete" >&2; exit 1; }'

(cd "$TARGET" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS)
echo "done: $TARGET"
