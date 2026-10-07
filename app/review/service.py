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
from app.codex.schemas import Finding
from app.github.client import GitHubAPIError, GitHubClient
from app.jobs.models import (
    FindingRecord,
    GlobalReviewSettings,
    RepositorySettings,
    ReviewFindingDiagnostic,
    ReviewJob,
    ReviewPass,
    ReviewRun,
    TriggerType,
)
from app.review.context import ContextBudget, select_context
from app.review.contracts import validate_prompt_version
from app.review.deduplicator import fingerprint
from app.review.diagnostics import StageCounts, context_manifest
from app.review.diff import RepositoryCheckout, filter_unified_diff, no_reviewable_reason
from app.review.domains import PROMPT_VERSION, detect_domains, effective_domains
from app.review.i18n import CATALOG_VERSION, normalize_locale
from app.review.model_catalog import CLI_DEFAULT, db_catalog_version, load_db_catalog, spec_for
from app.review.passes import execute_passes
from app.review.publisher import build_review_payload, review_marker
from app.review.risks import detect_risks
from app.review.settings import EffectiveReviewSettings, resolve
from app.review.validator import FindingRejectionDiagnostic, validate_findings_with_diagnostics

logger = logging.getLogger(__name__)


class ReviewSkipped(RuntimeError):
    def __init__(self, message: str, outcome_code: str = "SKIPPED") -> None:
        super().__init__(message)
        self.outcome_code = outcome_code


class JobSnapshotPersistenceError(RuntimeError):
    """The executor has not been called because the immutable snapshot failed to persist."""


