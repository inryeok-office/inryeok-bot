import pytest
from sqlalchemy import select
from test_integration import FakeGitHub

from app.codex.runner import FakeRunner
from app.codex.schemas import ReviewOutput
from app.jobs.models import GlobalReviewSettings, RepositorySettings, ReviewJob, TriggerType
from app.review.service import ReviewService, ReviewSkipped


class NoExecutionRunner(FakeRunner):
    async def run(self, *args, **kwargs):
        raise AssertionError("Codex must not be called")


@pytest.mark.asyncio
async def test_manual_command_is_skipped_when_head_changes_before_execution(app_client):
    _, factory = app_client

    class NewHeadGitHub(FakeGitHub):
        async def get_pull_request(self, *args: object) -> dict[str, object]:
            return {
                "state": "open",
                "draft": False,
                "merged": False,
                "head": {"sha": "c" * 40},
            }

    async with factory() as session:
        session.add(
            RepositorySettings(installation_id=1, repository_owner="acme", repository_name="repo")
        )
        job = ReviewJob(
            delivery_id="manual-stale-head",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.COMMAND,
            trigger_action="command",
        )
        session.add(job)
        await session.commit()
        with pytest.raises(ReviewSkipped) as caught:
            await ReviewService(
                session, NewHeadGitHub(), NoExecutionRunner(ReviewOutput(summary="", findings=[]))
            ).execute(job)
        assert caught.value.outcome_code == "STALE_HEAD"
        assert await session.scalar(select(ReviewJob).where(ReviewJob.head_sha == "c" * 40)) is None


@pytest.mark.asyncio
async def test_historical_synchronize_job_is_terminal_without_external_execution(app_client):
    _, factory = app_client

    async with factory() as session:
        session.add(GlobalReviewSettings(id=1, review_on_synchronize=True))
        session.add(
            RepositorySettings(installation_id=1, repository_owner="acme", repository_name="repo")
        )
        job = ReviewJob(
            delivery_id="runtime-sync",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.AUTO,
            trigger_action="synchronize",
        )
        session.add(job)
        await session.commit()
        with pytest.raises(ReviewSkipped) as caught:
            await ReviewService(
                session, FakeGitHub(), NoExecutionRunner(ReviewOutput(summary="", findings=[]))
            ).execute(job)
        assert caught.value.outcome_code == "MANUAL_REREVIEW_REQUIRED"
        assert await session.scalar(select(ReviewJob).where(ReviewJob.head_sha == "c" * 40)) is None
