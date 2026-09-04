# Operations

The GitHub App works after installation with the global review defaults. Repository settings in
the administrator dashboard override only the fields that are explicitly set; unset overrides
inherit the global value.

Profiles are conservative, balanced (the default), and thorough. They change the review prompt
and validation policy without lowering the confidence floor. Model choices are limited to the
server-side `CODEX_MODEL_ALLOWLIST`; an empty value uses the Codex CLI default model.

Use a fixed HTTPS `PUBLIC_BASE_URL` for production. Cloudflare Quick Tunnels are for development
testing only. Administrators sign in with GitHub and must have repository admin permission.

## Database backup and restore

Create a Git-ignored `backups` directory, then run the following from the Compose project. It
uses the PostgreSQL container environment rather than placing a password in the command line.

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
$stamp = Get-Date -Format yyyyMMdd-HHmmss
docker compose exec -T postgres sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' `
  > "backups/inryeok-bot-$stamp.sql"
```

To restore, stop web and worker first, then pipe a reviewed backup into `psql` in the same
container. Restoration changes data and must be performed deliberately by an operator.