class ReviewService:
    def __init__(self, session: AsyncSession, github: GitHubClient, runner: ReviewRunner) -> None:
        self.session, self.github, self.runner = session, github, runner
        self._pass_origins: list[str] = []
        self._candidates: list[Finding] = []

    async def _assert_current_command_head(self, job: ReviewJob) -> None:
        """Fail closed if a manual request became stale while queued/running."""
        current = await self.github.get_pull_request(
            job.installation_id,
            job.repository_owner,
            job.repository_name,
            job.pull_request_number,
        )
        if current.get("state") == "closed" or bool(current.get("merged")):
            raise ReviewSkipped("pull request is not reviewable", "PR_NOT_REVIEWABLE")
        if bool(current.get("draft")):
            raise ReviewSkipped("pull request is draft", "PR_NOT_REVIEWABLE")
        raw_head = current.get("head")
        latest_head = str(raw_head.get("sha", "")) if isinstance(raw_head, dict) else ""
        if latest_head and latest_head != job.head_sha:
            raise ReviewSkipped("manual review head is stale", "STALE_HEAD")

    async def _persist_rejection_diagnostics(
        self, run: ReviewRun, diagnostics: list[FindingRejectionDiagnostic]
    ) -> None:
        for diagnostic in diagnostics:
            candidate = self._candidates[diagnostic.finding_index - 1]
            self.session.add(
                ReviewFindingDiagnostic(
                    pass_type=self._pass_origins[diagnostic.finding_index - 1],
                    dedup_fingerprint=fingerprint(candidate),
                    path_present=candidate.path is not None,
                    line_present=candidate.line is not None,
                    job_id=run.job_id,
                    review_run_id=run.id,
                    finding_index=diagnostic.finding_index,
                    scope=diagnostic.scope,
                    category=diagnostic.category,
                    review_type=diagnostic.review_type,
                    relation_to_change=diagnostic.relation_to_change,
                    introduced_by_pr=diagnostic.introduced_by_pr,
                    severity=diagnostic.severity,
                    confidence=diagnostic.confidence,
                    rejection_stage=diagnostic.rejection_stage,
                    rejection_reason=diagnostic.rejection_reason,
                    path_is_changed=diagnostic.path_is_changed,
                    changed_symbol_present=diagnostic.changed_symbol_present,
                    causal_evidence_present=diagnostic.causal_evidence_present,
                    expected_anchor_kind=diagnostic.expected_anchor_kind,
                    anchor_matches=diagnostic.anchor_matches,
                    diagnostic_schema_version=diagnostic.diagnostic_schema_version,
                )
            )
        await self.session.flush()

    async def execute(self, job: ReviewJob, execution_id: str | None = None) -> None:
        if job.trigger_action == "synchronize":
            # Historical pending synchronize jobs must fail closed after the
            # policy change.  They remain durable rows, but cannot launch
            # Codex or publish a stale automatic review.
            raise ReviewSkipped(
                "synchronize rereview requires an explicit /review", "MANUAL_REREVIEW_REQUIRED"
            )
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
        model_catalog = await load_db_catalog(self.session)
        snapshot_bound = job.model_source is not None or job.reasoning_source is not None
        if snapshot_bound and job.model is not None:
            snapshot_spec = spec_for(self.github.settings, job.model, model_catalog)
            if (
                snapshot_spec is None
                or not snapshot_spec.selectable
                or job.model_verification_id is None
            ):
                raise ReviewSkipped(
                    "the model snapshot is no longer selectable; operator review is required",
                    "MODEL_DISABLED_AFTER_ENQUEUE",
                )
        effective = resolve(global_settings, config, self.github.settings, catalog=model_catalog)
        if not effective.enabled:
            raise ReviewSkipped("repository is disabled")
        if job.trigger_action == "command":
            await self._assert_current_command_head(job)
        # New jobs carry their effective model/effort from enqueue time. Do
        # not silently change an in-flight job when policy is edited later.
        if job.model_source is not None or job.reasoning_source is not None:
            effective = replace(
                effective,
                model=job.model,
                reasoning_effort=job.reasoning_effort or "default",
            )
        # Locale is presentation policy too: do not reinterpret queued work
        # after an administrator changes a global or repository setting.
        if job.effective_locale is not None:
            effective = replace(effective, language=normalize_locale(job.effective_locale))
        # A repeated /review for the same head must not spend another Codex
        # execution merely to discover the existing GitHub marker afterwards.
        # The head is the idempotency boundary even if other policy inputs
        # changed after the successful review.
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
        for _prior_job in completed_jobs:
            raise ReviewSkipped(
                "same review input already completed", "ALREADY_REVIEWED_CURRENT_HEAD"
            )
        # Preserve the effective policy used by this job for auditability.
        job.model = effective.model
        job.reasoning_effort = effective.reasoning_effort
        job.review_profile = effective.review_profile
        job.effective_locale = job.effective_locale or normalize_locale(effective.language)
        job.locale_source = job.locale_source or (
            "REPOSITORY_OVERRIDE" if config.override_language is not None else "GLOBAL_DEFAULT"
        )
        job.message_catalog_version = job.message_catalog_version or CATALOG_VERSION
        job.model_source = job.model_source or (
            "REPOSITORY_OVERRIDE" if config.override_model is not None else "GLOBAL_DEFAULT"
        )
        if (
            effective.model is None
            and config.override_model is None
            and global_settings.model is None
        ):
            job.model_source = CLI_DEFAULT
        job.reasoning_source = job.reasoning_source or (
            "REPOSITORY_OVERRIDE"
            if config.override_reasoning_effort is not None
            else "GLOBAL_DEFAULT"
        )
        job.model_catalog_version = job.model_catalog_version or await db_catalog_version(
            self.session
        )
        model_spec = spec_for(self.github.settings, effective.model, model_catalog)
        job.model_catalog_entry_id = job.model_catalog_entry_id or (
            model_spec.model_id if model_spec is not None else None
        )
        job.model_verification_id = job.model_verification_id or (
            (model_spec.verification_id or model_spec.version) if model_spec is not None else None
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
            prompt_diff = filter_unified_diff(manager.diff_text, set(changed))
            incremental = ""
            if job.previous_reviewed_head:
                incremental = await manager.incremental_diff(
                    job.previous_reviewed_head, job.head_sha
                )
                job.incremental_diff_bytes = len(incremental.encode("utf-8"))
            job.context_manifest = context_manifest(
                checkout, manager.diff_text, list(changed)
            ).model_dump()
            signals = detect_risks(prompt_diff)
            pack = select_context(
                checkout,
                list(changed),
                manager.diff_text,
                signals,
                patterns,
                ContextBudget(
                    max_files=self.github.settings.context_max_files,
                    per_file_bytes=self.github.settings.context_file_bytes,
                    total_bytes=self.github.settings.context_total_bytes,
                ),
            )
            job.context_manifest = {
                **job.context_manifest,
                "risk_signals": list(signals),
                "related_paths": [item.path for item in pack.files],
                "config_files": sum(item.role == "CONFIG" for item in pack.files),
                "test_files": sum(item.role == "TEST" for item in pack.files),
                "caller_callee_files": sum(item.role == "CALLER_CALLEE" for item in pack.files),
                "truncated": pack.truncated,
                "truncation_reasons": list(pack.reasons),
                "size_excluded_files": pack.excluded_size,
                "prompt_diff_bytes": len(prompt_diff.encode("utf-8")),
            }
            detection = detect_domains(list(changed))
            domains = effective_domains(
                effective.review_domain_mode, effective.manual_review_domains, detection
            )
            job.detected_review_domains = ",".join(detection.domains)
            job.effective_review_domains = ",".join(domains)
            job.detection_reasons = "\n".join(detection.reasons)[:2000]
            job.prompt_version = validate_prompt_version(PROMPT_VERSION)
            try:
                await self.session.flush()
            except Exception as exc:
                raise JobSnapshotPersistenceError("job snapshot persistence failed") from exc
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
                    "risk_signals": signals,
                },
                prompt_diff,
                related_context=(
                    "Prior reviewed head: "
                    + (job.previous_reviewed_head or "UNKNOWN")
                    + "\nPrioritize new changes below; full PR diff governs grounding. "
                    "A missing prior finding means not detected, not proven fixed.\n"
                    + "<untrusted-incremental-diff>\n"
                    + incremental
                    + "\n</untrusted-incremental-diff>\n"
                    + pack.render()
                ),
            )
            await self.session.commit()
            if execution_id is None:
                raise ValueError("execution identity must be bound before orchestration")
            output = await execute_passes(
                self.session,
                job,
                self.github.settings,
                effective,
                self.runner,
                checkout,
                prompt,
                signals,
                execution_id,
            )
            self._candidates = list(output.findings)
            self._pass_origins = output.origins
        existing = set(
            (
                await self.session.scalars(
                    select(FindingRecord.fingerprint)
                    .join(ReviewRun, FindingRecord.review_run_id == ReviewRun.id)
                    .join(ReviewJob, ReviewRun.job_id == ReviewJob.id)
                    .where(
                        ReviewJob.installation_id == job.installation_id,
                        ReviewJob.repository_owner == job.repository_owner,
                        ReviewJob.repository_name == job.repository_name,
                        ReviewJob.pull_request_number == job.pull_request_number,
                        (
                            ReviewRun.github_review_id.is_not(None)
                            if job.trigger_action == "synchronize"
                            else ReviewJob.head_sha == job.head_sha
                        ),
                    )
                )
            ).all()
        )
        if job.trigger_action == "synchronize":
            prior_indexes = (
                await self.session.scalars(
                    select(ReviewRun.published_finding_fingerprints)
                    .join(ReviewJob)
                    .where(
                        ReviewJob.installation_id == job.installation_id,
                        ReviewJob.repository_owner == job.repository_owner,
                        ReviewJob.repository_name == job.repository_name,
                        ReviewJob.pull_request_number == job.pull_request_number,
                        ReviewRun.github_review_id.is_not(None),
                    )
                )
            ).all()
            for index in prior_indexes:
                if index is None:
                    continue
                stored = json.loads(index)
                if not isinstance(stored, list) or any(
                    not isinstance(value, str) or len(value) != 64 for value in stored
                ):
                    raise ValueError("invalid published fingerprint index")
                existing.update(stored)
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
            effective.max_inline_comments,
            effective.minimum_review_type,
            effective.allow_suggestions,
            effective.allow_questions,
            effective.allow_positive_fallback,
        )
        if not validation.findings:
            validation = replace(
                validation,
                no_reviewable_reason=(
                    no_reviewable_reason(manager.diff_text, changed)
                    or validation.no_reviewable_reason
                ),
            )
        findings = validation.findings
        contributed = {id(finding) for finding in findings}
        pass_records = (
            await self.session.scalars(select(ReviewPass).where(ReviewPass.job_id == job.id))
        ).all()
        for record in pass_records:
            if record.raw_count is None:
                continue
            contribution = sum(
                origin == record.pass_type and id(candidate) in contributed
                for origin, candidate in zip(output.origins, output.findings, strict=True)
            )
            record.contribution_count = contribution
            record.accepted_count = contribution
            record.rejected_count = record.raw_count - contribution
        scope_rejections = sum(
            item.rejection_stage in {"scope", "relation", "grounding"}
            for item in validation.rejection_diagnostics
        )
        counts = StageCounts(
            raw=len(output.findings),
            schema_valid=len(output.findings),
            scope_valid=len(output.findings) - scope_rejections,
            evidence_valid=validation.evidence_count,
            deduplicated=validation.deduplicated_count,
            ranked=len(findings),
            publishable=len(findings),
            published=0,
            rejected=sum(validation.rejection_counts.values()),
        )
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
        observed_fingerprints = published_fingerprints | {
            fingerprint(output.findings[item.finding_index - 1])
            for item in validation.rejection_diagnostics
            if item.rejection_reason == "DUPLICATE" and job.trigger_action == "synchronize"
        }
        comparison = {
            "new": len(observed_fingerprints - prior_fingerprints),
            "still": len(observed_fingerprints & prior_fingerprints),
            "not_detected": len(prior_fingerprints - observed_fingerprints),
        }
        run = ReviewRun(
            partial_review=output.partial,
            duplicate_only=bool(output.findings)
            and not findings
            and validation.rejection_counts == {"DUPLICATE": len(output.findings)},
            stage_counts=counts.model_dump(),
            publisher_fallback=False,
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
            published_findings_count=0,
            rejection_counts=json.dumps(validation.rejection_counts, sort_keys=True),
            published_finding_fingerprints=json.dumps(sorted(published_fingerprints)),
            comparison_new_count=comparison["new"],
            comparison_still_count=comparison["still"],
            comparison_not_detected_count=comparison["not_detected"],
            github_review_id=None,
            review_type_counts={
                key: sum(item.review_type.value == key for item in findings)
                for key in ("MUST_FIX", "SHOULD_FIX", "SUGGESTION", "QUESTION", "POSITIVE")
            },
            severity_counts={
                key: sum(item.severity.value == key for item in findings)
                for key in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
            },
            no_reviewable_reason=validation.no_reviewable_reason,
            comment_budget_accepted_count=validation.inline_count,
            suggested_patch_fallback=False,
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
        await self._persist_rejection_diagnostics(run, validation.rejection_diagnostics)
        # Persist the safe result and diagnostics before any GitHub write.  A
        # diagnostic insert/flush failure aborts here, so an unvalidated
        # finding can never be published merely because observability failed.
        await self.session.flush()
        await self.session.commit()
        if job.trigger_action == "command":
            await self._assert_current_command_head(job)
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
        if previous_auto_summary is None and not run.duplicate_only:
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
                    no_reviewable_reason=validation.no_reviewable_reason,
                    allow_suggested_changes=effective.allow_suggested_changes,
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
                    run.publisher_fallback = True
                    await self.session.commit()
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
                        no_reviewable_reason=validation.no_reviewable_reason,
                        allow_suggested_changes=False,
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
        counts.published = len(findings) if run.github_review_id is not None else 0
        run.published_findings_count = counts.published
        run.stage_counts = StageCounts.model_validate(counts.model_dump()).model_dump()
        comment_ids: dict[tuple[str, int], int] = {}
        if run.github_review_id is not None:
            try:
                comments = await self.github.list_review_comments(
                    job.installation_id,
                    job.repository_owner,
                    job.repository_name,
                    job.pull_request_number,
                )
                comment_ids = {
                    (str(item["path"]), int(item["line"])): int(item["id"])
                    for item in comments
                    if item.get("pull_request_review_id") in {None, run.github_review_id}
                    and isinstance(item.get("path"), str)
                    and isinstance(item.get("line"), int)
                    and isinstance(item.get("id"), int)
                }
            except Exception:
                logger.warning("Unable to read published inline comment identifiers job=%s", job.id)
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
                    github_comment_id=comment_ids.get((finding.path, finding.line)),
                    review_type=finding.review_type.value,
                    blocking=finding.blocking,
                    suggested_patch=finding.suggested_patch,
                    style_guide_reference=finding.style_guide_reference,
                )
            )
        job.terminal_outcome = (
            "PARTIAL_REVIEW"
            if output.partial
            else "SUCCEEDED_NO_FINDINGS"
            if not findings
            else "SUCCEEDED"
        )
        await self.session.commit()
        return len(findings)
