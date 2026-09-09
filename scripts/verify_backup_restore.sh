#!/usr/bin/env bash
set -Eeuo pipefail

# Restore a plain PostgreSQL dump into an explicitly temporary container and
# verify only structural metadata.  This script must never use the production
# postgres volume.  It intentionally does not print database contents.

BACKUP_FILE="${1:-}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:17-alpine}"
KEEP_TEMP="${KEEP_TEMP:-0}"
LOCK_DIR="${TMPDIR:-/tmp}/inryeok-bot-backup-restore.lock"

if [[ -z "$BACKUP_FILE" ]]; then
  echo "usage: $0 BACKUP_FILE" >&2
  exit 2
fi
if [[ ! -f "$BACKUP_FILE" || ! -r "$BACKUP_FILE" ]]; then
  echo "backup file is not readable" >&2
  exit 2
fi
if [[ ! "$KEEP_TEMP" =~ ^[01]$ ]]; then
  echo "KEEP_TEMP must be 0 or 1" >&2
  exit 2
fi
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 2; }
docker image inspect "$POSTGRES_IMAGE" >/dev/null 2>&1 || {
  echo "postgres image is not available locally; refusing an implicit pull" >&2
  exit 2
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another restore verification is already running" >&2
  exit 2
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
container="inryeok-restore-verify-$stamp"
volume="inryeok-restore-verify-$stamp"
password="$(openssl rand -hex 32 2>/dev/null || true)"
if [[ -z "$password" ]]; then
  echo "openssl is required to create the temporary database password" >&2
  rmdir "$LOCK_DIR"
  exit 2
fi

cleanup() {
  local status=$?
  if [[ "$KEEP_TEMP" == 0 ]]; then
    docker rm -f "$container" >/dev/null 2>&1 || true
    # The name is generated above and is deliberately restricted to this
    # script's prefix; never use a wildcard or volume prune here.
    docker volume rm "$volume" >/dev/null 2>&1 || true
  else
    echo "temporary restore retained: $container / $volume" >&2
  fi
  rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

docker volume create "$volume" >/dev/null
docker run -d --name "$container" \
  --volume "$volume:/var/lib/postgresql/data" \
  --env POSTGRES_DB=reviewbot \
  --env POSTGRES_USER=reviewbot \
  --env "POSTGRES_PASSWORD=$password" \
  "$POSTGRES_IMAGE" >/dev/null

ready=0
for _ in $(seq 1 60); do
  if docker exec "$container" pg_isready -U reviewbot -d reviewbot >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  echo "temporary postgres did not become ready" >&2
  exit 1
fi

# The dump is fed to psql without echoing its contents.  ON_ERROR_STOP makes a
# syntactically valid but partially restored backup fail closed.
docker exec -i "$container" psql -v ON_ERROR_STOP=1 -U reviewbot -d reviewbot \
  >/dev/null < "$BACKUP_FILE"

version="$(docker exec "$container" psql -Atq -U reviewbot -d reviewbot \
  -c 'SELECT version_num FROM alembic_version LIMIT 1;' 2>/dev/null || true)"
if [[ -z "$version" ]]; then
  echo "restored database has no Alembic revision" >&2
  exit 1
fi

for table in review_jobs review_runs finding_records admin_audit_logs; do
  exists="$(docker exec "$container" psql -Atq -U reviewbot -d reviewbot \
    -c "SELECT to_regclass('public.$table') IS NOT NULL;" 2>/dev/null || true)"
  if [[ "$exists" != t ]]; then
    echo "restored database is missing a required table" >&2
    exit 1
  fi
done

echo "backup restore verification succeeded (revision present; required tables present)"
