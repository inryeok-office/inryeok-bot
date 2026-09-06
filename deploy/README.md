# Linux Codex executor deployment

The production executor runs as the host `inryeok-codex-executor.service`, not
as a Compose container. Install the unit only after creating the dedicated
`inryeok-executor` account, its private `CODEX_HOME`, and the worker socket
group. The worker container connects through
`/run/inryeok-bot/executor.sock`; no TCP executor port is published.

Install the managed Codex policy as root at `/etc/codex/requirements.toml`
with mode `0644`. Copy the service unit to `/etc/systemd/system/` and run
`systemctl daemon-reload`. Do not start the service until the dedicated account
has completed device authentication:

```text
sudo -u inryeok-executor env CODEX_HOME=/var/lib/inryeok-bot-executor/codex-home codex login --device-auth
```

The executor account must not be in `sudo` or `docker` groups and must not have
access to `/opt/inryeok-bot/secrets`, database credentials, backups, or the
Compose `review-work` volume.
