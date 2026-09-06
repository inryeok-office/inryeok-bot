# Trivy exception register

This register is intentionally narrow and expires on 2026-10-07. It does not
replace the full Trivy report.

- `CVE-2026-56854`: scoped to
  `pkg:golang/golang.org/x/crypto@v0.52.0` in the pinned Caddy image. The
  deployed Caddy configuration is HTTPS reverse proxy only and exposes no SSH
  module or `NewServerConn` execution path. Revalidate if the Caddy digest or
  configuration changes.
- `private-key`: scoped to
  `/etc/ssl/private/ssl-cert-snakeoil.key` in the pinned official PostgreSQL
  image. It is a Debian snakeoil sample, is not referenced by PostgreSQL
  runtime configuration, and is not an operational credential. Revalidate if
  the PostgreSQL digest or image build changes.

The exception file must be passed explicitly to Trivy, and scans without it remain the source-of-truth full result. Expired entries fail the release gate.
