import hashlib
import json
import logging
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.codex.prompt import build_prompt
from app.codex.runner import ReviewRunner
from app.github.client import GitHubAPIError, GitHubClient
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
from app.review.domains import PROMPT_VERSION, detect_domains, effective_domains
from app.review.model_catalog import catalog_version
from app.review.publisher import build_review_payload, review_marker
from app.review.settings import EffectiveReviewSettings, resolve
from app.review.validator import validate_findings_with_diagnostics

logger = logging.getLogger(__name__)


class ReviewSkipped(RuntimeError):
    def __init__(self, message: str, outcome_code: str = "SKIPPED") -> None:
        super().__init__(message)
        self.outcome_code = outcome_code


class ReviewService:
    def __init__(self, session: AsyncSession, github: GitHubClient, runner: ReviewRunner) -> None:
        self.session, self.github, self.runner = session, github, runner

    async def execute(self, job: ReviewJob, execution_id: str | None = None) -> None:
        # Keep direct callers (tests/administrative runners) on the same
        # durable identity contract as the worker.  The worker commits this
        # value before entering the executor; here we at least bind it before
        # the runner is invoked when the service is used directly.
        execution_id = execution_id or job.execution_id or uuid4().hex
        if job.execution_id is None:
            job.execution_id = execution_id
            await self.session.flush()
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
        # New jobs carry their effective model/effort from enqueue time. Do
        # not silently change an in-flight job when policy is edited later.
        if job.model_source is not None or job.reasoning_source is not None:
            effective = replace(
                effective,
                model=job.model,
                reasoning_effort=job.reasoning_effort or "default",
            )
        # A repeated /review for the same head must not spend another Codex
        # execution merely to discover the existing GitHub marker afterwards.
        # Compare the immutable execution inputs as well as the head so a
        # prompt/profile/model change intentionally permits a fresh review.
        completed_jobs = (
            await self.session.scalars(
                select(ReviewJob)
                .join(ReviewRun, ReviewRun.job_id == ReviewJob.id)
                .where(
                    ReviewRun.github_review_id.is_not(None),
                    ReviewJob.installation_id == job.installation_id,
                    ReviewJob.repository_owner == job.repository_owner,
                    ReviewJob.repository_name == job.repository_name,
                    ReviewJob.pull_request_number == job.pull_request_number,
                    ReviewJob.head_sha == job.head_sha,
                    ReviewJob.prompt_version == PROMPT_VERSION,
                )
            )
        ).all()
        for prior_job in completed_jobs:
            if (
                prior_job.model == effective.model
                and prior_job.reasoning_effort == effective.reasoning_effort
            ):
                raise ReviewSkipped(
                    "same review input already completed", "ALREADY_REVIEWED_CURRENT_HEAD"
                )
        # Preserve the effective policy used by this job for auditability.
        job.model = effective.model
        job.reasoning_effort = effective.reasoning_effort
        job.review_profile = effective.review_profile
        job.model_source = job.model_source or (
            "REPOSITORY_OVERRIDE" if config.override_model is not None else "GLOBAL_DEFAULT"
        )
        job.reasoning_source = job.reasoning_source or (
            "REPOSITORY_OVERRIDE"
            if config.override_reasoning_effort is not None
            else "GLOBAL_DEFAULT"
        )
        job.model_catalog_version = job.model_catalog_version or catalog_version(
            self.github.settings
        )
        # The application schema is the stable source of truth in the web/
        # worker image.  A missing file remains nullable rather than guessed.
        app_schema = Path("review-schema.json")
        if app_schema.is_file():
            job.schema_hash = hashlib.sha256(app_schema.read_bytes()).hexdigest()
        job.codex_cli_version = (
            job.codex_cli_version or self.github.settings.codex_cli_version or None
        )
        job.executor_runtime_version = (
            job.executor_runtime_version or self.github.settings.executor_runtime_version or None
        )
        patterns = list(effective.ignored_paths)
        await self._execute_checkout(job, config, patterns, effective, execution_id)

    async def _execute_checkout(
        self,
        job: ReviewJob,
        config: RepositorySettings,
        patterns: list[str],
        effective: EffectiveReviewSettings,
        execution_id: str | None = None,
    ) -> int:
        token = await self.github.tokens.get(job.installation_id)
        manager = RepositoryCheckout(
            self.github.settings, job.repository_owner, job.repository_name, token
        )
        async with manager as checkout:
            changed = await manager.fetch_and_diff(job.base_sha, job.head_sha, patterns)
            detection = detect_domains(list(changed))
            domains = effective_domains(
                effective.review_domain_mode, effective.manual_review_domains, detection
            )
            job.detected_review_domains = ",".join(detection.domains)
            job.effective_review_domains = ",".join(domains)
            job.detection_reasons = "\n".join(detection.reasons)[:2000]
            job.prompt_version = PROMPT_VERSION
            await self.session.flush()
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
                    "reasoning_effort": effective.reasoning_effort,
                    "minimum_severity": effective.minimum_severity,
                    "enabled_categories": effective.enabled_categories,
                    "review_domains": domains,
                    "prompt_version": PROMPT_VERSION,
                    "ignore_patterns": patterns,
                },
                manager.diff_text,
            )
            output = await self.runner.run(
                checkout,
                prompt,
                effective.model,
                effective.codex_timeout_seconds,
                execution_id,
                reasoning_effort=effective.reasoning_effort,
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
                        ReviewJob.head_sha == job.head_sha,
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
        published_fingerprints = {fingerprint(item) for item in findings}
        prior_run = await self.session.scalar(
            select(ReviewRun)
            .join(ReviewJob, ReviewRun.job_id == ReviewJob.id)
            .where(
                ReviewRun.job_id != job.id,
                ReviewRun.github_review_id.is_not(None),
                ReviewJob.repository_owner == job.repository_owner,
                ReviewJob.repository_name == job.repository_name,
                ReviewJob.pull_request_number == job.pull_request_number,
            )
            .order_by(ReviewRun.id.desc())
            .limit(1)
        )
        prior_fingerprints: set[str] = set()
        if prior_run is not None and prior_run.published_finding_fingerprints:
            try:
                stored = json.loads(prior_run.published_finding_fingerprints)
                if isinstance(stored, list):
                    prior_fingerprints = {value for value in stored if isinstance(value, str)}
            except (TypeError, ValueError):
                prior_fingerprints = set()
        if prior_run is not None and not prior_fingerprints:
            # Legacy runs only persisted inline FindingRecord rows.  Use that
            # bounded fallback without pretending historical FILE/PR records
            # existed when they were never stored.
            prior_fingerprints = set(
                (
                    await self.session.scalars(
                        select(FindingRecord.fingerprint).where(
                            FindingRecord.review_run_id == prior_run.id
                        )
                    )
                ).all()
            )
        comparison = {
            "new": len(published_fingerprints - prior_fingerprints),
            "still": len(published_fingerprints & prior_fingerprints),
            "not_detected": len(prior_fingerprints - published_fingerprints),
        }
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
            scope_valid_findings_count=validation.changed_file_count,
            rejected_findings_count=sum(validation.rejection_counts.values()),
            published_findings_count=validation.published_count,
            rejection_counts=json.dumps(validation.rejection_counts, sort_keys=True),
            published_finding_fingerprints=json.dumps(sorted(published_fingerprints)),
            comparison_new_count=comparison["new"],
            comparison_still_count=comparison["still"],
            comparison_not_detected_count=comparison["not_detected"],
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
            marker = review_marker(
                job.repository_owner,
                job.repository_name,
                job.pull_request_number,
                job.head_sha,
                PROMPT_VERSION,
            )
            existing_reviews = await self.github.list_reviews(
                job.installation_id,
                job.repository_owner,
                job.repository_name,
                job.pull_request_number,
            )
            existing_id = next(
                (
                    int(review["id"])
                    for review in existing_reviews
                    if f"<!-- inryeok-review:{marker} -->" in str(review.get("body", ""))
                ),
                None,
            )
            if existing_id is not None:
                run.github_review_id = existing_id
            else:
                payload = build_review_payload(
                    findings,
                    len(changed),
                    job.head_sha,
                    job.trigger_type in {TriggerType.COMMAND, TriggerType.RETRY},
                    effective.language,
                    marker,
                    {
                        **comparison,
                    },
                )
                try:
                    posted = await self.github.create_review(
                        job.installation_id,
                        job.repository_owner,
                        job.repository_name,
                        job.pull_request_number,
                        payload,
                    )
                except GitHubAPIError as exc:
                    # GitHub rejects the entire review when any one inline
                    # location is no longer valid.  Preserve the review
                    # result by retrying once as a summary-only review; do
                    # not use this fallback for auth/permission/rate-limit
                    # failures, and never invent a line location.
                    if exc.status_code != 422 or not payload.get("comments"):
                        raise
                    fallback_payload = build_review_payload(
                        findings,
                        len(changed),
                        job.head_sha,
                        job.trigger_type in {TriggerType.COMMAND, TriggerType.RETRY},
                        effective.language,
                        marker,
                        {
                            **comparison,
                        },
                        include_inline_comments=False,
                    )
                    logger.warning(
                        "GitHub rejected inline review locations; retrying summary-only "
                        "review job=%s safe_category=GITHUB_INVALID_LOCATION",
                        job.id,
                    )
                    posted = await self.github.create_review(
                        job.installation_id,
                        job.repository_owner,
                        job.repository_name,
                        job.pull_request_number,
                        fallback_payload,
                    )
                except (httpx.TimeoutException, httpx.NetworkError):
                    recovered_reviews = await self.github.list_reviews(
                        job.installation_id,
                        job.repository_owner,
                        job.repository_name,
                        job.pull_request_number,
                    )
                    recovered_id = next(
                        (
                            int(review["id"])
                            for review in recovered_reviews
                            if f"<!-- inryeok-review:{marker} -->" in str(review.get("body", ""))
                        ),
                        None,
                    )
                    if recovered_id is None:
                        raise
                    posted = {"id": recovered_id}
                run.github_review_id = int(posted["id"])
        for finding in findings:
            # FindingRecord is the legacy inline-finding index. FILE/PR
            # findings are retained in the ReviewRun summary and must not be
            # coerced into a fictitious path/line for deduplication.
            if finding.scope.value != "LINE" or finding.path is None or finding.line is None:
                continue
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
        job.terminal_outcome = "SUCCEEDED_NO_FINDINGS" if not findings else "SUCCEEDED"
        await self.session.commit()
        return len(findings)
