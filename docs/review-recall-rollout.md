# Risk-aware review rollout

## Contracts and defaults

The pipeline records nullable, non-backfilled manifests and canonical stage
counts. Related source and prompts are transient inputs, not diagnostic database
fields. New revisions 0031, 0032 and 0033 preserve historical rows.

Context selection is deterministic and heuristic, not a complete language call
graph: 12 related files, 16 KiB per file, 96 KiB total, direct-reference depth one,
two configuration and two test reservations. Risk signals require multiple code
evidence families; filenames alone are insufficient. Unrelated, binary, generated
and symlinked support files are excluded. Archives reject traversal, symlinks,
Windows drive paths and expanded input over 50 MB.

`detailed-review-v4-risk-aware` explores independent perspectives without a quota.
All passes use the same snapshot model/effort and canonical grounding validator.
Semantic dedup requires an explicit defect identity, changed symbol, anchor and
trigger/mechanism/consequence chain; legacy candidates retain strict fingerprints.

Selective passes default OFF pending isolated A/B evidence. When enabled, default
maximum is two passes; three requires an explicitly configured limit and complex
independent signals. Default process reservations are 20/hour and 100/day, global
and repository concurrency one, pass timeout 900 seconds bounded by remaining job
time. These limits include GENERAL. Reservations are conservative: UNKNOWN and
ambiguous failures consume capacity and are never automatically retried.

GENERAL failure is terminal. A known extra-pass schema/quota failure retains valid
GENERAL findings with PARTIAL_REVIEW. Unknown executor outcome, permission/auth
failure or non-durable result fails closed. Successful durable retrieval reports
zero newly launched processes; uncertain transport responses remain unknown.

Synchronize retains the existing audited global/repository resolver and configured
60-second debounce. Only PENDING AUTO jobs can be superseded. RUNNING and terminal
rows are preserved. The current head is rechecked before execution; incremental
diff is exploratory context, while full PR diff controls grounding. Previously
published fingerprints suppress duplicate comments without modifying old reviews.
Production activation must use the command service; never enqueue historical PRs.

## Corpus interpretation

Fixtures are pinned to historical SHA pairs and LF-normalized diff hashes. Remote
drift requires explicit investigation rather than silently updating fixtures.
The evaluator compares adjudicated path/symbol/category/causal-chain identities,
not keyword similarity. It does not itself infer identities from arbitrary model
prose. Deterministic acceptance tests prove schema/validator compatibility, not
model recall. Usage and latency remain unknown unless measured.

PR190 contains five initial-head acceptance targets. Later masking rewrites are
excluded from initial recall. PR171 mostly contains follow-up issues, but pinned
history confirms the unversioned bulk UPDATE was introduced in its initial head;
report timing must not be confused with introduction timing. PR348 retains the
historical 2 raw / 2 schema-valid / 0 scope-valid record and validator scenarios;
the original production candidates were not reconstructed or declared confirmed
defects without access to that historical execution.

## Safe deployment gate

Run tests, Ruff, strict mypy, contract preflight, Alembic offline SQL, Compose,
secret scan, image builds and admin browser smoke before pushing. Applied migration
files must not be reformatted to repair a pre-existing formatting violation.

Before production changes, identify the actual host and clean checkout, verify
queue and executor outcomes, obtain a validated non-empty PostgreSQL backup, then
pause through the audited command service. Apply forward migrations and preserve
the PostgreSQL volume. Resume only if processing was originally RUNNING. Observe
for at least five minutes. Do not deploy to an unrelated SSH alias merely because
it is reachable. Keep selective passes OFF without a safe, measured A/B result.

Credential-free deterministic preflight:

```powershell
python -m scripts.verify_review_contracts
python -m pytest
```
