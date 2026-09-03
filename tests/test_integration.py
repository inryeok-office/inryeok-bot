from pathlib import Path

import pytest
from sqlalchemy import select

from app.codex.runner import FakeRunner
from app.codex.schemas import Category, Finding, ReviewOutput, Severity
from app.config import Settings
from app.jobs.models import RepositorySettings, ReviewJob, ReviewRun, TriggerType
from app.review.diff import ChangedFile
from app.review.service import ReviewService, ReviewSkipped


class Tokens:
    async def get(self, installation_id: int) -> str:
        return "token"


class FakeGitHub:
    def __init__(self) -> None:
        self.settings = Settings(
            environment="test", work_root=Path("work-test"), allowed_github_accounts="acme"
        )
        self.tokens = Tokens()
        self.payload = None
        self.checks: list[dict[str, object]] = []

    async def create_review(self, *args: object) -> dict[str, int]:
        self.payload = args[-1]
        return {"id": 99}

    async def create_check_run(self, *args: object) -> dict[str, int]:
        self.checks.append({"operation": "create", "arguments": args})
        return {"id": 55}

    async def complete_check_run(self, *args: object) -> dict[str, int]:
        self.checks.append({"operation": "complete", "arguments": args})
        return {"id": 55}


class FakeCheckout:
    diff_arguments: tuple[object, ...] = ()

    def __init__(self, *_: object) -> None:
        self.path = Path(".")

    async def __aenter__(self) -> Path:
        return self.path

    async def fetch_and_diff(self, *args: object) -> dict[str, ChangedFile]:
        type(self).diff_arguments = args
        return {"app.py": ChangedFile("app.py", frozenset({2}))}

    async def __aexit__(self, *_: object) -> None:
        pass


@pytest.mark.asyncio
async def test_fake_end_to_end_worker_pipeline(app_client, monkeypatch) -> None:
    _, factory = app_client
    monkeypatch.setattr("app.review.service.RepositoryCheckout", FakeCheckout)
    output = ReviewOutput(
        summary="found a defect",
        findings=[
            Finding(
                path="app.py",
                line=2,
                category=Category.NULL_SAFETY,
                severity=Severity.HIGH,
                confidence=0.95,
                title="Crash",
                body="This dereferences None.",
            )
        ],
    )
    async with factory() as session:
        session.add(
            RepositorySettings(
                installation_id=1,
                repository_owner="acme",
                repository_name="repo",
            )
        )
        job = ReviewJob(
            delivery_id="integration",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.AUTO,
        )
        session.add(job)
        await session.commit()
        github = FakeGitHub()
        await ReviewService(session, github, FakeRunner(output)).execute(job)  # type: ignore[arg-type]
        run = await session.scalar(select(ReviewRun).where(ReviewRun.job_id == job.id))
        assert run and run.finding_count == 1 and run.github_review_id == 99
        assert github.payload["comments"][0]["side"] == "RIGHT"
        assert github.checks[0]["operation"] == "create"
        assert github.checks[-1]["arguments"][4] == "neutral"
        assert FakeCheckout.diff_arguments[:2] == ("a" * 40, "b" * 40)


@pytest.mark.asyncio
async def test_no_findings_posts_summary_and_completes_success(app_client, monkeypatch) -> None:
    _, factory = app_client
    monkeypatch.setattr("app.review.service.RepositoryCheckout", FakeCheckout)
    async with factory() as session:
        session.add(
            RepositorySettings(installation_id=2, repository_owner="acme", repository_name="repo")
        )
        job = ReviewJob(
            delivery_id="no-findings",
            installation_id=2,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=8,
            base_sha="c" * 40,
            head_sha="d" * 40,
            trigger_type=TriggerType.AUTO,
        )
        session.add(job)
        await session.commit()
        github = FakeGitHub()
        await ReviewService(
            session, github, FakeRunner(ReviewOutput(summary="ok", findings=[]))
        ).execute(job)  # type: ignore[arg-type]
        assert github.payload["comments"] == []
        assert "0 findings" in github.payload["body"]
        assert github.checks[-1]["arguments"][4] == "success"


@pytest.mark.asyncio
async def test_internal_failure_completes_check_with_failure(app_client, monkeypatch) -> None:
    _, factory = app_client
    monkeypatch.setattr("app.review.service.RepositoryCheckout", FakeCheckout)

    class BrokenRunner:
        async def run(self, *_: object) -> ReviewOutput:
            raise RuntimeError("sensitive internal detail")

    async with factory() as session:
        session.add(
            RepositorySettings(installation_id=3, repository_owner="acme", repository_name="repo")
        )
        job = ReviewJob(
            delivery_id="failure",
            installation_id=3,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=9,
            base_sha="e" * 40,
            head_sha="f" * 40,
            trigger_type=TriggerType.AUTO,
        )
        session.add(job)
        await session.commit()
        github = FakeGitHub()
        with pytest.raises(RuntimeError, match="sensitive internal detail"):
            await ReviewService(session, github, BrokenRunner()).execute(job)  # type: ignore[arg-type]
        assert github.checks[-1]["arguments"][4] == "failure"
        assert "sensitive" not in github.checks[-1]["arguments"][6]


@pytest.mark.asyncio
async def test_completed_auto_review_summary_is_not_repeated(app_client, monkeypatch) -> None:
    _, factory = app_client
    monkeypatch.setattr("app.review.service.RepositoryCheckout", FakeCheckout)
    async with factory() as session:
        session.add(
            RepositorySettings(installation_id=4, repository_owner="acme", repository_name="repo")
        )
        previous = ReviewJob(
            delivery_id="previous-auto",
            installation_id=4,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=10,
            base_sha="1" * 40,
            head_sha="2" * 40,
            trigger_type=TriggerType.AUTO,
        )
        session.add(previous)
        await session.flush()
        session.add(
            ReviewRun(
                job_id=previous.id,
                base_sha=previous.base_sha,
                head_sha=previous.head_sha,
                summary="complete",
                github_review_id=88,
                reviewed_file_count=1,
                finding_count=0,
            )
        )
        retry = ReviewJob(
            delivery_id="retry-same-head",
            installation_id=4,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=10,
            base_sha="1" * 40,
            head_sha="2" * 40,
            trigger_type=TriggerType.RETRY,
        )
        session.add(retry)
        await session.commit()
        github = FakeGitHub()
        await ReviewService(
            session, github, FakeRunner(ReviewOutput(summary="ok", findings=[]))
        ).execute(retry)  # type: ignore[arg-type]
        assert github.payload is None
        assert github.checks[-1]["arguments"][4] == "success"


@pytest.mark.asyncio
async def test_disabled_repository_completes_check_as_skipped(app_client) -> None:
    _, factory = app_client
    async with factory() as session:
        session.add(
            RepositorySettings(
                installation_id=5,
                repository_owner="acme",
                repository_name="repo",
                enabled=False,
            )
        )
        job = ReviewJob(
            delivery_id="disabled",
            installation_id=5,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=11,
            base_sha="3" * 40,
            head_sha="4" * 40,
            trigger_type=TriggerType.AUTO,
        )
        session.add(job)
        await session.commit()
        github = FakeGitHub()
        with pytest.raises(ReviewSkipped):
            await ReviewService(
                session, github, FakeRunner(ReviewOutput(summary="unused", findings=[]))
            ).execute(job)  # type: ignore[arg-type]
        assert github.checks[-1]["arguments"][4] == "skipped"
