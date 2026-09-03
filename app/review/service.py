from contextlib import suppress

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


class ReviewSkipped(RuntimeError):
    pass


class ReviewService:
    def __init__(self, session: AsyncSession, github: GitHubClient, runner: ReviewRunner) -> None:
        self.session, self.github, self.runner = session, github, runner

    async def execute(self, job: ReviewJob) -> None:
        if not self.github.settings.github_account_allowed(job.repository_owner):
            raise ReviewSkipped("repository account is not allowed")
        check = await self.github.create_check_run(
            job.installation_id, job.repository_owner, job.repository_name, job.head_sha
        )
        check_run_id = int(check["id"])
        job.github_check_run_id = check_run_id
        await self.session.commit()
        try:
            config = await self.session.scalar(
                select(RepositorySettings).where(
                    RepositorySettings.installation_id == job.installation_id,
                    RepositorySettings.repository_owner == job.repository_owner,
                    RepositorySettings.repository_name == job.repository_name,
                )
            )
            if config is None or not config.enabled or not config.installed:
                raise ReviewSkipped("repository is disabled")
            patterns = [
                line.strip() for line in config.ignore_patterns.splitlines() if line.strip()
            ]
            finding_count = await self._execute_checkout(job, config, patterns)
            conclusion = "neutral" if finding_count else "success"
            await self.github.complete_check_run(
                job.installation_id,
                job.repository_owner,
                job.repository_name,
                check_run_id,
                conclusion,
                "Review completed",
                f"Reviewed the pull request and found {finding_count} findings.",
            )
        except ReviewSkipped:
            with suppress(Exception):
                await self.github.complete_check_run(
                    job.installation_id,
                    job.repository_owner,
                    job.repository_name,
                    check_run_id,
                    "skipped",
                    "Review skipped",
                    "This pull request is not eligible for review.",
                )
            raise
        except Exception:
            with suppress(Exception):
                await self.github.complete_check_run(
                    job.installation_id,
                    job.repository_owner,
                    job.repository_name,
                    check_run_id,
                    "failure",
                    "Review failed",
                    "The review could not be completed due to an internal error.",
                )
            raise

    async def _execute_checkout(
        self, job: ReviewJob, config: RepositorySettings, patterns: list[str]
    ) -> int:
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
        previous_auto_summary = None
        if job.trigger_type in {TriggerType.AUTO, TriggerType.RETRY}:
            previous_auto_summary = await self.session.scalar(
                select(ReviewRun.id)
                .join(ReviewJob, ReviewRun.job_id == ReviewJob.id)
                .where(
                    ReviewJob.id != job.id,
                    ReviewJob.repository_owner == job.repository_owner,
                    ReviewJob.repository_name == job.repository_name,
                    ReviewJob.pull_request_number == job.pull_request_number,
                    ReviewJob.head_sha == job.head_sha,
                    ReviewJob.trigger_type == TriggerType.AUTO,
                    ReviewRun.github_review_id.is_not(None),
                )
                .limit(1)
            )
        if previous_auto_summary is None:
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
                    category=finding.category.value,
                    confidence=finding.confidence,
                    title=finding.title,
                    fingerprint=fingerprint(finding),
                    github_comment_id=None,
                )
            )
        await self.session.commit()
        return len(findings)
