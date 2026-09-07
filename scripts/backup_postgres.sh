#!/usr/bin/env bash
set -Eeuo pipefail

# Create an atomic PostgreSQL dump without putting credentials in argv or logs.
APP_DIR="${APP_DIR:-/opt/inryeok-bot/app}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/inryeok-bot}"
RETENTION_DAYS="${RETENTION_DAYS:-0}"

case "$BACKUP_DIR" in
  /var/backups/inryeok-bot|/var/backups/inryeok-bot/*) ;;
  *) echo "backup directory is outside the approved location" >&2; exit 2 ;;
esac
if [[ ! -d "$APP_DIR" || ! -f "$APP_DIR/compose.yml" ]]; then
  echo "compose project is not available" >&2
  exit 2
fi
if [[ ! "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  echo "RETENTION_DAYS must be a non-negative integer" >&2
  exit 2
fi

umask 077
install -d -m 0700 -- "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$BACKUP_DIR/inryeok-bot-$stamp.sql"
partial="$final.partial"
cleanup() { rm -f -- "$partial"; }
trap cleanup EXIT

if ! docker compose --project-directory "$APP_DIR" exec -T postgres \
  pg_dump -U reviewbot reviewbot >"$partial" 2>/dev/null; then
  echo "pg_dump failed; no completed backup was created" >&2
  exit 1
fi
if [[ ! -s "$partial" ]]; then
  echo "pg_dump produced an empty backup" >&2
  exit 1
fi
chmod 0600 -- "$partial"
mv -n -- "$partial" "$final"
if [[ ! -f "$final" ]]; then
  echo "backup finalization failed" >&2
  exit 1
fi
chmod 0600 -- "$final"

if (( RETENTION_DAYS > 0 )); then
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type f \
    -name 'inryeok-bot-*.sql' -mtime "+$RETENTION_DAYS" -delete
fi
printf '%s\n' "$final"
