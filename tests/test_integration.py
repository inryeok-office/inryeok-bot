from pathlib import Path

import pytest
from sqlalchemy import select

from app.codex.runner import FakeRunner
from app.codex.schemas import Finding, ReviewOutput, Severity
from app.config import Settings
from app.jobs.models import RepositorySettings, ReviewJob, ReviewRun, TriggerType
from app.review.diff import ChangedFile
from app.review.service import ReviewService


class Tokens:
    async def get(self, installation_id: int) -> str:
        return "token"


class FakeGitHub:
    def __init__(self) -> None:
        self.settings = Settings(environment="test", work_root=Path("work-test"))
        self.tokens = Tokens()
        self.payload = None

    async def create_review(self, *args: object) -> dict[str, int]:
        self.payload = args[-1]
        return {"id": 99}


class FakeCheckout:
    def __init__(self, *_: object) -> None:
        self.path = Path(".")

    async def __aenter__(self) -> Path:
        return self.path

    async def fetch_and_diff(self, *_: object) -> dict[str, ChangedFile]:
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
