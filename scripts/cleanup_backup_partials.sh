#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/inryeok-bot}"
STALE_SECONDS="${STALE_SECONDS:-86400}"
APPLY=0
if [[ "${1:-}" == "--apply" ]]; then APPLY=1; fi

find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.partial' -mmin "+$((STALE_SECONDS / 60))" -print | while IFS= read -r file; do
  # A running official backup holds this lock; never remove an active partial.
  if fuser "$file" >/dev/null 2>&1; then
    echo "active partial preserved: $file"
    continue
  fi
  if (( APPLY )); then
    rm -- "$file"
    echo "removed stale partial: $file"
  else
    echo "dry-run stale partial: $file"
  fi
done
