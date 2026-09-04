# Operations

The GitHub App works after installation with the global review defaults.
Repository settings in the admin dashboard override only fields that are set.
Unset overrides inherit the global value.

Profiles are conservative, balanced (the default), and thorough. They change the
review prompt and validation policy without lowering the confidence floor.
Model choices are limited to the server-side `CODEX_MODEL_ALLOWLIST`.
An empty value uses the Codex CLI default model.

Use a fixed HTTPS `PUBLIC_BASE_URL` for production.
Cloudflare Quick Tunnels are for development testing only.
Administrators sign in with GitHub and must have repository admin permission.

## Database backup and restore

Create a Git-ignored `backups` directory in the Compose project.
Then run `scripts/backup_postgres.ps1`.
The script writes a partial file first and renames it only after `pg_dump` succeeds.

```powershell
./scripts/backup_postgres.ps1
```

To restore, stop web and worker first.
Then pipe a reviewed backup into `psql` in the same container.
Restoration changes data and must be performed deliberately by an operator.
