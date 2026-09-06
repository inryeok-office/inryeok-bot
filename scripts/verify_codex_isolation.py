"""Deterministic host-sandbox checks for the dedicated Codex executor."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PROFILE = "inryeok_review_read_only"


def _sandbox_command(codex: str, workspace: Path, *command: str) -> list[str]:
    return [
        codex,
        "sandbox",
        "--permission-profile",
        PROFILE,
        "--sandbox-state-disable-network",
        "--cd",
        str(workspace),
        "--",
        *command,
    ]


def _run(codex: str, workspace: Path, *command: str) -> bool:
    try:
        result = subprocess.run(  # noqa: S603 - fixed canary command only
            _sandbox_command(codex, workspace, *command),
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=20,
            check=False,
            env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _blocked(codex: str, workspace: Path, *command: str) -> bool:
    return not _run(codex, workspace, *command)


def run_check(codex: str, root_base: Path) -> int:
    if not root_base.is_dir() or not os.access(root_base, os.W_OK):
        print("RESULT=NOT_PROVEN")
        return 2
    with tempfile.TemporaryDirectory(prefix="inryeok-isolation-", dir=root_base) as root_name:
        root = Path(root_name)
        workspace = root / "job"
        outside = root / "outside.txt"
        other_job = root / "other-job.txt"
        codex_home = root / "codex-home"
        secrets = root / "secrets"
        workspace.mkdir()
        codex_home.mkdir()
        secrets.mkdir()
        (workspace / "changed.txt").write_text("canary", encoding="utf-8")
        outside.write_text("canary", encoding="utf-8")
        other_job.write_text("canary", encoding="utf-8")
        (codex_home / "canary").write_text("canary", encoding="utf-8")
        (secrets / "canary").write_text("canary", encoding="utf-8")

        checks = {
            "workspace_read": _run(codex, workspace, "/bin/cat", "changed.txt"),
            "workspace_write_blocked": _blocked(
                codex, workspace, "/bin/sh", "-c", "printf x > write-test"
            ),
            "outside_read_blocked": _blocked(codex, workspace, "/bin/cat", str(outside)),
            "other_job_read_blocked": _blocked(codex, workspace, "/bin/cat", str(other_job)),
            "codex_home_read_blocked": _blocked(
                codex, workspace, "/bin/cat", str(codex_home / "canary")
            ),
            "secrets_read_blocked": _blocked(codex, workspace, "/bin/cat", str(secrets / "canary")),
            "external_network_blocked": _blocked(
                codex, workspace, "/usr/bin/curl", "--connect-timeout", "2", "https://example.com"
            ),
            "localhost_blocked": _blocked(
                codex, workspace, "/usr/bin/curl", "--connect-timeout", "1", "http://127.0.0.1:1"
            ),
        }
        failed = [name for name, passed in checks.items() if not passed]
        print("RESULT=PASS" if not failed else "RESULT=FAIL")
        for name, passed in checks.items():
            print(f"{name}={'PASS' if passed else 'FAIL'}")
        return 0 if not failed else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", default=shutil.which("codex") or "codex")
    parser.add_argument(
        "--root", type=Path, default=Path("/var/lib/inryeok-bot-executor/workspaces")
    )
    args = parser.parse_args()
    raise SystemExit(run_check(args.codex, args.root))
