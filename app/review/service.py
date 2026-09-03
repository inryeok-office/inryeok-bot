from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.codex.prompt import build_prompt
from app.codex.runner import ReviewRunner
from app.github.client import GitHubClient
from app.jobs.models import FindingRecord, RepositorySettings, ReviewJob, ReviewRun, TriggerType
from app.review.deduplicator import fingerprint
from app.review.diff import RepositoryCheckout
from app.review.publisher import build_review_payload
from app.review.validator import validate_findings


class ReviewService:
    def __init__(self, session: AsyncSession, github: GitHubClient, runner: ReviewRunner) -> None:
        self.session, self.github, self.runner = session, github, runner

    async def execute(self, job: ReviewJob) -> None:
        config = await self.session.scalar(
            select(RepositorySettings).where(
                RepositorySettings.installation_id == job.installation_id,
                RepositorySettings.repository_owner == job.repository_owner,
                RepositorySettings.repository_name == job.repository_name,
            )
        )
        if config is None or not config.enabled:
            raise RuntimeError("repository is disabled")
        patterns = [line.strip() for line in config.ignore_patterns.splitlines() if line.strip()]
        await self._execute_checkout(job, config, patterns)

    async def _execute_checkout(
        self, job: ReviewJob, config: RepositorySettings, patterns: list[str]
    ) -> None:
        token = await self.github.tokens.get(job.installation_id)
        manager = RepositoryCheckout(
            self.github.settings, job.repository_owner, job.repository_name, token
        )
        async with manager as checkout:
            changed = await manager.fetch_and_diff(job.base_sha, job.head_sha, patterns)
            prompt = build_prompt(
                job.base_sha,
                job.head_sha,
                list(changed),
                {
                    "min_confidence": config.min_confidence,
                    "max_findings": config.max_findings,
                    "include_low_severity": config.include_low_severity,
                    "ignore_patterns": patterns,
                },
            )
            output = await self.runner.run(checkout, prompt)
        existing = set(
            (
                await self.session.scalars(
                    select(FindingRecord.fingerprint)
                    .join(ReviewRun, FindingRecord.review_run_id == ReviewRun.id)
                    .join(ReviewJob, ReviewRun.job_id == ReviewJob.id)
                    .where(
                        ReviewJob.repository_owner == job.repository_owner,
                        ReviewJob.repository_name == job.repository_name,
                        ReviewJob.pull_request_number == job.pull_request_number,
                    )
                )
            ).all()
        )
        findings = validate_findings(
            output.findings,
            changed,
            config.min_confidence,
            config.include_low_severity,
            config.max_findings,
            existing,
        )
        run = ReviewRun(
            job_id=job.id,
            base_sha=job.base_sha,
            head_sha=job.head_sha,
            summary=output.summary,
            reviewed_file_count=len(changed),
            finding_count=len(findings),
            github_review_id=None,
        )
        self.session.add(run)
        await self.session.flush()
        if findings:
            payload = build_review_payload(
                findings, len(changed), job.head_sha, job.trigger_type == TriggerType.RETRY
            )
            posted = await self.github.create_review(
                job.installation_id,
                job.repository_owner,
                job.repository_name,
                job.pull_request_number,
                payload,
            )
            run.github_review_id = int(posted["id"])
        for finding in findings:
            self.session.add(
                FindingRecord(
                    review_run_id=run.id,
                    path=finding.path,
                    line=finding.line,
                    severity=finding.severity.value,
                    confidence=finding.confidence,
                    title=finding.title,
                    fingerprint=fingerprint(finding),
                    github_comment_id=None,
                )
            )
        await self.session.commit()
