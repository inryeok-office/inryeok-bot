# Unattended operations

The watchdog runs in dry-run mode unless `--apply` is explicitly supplied.
It records stale Webhook/Job state using bounded identifiers only. A Job with
an already-persisted `execution_id` is never automatically re-run: it is
classified as `UNKNOWN_OUTCOME` and requires operator confirmation. Only a
stale RUNNING Job with no execution identity may be re-queued, subject to the
existing attempt limit.

Operational incidents are keyed, cooldown-limited, and safe-context only. The
optional `OPS_ALERT_WEBHOOK_URL` is empty by default; configuring it is an
operator action. Notifier failures do not change a Job outcome.

`CODEX_MODEL_CATALOG_JSON` is an operator-managed JSON array. Only entries
with `enabled=true` and `availability_status=VERIFIED` can be selected. The
legacy `CODEX_MODEL_ALLOWLIST` remains compatible when the catalog is empty.
The catalog is never populated by scraping an undocumented CLI endpoint.

Backup `.partial` cleanup is a separate dry-run command. It preserves active
partials and only applies when an operator explicitly passes `--apply`.
