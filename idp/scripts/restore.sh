#!/usr/bin/env sh
# Restore a backup made by scripts/backup.sh into the running Compose stack.
#
#   scripts/restore.sh <backup-dir>
#
# DESTRUCTIVE: replaces the database contents and overwrites bucket objects.
# Stop api and worker first:  docker compose stop api worker
set -eu

SOURCE=${1:?usage: scripts/restore.sh <backup-dir>}
SOURCE=$(cd "$SOURCE" && pwd)
(cd "$SOURCE" && sha256sum -c --quiet SHA256SUMS)

printf 'This replaces the database and bucket contents. Type "restore" to continue: '
read -r answer
[ "$answer" = "restore" ] || { echo "aborted"; exit 1; }

docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < "$SOURCE/postgres.dump"
docker compose run --rm --no-deps -T -v "$SOURCE:/backup:ro" --entrypoint /bin/sh minio-init -c \
  'mc alias set dst http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null &&
   mc mirror --quiet --overwrite /backup/objects "dst/$BUCKET"'
# Re-apply runtime-role grants (restored tables are owned by the migration role).
docker compose run --rm migrate
echo "restored from $SOURCE; start the stack: docker compose up -d"
