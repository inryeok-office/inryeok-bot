# Threat model

The review boundary treats GitHub webhook bodies, pull-request source, paths,
comments, documents, and Codex output as untrusted data. Those values are never
allowed to change the system review policy or become shell commands.

Protected resources include the GitHub App private key, installation tokens,
webhook and OAuth secrets, database credentials, Codex authentication files,
administrator sessions, PostgreSQL data, and other review workspaces.

The main threats are prompt injection, path traversal and symlink escapes,
environment-variable leakage, replayed webhooks, oversized payloads, resource
exhaustion, API partial failure, CSRF, and accidental disclosure through logs or
error messages.

Current controls include HMAC verification and delivery idempotency, account and
permission checks, bounded webhook bodies, read-only Codex execution, a child
environment allowlist, per-job workspaces, path and symlink validation, bounded
diff and output sizes, PostgreSQL row locking, CSRF tokens, OAuth state checks,
secret redaction, and non-root containers with dropped capabilities.

The Codex read-only sandbox is not treated as a complete filesystem or network
boundary unless verified on the deployed platform. The worker still requires its
Codex authentication volume, so production deployment must validate the actual
container and CLI policy before accepting untrusted repositories.
