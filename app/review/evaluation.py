"""Opt-in local quality evaluation for the production review pipeline.

Run inside the worker container only: ``python -m app.review.evaluation --run``.
It creates disposable local git repositories and never contacts GitHub or persists
prompts, source text, process diagnostics, or credentials.
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from app.codex.prompt import build_prompt
from app.codex.runner import CodexError, CodexRunner
from app.codex.schemas import Finding
from app.config import Settings, get_settings
from app.jobs.models import ReviewDomainMode
from app.review.diff import ChangedFile, parse_unified_diff
from app.review.domains import PROMPT_VERSION, detect_domains, effective_domains
from app.review.validator import validate_findings_with_diagnostics

MAX_CALLS = 3
STOP_CODES = {"CODEX_QUOTA", "CODEX_RATE_LIMIT", "CODEX_AUTH", "CODEX_SERVICE_UNAVAILABLE"}


@dataclass(frozen=True)
class ExpectedIssue:
    id: str
    domain: str
    path: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class GroupSpec:
    name: str
    base_files: dict[str, str]
    changed_files: dict[str, str]
    expected: tuple[ExpectedIssue, ...]


@dataclass(frozen=True)
class EvaluationReport:
    group: str
    detected_domains: tuple[str, ...]
    effective_domains: tuple[str, ...]
    detection_reasons: tuple[str, ...]
    prompt_version: str
    changed_files: int
    changed_lines: int
    exit_category: str
    raw_findings: int
    schema_valid: int
    changed_line_valid: int
    evidence_valid: int
    final_findings: int
    expected_status: dict[str, str]
    unexpected_findings: int
    elapsed_seconds: float


def _groups() -> tuple[GroupSpec, ...]:
    return (
        GroupSpec(
            "A-general-backend-database",
            {"README.md": "evaluation baseline\n", "api/service.py": "def ok():\n    return 1\n"},
            {
                "api/service.py": """def delete_account(repository, account_id: int | None):
    account = repository.find(account_id)
    return account.name

def remove_orders(repository, customer_id: str):
    return repository.delete_all()

def load_orders(repository, ids):
    return [repository.find(order_id) for order_id in ids]
""",
                "db/migrations/002_accounts.sql": """ALTER TABLE accounts
ADD COLUMN external_id INTEGER NOT NULL;
UPDATE accounts SET external_id = 0;
""",
            },
            (
                ExpectedIssue("nullable", "GENERAL", "api/service.py", ("none", "null", "account")),
                ExpectedIssue("delete-scope", "BACKEND", "api/service.py", ("delete", "all")),
                ExpectedIssue(
                    "repeated-lookup", "BACKEND", "api/service.py", ("loop", "repository", "query")
                ),
                ExpectedIssue(
                    "external-id",
                    "DATABASE",
                    "db/migrations/002_accounts.sql",
                    ("integer", "overflow", "external"),
                ),
                ExpectedIssue(
                    "migration-compat",
                    "DATABASE",
                    "db/migrations/002_accounts.sql",
                    ("not null", "existing", "migration"),
                ),
            ),
        ),
        GroupSpec(
            "B-web-mobile",
            {
                "README.md": "evaluation baseline\n",
                "web/src/App.tsx": "export const App = () => null;\n",
            },
            {
                "web/package.json": '{"dependencies":{"react":"^18.0.0"}}\n',
                "web/src/Search.tsx": """import { useEffect, useState } from "react";
export function Search({ query }: { query: string }) {
  const [html, setHtml] = useState("");
  useEffect(() => { fetch(`/api/search?q=${query}`).then(r => r.text()).then(setHtml); }, [query]);
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}
""",
                "mobile/lib/profile.dart": """class ProfileController {
  bool disposed = false;
  void load() { fetchProfile().then((value) { setState(value); }); }
  void dispose() { disposed = true; }
}
""",
                "mobile/pubspec.yaml": "name: mobile_fixture\n",
            },
            (
                ExpectedIssue(
                    "stale-response",
                    "WEB_FRONTEND",
                    "web/src/Search.tsx",
                    ("stale", "request", "query"),
                ),
                ExpectedIssue(
                    "unsafe-html", "WEB_FRONTEND", "web/src/Search.tsx", ("html", "xss", "unsafe")
                ),
                ExpectedIssue(
                    "disposed-state",
                    "MOBILE",
                    "mobile/lib/profile.dart",
                    ("dispose", "state", "async"),
                ),
            ),
        ),
        GroupSpec(
            "C-infra-data-library",
            {
                "README.md": "evaluation baseline\n",
                "src/lib.py": "def stable(value):\n    return value\n",
            },
            {
                "infra/Dockerfile": """FROM python:3.12
ARG DEPLOY_SECRET
RUN echo $DEPLOY_SECRET > /tmp/build-note
USER root
""",
                "ml/training.py": """def train(rows):
    train_rows = rows
    test_rows = rows
    return fit(train_rows), evaluate(test_rows)

def infer(client, prompt):
    return client.complete(prompt).text
""",
                "sdk/public_api.py": """def create_client(endpoint: str, timeout: int):
    return Client(endpoint, timeout)
""",
                "cli/command.py": """def main(args):
    write_output(args[0])
    return 0
