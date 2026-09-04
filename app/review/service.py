import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.codex.prompt import build_prompt
from app.codex.runner import ReviewRunner
from app.github.client import GitHubClient
from app.jobs.models import (
    FindingRecord,
    GlobalReviewSettings,
    RepositorySettings,
    ReviewJob,
    ReviewRun,
    TriggerType,
)
from app.review.deduplicator import fingerprint
from app.review.diff import RepositoryCheckout
from app.review.publisher import build_review_payload
from app.review.settings import EffectiveReviewSettings, resolve
from app.review.validator import validate_findings_with_diagnostics

logger = logging.getLogger(__name__)


class ReviewSkipped(RuntimeError):
    pass


class ReviewService:
    def __init__(self, session: AsyncSession, github: GitHubClient, runner: ReviewRunner) -> None:
        self.session, self.github, self.runner = session, github, runner

    async def execute(self, job: ReviewJob) -> None:
        if not self.github.settings.github_account_allowed(job.repository_owner):
            raise ReviewSkipped("repository account is not allowed")
        config = await self.session.scalar(
            select(RepositorySettings).where(
                RepositorySettings.installation_id == job.installation_id,
                RepositorySettings.repository_owner == job.repository_owner,
                RepositorySettings.repository_name == job.repository_name,
            )
        )
        global_settings = await self.session.get(GlobalReviewSettings, 1)
        if global_settings is None:
            global_settings = GlobalReviewSettings(id=1)
            self.session.add(global_settings)
            await self.session.flush()
        if config is None:
            raise ReviewSkipped("repository is disabled")
        effective = resolve(global_settings, config, self.github.settings)
        if not effective.enabled:
            raise ReviewSkipped("repository is disabled")
        patterns = list(effective.ignored_paths)
        await self._execute_checkout(job, config, patterns, effective)

    async def _execute_checkout(
        self,
        job: ReviewJob,
        config: RepositorySettings,
        patterns: list[str],
        effective: EffectiveReviewSettings,
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
                    "min_confidence": effective.minimum_confidence,
                    "max_findings": effective.max_findings,
                    "include_low_severity": effective.include_low_severity,
                    "language": effective.language,
                    "review_profile": effective.review_profile,
                    "minimum_severity": effective.minimum_severity,
                    "enabled_categories": effective.enabled_categories,
                    "ignore_patterns": patterns,
                },
                manager.diff_text,
            )
            output = await self.runner.run(
                checkout, prompt, effective.model, effective.codex_timeout_seconds
            )
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
        validation = validate_findings_with_diagnostics(
            output.findings,
            changed,
            effective.minimum_confidence,
            effective.include_low_severity,
            effective.max_findings,
            existing,
            effective.minimum_severity,
            effective.enabled_categories,
            effective.review_profile,
        )
        findings = validation.findings
        changed_lines_count = sum(len(file.added_lines) for file in changed.values())
        run = ReviewRun(
            job_id=job.id,
            base_sha=job.base_sha,
            head_sha=job.head_sha,
            summary=output.summary,
            reviewed_file_count=len(changed),
            finding_count=len(findings),
            changed_files_count=len(changed),
            changed_lines_count=changed_lines_count,
            codex_exit_code=0,
            codex_output_present=True,
            raw_findings_count=len(output.findings),
            schema_valid_findings_count=len(output.findings),
            changed_file_findings_count=validation.changed_file_count,
            changed_line_findings_count=validation.changed_line_count,
            confidence_findings_count=validation.confidence_count,
            severity_findings_count=validation.severity_count,
            evidence_findings_count=validation.evidence_count,
            deduplicated_findings_count=validation.deduplicated_count,
            published_findings_count=validation.published_count,
            github_review_id=None,
        )
        logger.info(
            "Review diagnostics job=%s files=%s lines=%s raw=%s schema=%s changed_file=%s "
            "changed_line=%s confidence=%s severity=%s evidence=%s deduplicated=%s published=%s",
            job.id,
            len(changed),
            changed_lines_count,
            len(output.findings),
            len(output.findings),
            validation.changed_file_count,
            validation.changed_line_count,
            validation.confidence_count,
            validation.severity_count,
            validation.evidence_count,
            validation.deduplicated_count,
            validation.published_count,
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
                findings,
                len(changed),
                job.head_sha,
                job.trigger_type == TriggerType.RETRY,
                effective.language,
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
