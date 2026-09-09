#!/usr/bin/env bash
set -Eeuo pipefail

# Synchronize the separately installed executor with the application source.
# The executor is intentionally outside the web/worker image, so rebuilding
# those images alone can leave an older schema/diagnostic implementation live.
APP_ROOT="${APP_ROOT:-/opt/inryeok-bot/app}"
EXECUTOR_VENV="${EXECUTOR_VENV:-/opt/inryeok-bot/executor/venv}"
SERVICE="${SERVICE:-inryeok-codex-executor.service}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "run as root" >&2
  exit 2
fi
[[ -f "${APP_ROOT}/pyproject.toml" ]] || { echo "application source not found" >&2; exit 2; }
[[ -x "${EXECUTOR_VENV}/bin/pip" ]] || { echo "executor virtualenv not found" >&2; exit 2; }

"${EXECUTOR_VENV}/bin/pip" install --no-deps "${APP_ROOT}" >/dev/null
systemctl restart "${SERVICE}"
systemctl is-active --quiet "${SERVICE}"

# Hashes are safe deployment evidence; source and credentials are not printed.
sha256sum \
  "${APP_ROOT}/app/codex/runner.py" \
  "${EXECUTOR_VENV}/lib"/*/site-packages/app/codex/runner.py
