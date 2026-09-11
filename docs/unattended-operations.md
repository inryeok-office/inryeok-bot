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

The PostgreSQL `codex_model_catalog` table is the sole production source of
truth. Web, worker, and executor do not read a catalog file or environment
JSON. Only entries with `enabled=true` and `availability_status=VERIFIED` and
at least one successful effort verification can be selected. The legacy
`CODEX_MODEL_ALLOWLIST` and JSON settings are retained only for isolated test
or import fixtures and are never promoted automatically.

The installed CLI does not expose a stable public machine-readable model
listing. An operator must therefore obtain a model ID from the installed
CLI's documented help/configuration or an approved provider record, then use
the safe catalog command. The command never calls a model and defaults to
read-only inspection:

```text
python scripts/manage_models.py inspect
python scripts/manage_models.py add-candidate --model MODEL_ID --display-name "Display name" --description "운영 용도" --apply --actor ACTOR --reason "approved candidate"
python scripts/verify_codex_model.py --model MODEL_ID --effort medium --execution-id MODEL_VERIFY_ID
python scripts/manage_models.py list
```

Candidate addition and verification recording are separate audited database
transactions. `verify_codex_model.py` starts and completes one bounded
verification record through the executor socket. Failed or unknown results
remain in the database as non-selectable evidence and are never retried by the
command. The executor receives the worker's immutable verification snapshot;
it does not connect to PostgreSQL.

Backup `.partial` cleanup is a separate dry-run command. It preserves active
partials and only applies when an operator explicitly passes `--apply`.
