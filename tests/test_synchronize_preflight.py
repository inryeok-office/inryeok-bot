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
@pytest.mark.parametrize(
    "state,draft,merged,head,code",
    [
        ("closed", False, False, "b", "PR_NOT_REVIEWABLE"),
        ("open", True, False, "b", "PR_NOT_REVIEWABLE"),
        ("open", False, True, "b", "PR_NOT_REVIEWABLE"),
        ("open", False, False, "c", "STALE_HEAD"),
    ],
)
async def test_synchronize_runtime_preflight(app_client, state, draft, merged, head, code):
    _, factory = app_client

    class GitHub(FakeGitHub):
        async def get_pull_request(self, *args):
            return dict(
                state=state,
                draft=draft,
                merged=merged,
                head={"sha": head * 40},
                base={"sha": "a" * 40},
            )

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
                session, GitHub(), NoExecutionRunner(ReviewOutput(summary="", findings=[]))
            ).execute(job)
        assert caught.value.outcome_code == code
        if code == "STALE_HEAD":
            latest = await session.scalar(select(ReviewJob).where(ReviewJob.head_sha == "c" * 40))
            assert latest is not None and latest.not_before is not None
            assert job.superseded_by_head_sha == latest.head_sha
            assert latest.model_source == "CLI_DEFAULT"