""",
            },
            (
                ExpectedIssue(
                    "secret-layer",
                    "INFRASTRUCTURE",
                    "infra/Dockerfile",
                    ("secret", "image", "layer"),
                ),
                ExpectedIssue(
                    "data-leakage", "DATA_AI", "ml/training.py", ("train", "test", "leak")
                ),
                ExpectedIssue(
                    "model-validation",
                    "DATA_AI",
                    "ml/training.py",
                    ("response", "validate", "structured"),
                ),
                ExpectedIssue(
                    "api-break",
                    "LIBRARY_SDK_CLI",
                    "sdk/public_api.py",
                    ("api", "compat", "default"),
                ),
            ),
        ),
    )


def _run_git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed executable and fixture arguments
        ["git", *args],  # noqa: S607 - fixed executable and fixture arguments
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")


def create_fixture(spec: GroupSpec, parent: Path) -> tuple[Path, str, str]:
    root = parent / spec.name
    root.mkdir()
    _run_git(["init", "--initial-branch=main"], root)
    _run_git(["config", "user.email", "evaluation@localhost"], root)
    _run_git(["config", "user.name", "Inryeok Evaluation"], root)
    _write(root, spec.base_files)
    _run_git(["add", "."], root)
    _run_git(["commit", "-m", "baseline"], root)
    base = _run_git(["rev-parse", "HEAD"], root)
    _write(root, spec.changed_files)
    _run_git(["add", "."], root)
    _run_git(["commit", "-m", "review fixtures"], root)
    return root, base, _run_git(["rev-parse", "HEAD"], root)


def _diff(root: Path, base: str, head: str) -> tuple[str, dict[str, ChangedFile]]:
    diff = _run_git(
        ["diff", "--no-ext-diff", "--unified=0", "--find-renames", f"{base}...{head}", "--"], root
    )
    return diff, parse_unified_diff(diff)


def _expected_status(spec: GroupSpec, findings: Sequence[Finding]) -> tuple[dict[str, str], int]:
    # Keyword comparison is deliberately conservative: ambiguous model wording is surfaced.
    pending = set(range(len(findings)))
    statuses: dict[str, str] = {}
    for expected in spec.expected:
        candidates = [
            index
            for index in pending
            if getattr(findings[index], "path", "") == expected.path
            and all(
                keyword in (f"{findings[index].title} {findings[index].body}".casefold())
                for keyword in expected.keywords[:1]
            )
        ]
        if candidates:
            pending.remove(candidates[0])
            statuses[expected.id] = "TRUE_POSITIVE"
        else:
            same_path = any(getattr(item, "path", "") == expected.path for item in findings)
            statuses[expected.id] = "NEEDS_MANUAL_REVIEW" if same_path else "MISSED"
    return statuses, len(pending)


async def evaluate_group(spec: GroupSpec, settings: Settings) -> EvaluationReport:
    started = time.monotonic()
    parent = Path(tempfile.mkdtemp(prefix="inryeok-domain-eval-"))
    try:
        checkout, base, head = create_fixture(spec, parent)
        diff, changed = _diff(checkout, base, head)
        detection = detect_domains(list(changed))
        domains = effective_domains(ReviewDomainMode.AUTO.value, None, detection)
        prompt = build_prompt(
            base,
            head,
            list(changed),
            {
                "language": "ko",
                "review_profile": "BALANCED",
                "review_domains": domains,
                "max_findings": 10,
                "minimum_confidence": 0.9,
            },
            diff,
        )
        try:
            output = await CodexRunner(settings).run(
                checkout, prompt, timeout=settings.review_timeout_seconds
            )
        except CodexError as exc:
            return EvaluationReport(
                spec.name,
                detection.domains,
                domains,
                detection.reasons,
                PROMPT_VERSION,
                len(changed),
                sum(len(item.added_lines) for item in changed.values()),
                exc.code,
                0,
                0,
                0,
                0,
                0,
                {item.id: "NOT_RUN" for item in spec.expected},
                0,
                round(time.monotonic() - started, 2),
            )
        result = validate_findings_with_diagnostics(
            output.findings,
            changed,
            0.9,
            False,
            10,
            minimum_severity="MEDIUM",
            review_profile="BALANCED",
        )
        statuses, unexpected = _expected_status(spec, result.findings)
        return EvaluationReport(
            spec.name,
            detection.domains,
            domains,
            detection.reasons,
            PROMPT_VERSION,
            len(changed),
            sum(len(item.added_lines) for item in changed.values()),
            "SUCCESS",
            len(output.findings),
            len(output.findings),
            result.changed_line_count,
            result.evidence_count,
            result.published_count,
            statuses,
            unexpected,
            round(time.monotonic() - started, 2),
        )
    finally:
        shutil.rmtree(parent, ignore_errors=True)


async def run_all(settings: Settings, group_name: str | None = None) -> list[EvaluationReport]:
    reports: list[EvaluationReport] = []
    for spec in _groups():
        if group_name and spec.name != group_name:
            continue
        if len(reports) >= MAX_CALLS:
            break
        report = await evaluate_group(spec, settings)
        reports.append(report)
        if report.exit_category in STOP_CODES:
            break
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="consume at most three Codex evaluations"
    )
    parser.add_argument("--group", choices=[spec.name for spec in _groups()])
    args = parser.parse_args()
    if not args.run:
        parser.error("pass --run to explicitly permit Codex evaluation")
    reports = asyncio.run(run_all(get_settings(), args.group))
    print(json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
