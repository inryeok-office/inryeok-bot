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

`CODEX_MODEL_CATALOG_JSON` or `CODEX_MODEL_CATALOG_FILE` is an operator-managed
JSON array. Only entries with `enabled=true` and
`availability_status=VERIFIED` can be selected. The legacy
`CODEX_MODEL_ALLOWLIST` is candidate input only and is never promoted to a
selectable model without verification metadata. The catalog is never
populated by scraping an undocumented CLI endpoint.

The installed CLI does not expose a stable public machine-readable model
listing. An operator must therefore obtain a model ID from the installed
CLI's documented help/configuration or an approved provider record, then use
the safe catalog command. The command never calls a model and defaults to
read-only inspection:

```text
python scripts/manage_models.py inspect --catalog-file /etc/inryeok-bot/model-catalog.json
python scripts/manage_models.py add-candidate --catalog-file /etc/inryeok-bot/model-catalog.json --model MODEL_ID --efforts medium,high --apply --actor ACTOR --reason "approved candidate"
python scripts/verify_codex_model.py --effort medium --execution-id MODEL_VERIFY_ID
python scripts/manage_models.py record-verification --catalog-file /etc/inryeok-bot/model-catalog.json --model MODEL_ID --cli-version CLI_VERSION --schema-hash SCHEMA_HASH --verification-id MODEL_VERIFY_ID --apply --actor ACTOR --reason "isolated verification passed"
```

The runtime must be configured with `CODEX_MODEL_CATALOG_FILE` pointing to the
same restrictive file before a verified entry becomes selectable. Candidate
addition and verification recording are separate audited operator actions;
failed or unknown verification results must not be recorded as `VERIFIED`.

Backup `.partial` cleanup is a separate dry-run command. It preserves active
partials and only applies when an operator explicitly passes `--apply`.
