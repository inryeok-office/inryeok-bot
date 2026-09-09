import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.control_plane import resolve_repository_policy
from app.config import Settings, get_settings
from app.db.session import get_session
from app.github.client import GitHubClient
from app.github.schemas import IssueCommentEvent, PullRequestEvent, is_review_command
from app.github.verifier import verify_signature
from app.jobs.models import (
    GitHubInstallation,
    GlobalReviewSettings,
    InstallationStatus,
    RepositorySettings,
    ReviewJob,
    TriggerType,
    WebhookDelivery,
)
from app.jobs.repository import JobRepository, QueueCapacityError
from app.review.settings import EffectiveReviewSettings

router = APIRouter()
logger = logging.getLogger(__name__)
SUPPORTED_PR_ACTIONS = {"opened", "reopened", "ready_for_review", "synchronize"}
DELIVERY_PROCESSING = "PROCESSING"
DELIVERY_PROCESSED = "PROCESSED"
DELIVERY_IGNORED = "IGNORED"
DELIVERY_FAILED_RETRYABLE = "FAILED_RETRYABLE"
DELIVERY_FAILED_FINAL = "FAILED_FINAL"
DELIVERY_MANUAL_REDELIVERY_REQUIRED = "MANUAL_REDELIVERY_REQUIRED"


async def get_github(settings: Settings = Depends(get_settings)) -> GitHubClient:
    return GitHubClient(settings)


async def _repository_settings(
    session: AsyncSession,
    installation_id: int,
    owner: str,
    name: str,
    settings: Settings,
    *,
    mark_installed: bool = False,
    github_repository_id: int | None = None,
    account_login: str | None = None,
) -> RepositorySettings:
    # GitHub owner/repository names are case-insensitive.  Canonicalizing at
    # the trust-boundary prevents a webhook using ``AcMe/Repo`` from creating
    # a second policy row or an unclaimable Job next to ``acme/repo``.
    owner = owner.strip().casefold()
    name = name.strip().casefold()
    value = await session.scalar(
        select(RepositorySettings).where(
            RepositorySettings.installation_id == installation_id,
            func.lower(RepositorySettings.repository_owner) == owner,
            func.lower(RepositorySettings.repository_name) == name,
        )
    )
    if value is None:
        value = RepositorySettings(
            installation_id=installation_id,
            repository_owner=owner,
            repository_name=name,
            min_confidence=settings.default_min_confidence,
            max_findings=settings.default_max_findings,
            include_low_severity=settings.default_include_low_severity,
            ignore_draft=settings.default_ignore_draft,
            ignore_patterns=settings.default_ignore_patterns,
        )
        session.add(value)
        await session.flush()
    elif mark_installed:
        # Only installation synchronization can restore repository access.
        # A later PR/comment webhook must never resurrect a repository removed
        # from the App installation.
        value.installed = True
    value.repository_owner = owner
    value.repository_name = name
    if github_repository_id is not None:
        value.github_repository_id = github_repository_id
    installation = await session.scalar(
        select(GitHubInstallation).where(
            GitHubInstallation.github_installation_id == installation_id
        )
    )
    if installation is None:
        installation = GitHubInstallation(
            github_installation_id=installation_id,
            account_login=account_login.strip().casefold() if account_login else None,
            status=InstallationStatus.ACTIVE,
        )
        session.add(installation)
        await session.flush()
    elif account_login:
        installation.account_login = account_login.strip().casefold()
    installation.status = InstallationStatus.ACTIVE if mark_installed else installation.status
    installation.last_synced_at = (
        datetime.now(UTC) if mark_installed else installation.last_synced_at
    )
    value.installation_fk_id = installation.id
    return value


async def _disable_installation(
    session: AsyncSession, installation_id: int, status: InstallationStatus
) -> None:
    installation = await session.scalar(
        select(GitHubInstallation).where(
            GitHubInstallation.github_installation_id == installation_id
        )
    )
    if installation is None:
        installation = GitHubInstallation(github_installation_id=installation_id, status=status)
        session.add(installation)
        await session.flush()
    else:
        installation.status = status
        installation.last_synced_at = datetime.now(UTC)
    values = (
        await session.scalars(
            select(RepositorySettings).where(RepositorySettings.installation_id == installation_id)
        )
    ).all()
    for value in values:
        # Installation access is a separate execution gate.  Preserve the
        # administrator's persisted policy so reinstall/unsuspend can restore
        # it without turning an explicit OFF into an implicit setting change.
        value.installed = False


async def _effective_settings(
    session: AsyncSession, repository: RepositorySettings, settings: Settings
) -> EffectiveReviewSettings:
    global_settings = await session.get(GlobalReviewSettings, 1)
    if global_settings is None:
        global_settings = GlobalReviewSettings(id=1)
        session.add(global_settings)
        await session.flush()
    return resolve_repository_policy(global_settings, repository, settings).settings


def _trigger_enabled(action: str, effective: EffectiveReviewSettings) -> bool:
    return bool(getattr(effective, f"review_on_{action}"))


async def _record_delivery(
    session: AsyncSession, delivery_id: str, event_name: str
) -> tuple[WebhookDelivery | None, bool]:
    """Atomically reserve a delivery and mark it PROCESSING.

    The reservation is committed before handler work so concurrent GitHub
    redeliveries cannot both enqueue a Job. Unlike the old boolean marker,
    retryable failures remain visible and can be safely retried.
    """
    correlation_id = uuid4().hex
    delivery = WebhookDelivery(
        delivery_id=delivery_id,
        event_name=event_name,
        status=DELIVERY_PROCESSING,
        attempt_count=1,
        correlation_id=correlation_id,
        processing_started_at=datetime.now(UTC),
    )
    session.add(delivery)
    try:
        await session.commit()
        await session.refresh(delivery)
        return delivery, True
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
        )
        if existing is None or existing.status != DELIVERY_FAILED_RETRYABLE:
            return existing, False
        existing.status = DELIVERY_PROCESSING
        existing.attempt_count += 1
        existing.safe_reason = None
        existing.processing_started_at = datetime.now(UTC)
        existing.completed_at = None
        await session.commit()
        return existing, True


async def _finish_delivery(
    session: AsyncSession,
    delivery: WebhookDelivery | None,
    state: str,
    reason: str,
    response_status: int = 200,
) -> None:
    if delivery is None:
        return
    delivery.status = state
    delivery.safe_reason = reason[:100]
    delivery.response_status = response_status
    delivery.completed_at = datetime.now(UTC)
    await session.commit()


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(None),
    x_github_delivery: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    github: GitHubClient = Depends(get_github),
) -> dict[str, object]:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > settings.max_webhook_body_bytes:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "webhook body too large")
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid content length") from exc
    if request.headers.get("content-encoding", "identity").casefold() not in {"", "identity"}:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "compressed webhooks are not supported"
        )
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > settings.max_webhook_body_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "webhook body too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    if not verify_signature(
        body, x_hub_signature_256, settings.github_webhook_secret.get_secret_value()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")
    if not x_github_event or not x_github_delivery:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing GitHub webhook headers")
    delivery, should_process = await _record_delivery(session, x_github_delivery, x_github_event)
    if not should_process:
        return {"accepted": True, "ignored": "duplicate_delivery"}

    async def ignored(reason: str) -> dict[str, object]:
        await _finish_delivery(session, delivery, DELIVERY_IGNORED, reason)
        return {"accepted": True, "ignored": reason}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        await _finish_delivery(session, delivery, DELIVERY_FAILED_FINAL, "INVALID_PAYLOAD", 400)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid webhook payload") from exc
    if delivery is not None and isinstance(payload, dict):
        installation = payload.get("installation")
        if isinstance(installation, dict) and isinstance(installation.get("id"), int):
            delivery.installation_id = installation["id"]
        repository = payload.get("repository")
        if isinstance(repository, dict):
            owner = repository.get("owner")
            if isinstance(owner, dict) and isinstance(owner.get("login"), str):
                delivery.repository_owner = owner["login"].strip().casefold()
            if isinstance(repository.get("name"), str):
                delivery.repository_name = repository["name"].strip().casefold()
    if x_github_event in {"installation", "installation_repositories"}:
        try:
            installation_id = int(payload["installation"]["id"])
            action = str(payload.get("action", ""))
            if x_github_event == "installation":
                if action in {"deleted", "suspend"}:
                    await _disable_installation(
                        session,
                        installation_id,
                        InstallationStatus.REMOVED
                        if action == "deleted"
                        else InstallationStatus.SUSPENDED,
                    )
                    repositories: list[dict[str, object]] = []
                elif action in {"created", "unsuspend"}:
                    repositories = await github.list_installation_repositories(installation_id)
                else:
                    return await ignored("unsupported_action")
            else:
                if action not in {"added", "removed"}:
                    return await ignored("unsupported_action")
                repositories = list(payload.get("repositories_added", []))
                for removed in list(payload.get("repositories_removed", [])):
                    owner, name = str(removed["full_name"]).split("/", 1)
                    removed_setting = await session.scalar(
                        select(RepositorySettings).where(
                            RepositorySettings.installation_id == installation_id,
                            func.lower(RepositorySettings.repository_owner) == owner.casefold(),
                            func.lower(RepositorySettings.repository_name) == name.casefold(),
                        )
                    )
                    if removed_setting:
                        # Access state is not administrator policy.  Keep the
                        # stored/effective policy intact for reinstall while
                        # installed=false blocks execution.
                        removed_setting.installed = False
            for repository in repositories:
                owner, name = str(repository["full_name"]).split("/", 1)
                account = payload.get("installation", {}).get("account", {})
                repository_setting = await _repository_settings(
                    session,
                    installation_id,
                    owner,
                    name,
                    settings,
                    mark_installed=True,
                    github_repository_id=(
                        int(str(repository["id"]))
                        if isinstance(repository.get("id"), int)
                        else None
                    ),
                    account_login=(
                        str(account.get("login"))
                        if isinstance(account, dict) and account.get("login")
                        else None
                    ),
                )
                # Installation sync supplies defaults only.  Nullable
                # overrides are the admin's explicit intent and must win over
                # re-delivery, restart, or repository-list refresh.
                if repository_setting.override_enabled is None:
                    repository_setting.enabled = True
                if repository_setting.override_auto_review_enabled is None:
                    repository_setting.auto_review = True
                repository_setting.installed = True
            await session.commit()
            await _finish_delivery(session, delivery, DELIVERY_PROCESSED, "INSTALLATION_SYNCED")
            return {"accepted": True, "synced": True}
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            await _finish_delivery(
                session, delivery, DELIVERY_FAILED_FINAL, "INVALID_INSTALLATION", 400
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "invalid installation payload"
            ) from exc
    if x_github_event not in {"pull_request", "issue_comment"}:
        return await ignored("unsupported_event")
    try:
        if x_github_event == "pull_request":
            pr_event = PullRequestEvent.model_validate(payload)
            if pr_event.action not in SUPPORTED_PR_ACTIONS:
                return await ignored("unsupported_action")
            if pr_event.sender.login.lower() == settings.github_bot_login.lower():
                return await ignored("bot_event")
            repo_settings = await _repository_settings(
                session,
                pr_event.installation.id,
                pr_event.repository.owner.login,
                pr_event.repository.name,
                settings,
                github_repository_id=pr_event.repository.id,
                account_login=pr_event.repository.owner.login,
            )
            effective = await _effective_settings(session, repo_settings, settings)
            if not effective.enabled or not effective.auto_review_enabled:
                return await ignored("repository_disabled")
            if not _trigger_enabled(pr_event.action, effective):
                return await ignored("trigger_disabled")
            if pr_event.pull_request.draft and repo_settings.ignore_draft:
                return await ignored("draft")
            installation_id = pr_event.installation.id
            owner = repo_settings.repository_owner
            repository_name = repo_settings.repository_name
            pr_number = pr_event.pull_request.number
            base_sha = pr_event.pull_request.base.sha
            head_sha = pr_event.pull_request.head.sha
            trigger = TriggerType.AUTO
            source_comment_id = None
            not_before = (
                datetime.now(UTC) + timedelta(seconds=effective.synchronize_debounce_seconds)
                if pr_event.action == "synchronize"
                else None
            )
        else:
            comment_event = IssueCommentEvent.model_validate(payload)
            if comment_event.action != "created" or comment_event.issue.pull_request is None:
                return await ignored("not_pr_comment")
            if (
                comment_event.sender.login.lower() == settings.github_bot_login.lower()
                or comment_event.comment.user.login.lower() == settings.github_bot_login.lower()
            ):
                return await ignored("bot_event")
            if not is_review_command(comment_event.comment.body, settings.github_bot_login):
                return await ignored("not_review_command")
            permission = await github.get_collaborator_permission(
                comment_event.installation.id,
                comment_event.repository.owner.login,
                comment_event.repository.name,
                comment_event.comment.user.login,
            )
            if permission not in {"admin", "maintain", "write"}:
                return await ignored("insufficient_permission")
            repo_settings = await _repository_settings(
                session,
                comment_event.installation.id,
                comment_event.repository.owner.login,
                comment_event.repository.name,
                settings,
                github_repository_id=comment_event.repository.id,
                account_login=comment_event.repository.owner.login,
            )
            effective = await _effective_settings(session, repo_settings, settings)
            # Keep manual command eligibility distinct from the repository's
            # general enabled switch.  In particular, auto_review_enabled is
            # intentionally not consulted here: operators may disable
            # automatic PR events while still allowing an explicit /review.
            # A stable reason is persisted on the delivery so an ignored
            # command can be diagnosed without exposing the webhook payload.
            if not effective.enabled:
                return await ignored("repository_disabled")
            if not effective.command_review_enabled:
                return await ignored("manual_review_disabled")
            raw_pr = await github.get_pull_request(
                comment_event.installation.id,
                comment_event.repository.owner.login,
                comment_event.repository.name,
                comment_event.issue.number,
            )
            if raw_pr.get("draft") and repo_settings.ignore_draft:
                return await ignored("draft")
            installation_id = comment_event.installation.id
            owner = repo_settings.repository_owner
            repository_name = repo_settings.repository_name
            pr_number = comment_event.issue.number
            base_sha = str(raw_pr["base"]["sha"])
            head_sha = str(raw_pr["head"]["sha"])
            trigger = TriggerType.COMMAND
            source_comment_id = comment_event.comment.id
            not_before = None
            if effective.command_cooldown_seconds:
                recent = await session.scalar(
                    select(ReviewJob)
                    .where(
                        ReviewJob.repository_owner == owner,
                        ReviewJob.repository_name == repository_name,
                        ReviewJob.pull_request_number == pr_number,
                        ReviewJob.head_sha == head_sha,
                        ReviewJob.trigger_type == TriggerType.COMMAND,
                        ReviewJob.status.in_({"PENDING", "RUNNING", "SUCCEEDED"}),
                        ReviewJob.created_at
                        >= datetime.now(UTC)
                        - timedelta(seconds=effective.command_cooldown_seconds),
                    )
                    .order_by(ReviewJob.created_at.desc())
                    .limit(1)
                )
                if recent is not None:
                    return await ignored("command_cooldown")
        try:
            job, created = await JobRepository(session).enqueue(
                max_pending_jobs=settings.max_pending_jobs,
                max_repository_pending_jobs=settings.max_repository_pending_jobs,
                delivery_id=x_github_delivery,
                installation_id=installation_id,
                repository_owner=owner,
                repository_name=repository_name,
                pull_request_number=pr_number,
                base_sha=base_sha,
                head_sha=head_sha,
                trigger_type=trigger,
                source_comment_id=source_comment_id,
                not_before=not_before,
            )
        except QueueCapacityError:
            await session.rollback()
            await _finish_delivery(
                session, delivery, DELIVERY_FAILED_RETRYABLE, "QUEUE_CAPACITY", 200
            )
            return {"accepted": True, "created": False, "ignored": "queue_capacity"}
        if created and job is not None:
            try:
                if trigger == TriggerType.AUTO:
                    await github.add_pull_request_eyes_reaction(
                        installation_id, owner, repository_name, pr_number
                    )
                else:
                    assert source_comment_id is not None
                    await github.add_comment_eyes_reaction(
                        installation_id, owner, repository_name, source_comment_id
                    )
            except Exception:
                logger.warning("Unable to add the review-start reaction for job %s", job.id)
        await _finish_delivery(session, delivery, DELIVERY_PROCESSED, "JOB_ENQUEUED")
        return {"accepted": True, "created": created, "job_id": job.id if job else None}
    except (ValidationError, json.JSONDecodeError, KeyError) as exc:
        await _finish_delivery(session, delivery, DELIVERY_FAILED_FINAL, "INVALID_PAYLOAD", 400)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid webhook payload") from exc
    except Exception:
        logger.exception("Webhook delivery failed: %s", x_github_delivery)
        await session.rollback()
        await _finish_delivery(session, delivery, DELIVERY_FAILED_RETRYABLE, "HANDLER_ERROR", 500)
        raise


async def recover_stale_deliveries(
    session: AsyncSession,
    *,
    threshold_seconds: int = 900,
    max_attempts: int = 3,
    apply: bool = False,
) -> list[dict[str, object]]:
    """Safely classify stale PROCESSING deliveries without replaying payloads."""
    cutoff = datetime.now(UTC) - timedelta(seconds=threshold_seconds)
    rows = list(
        (
            await session.scalars(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status == DELIVERY_PROCESSING,
                    WebhookDelivery.processing_started_at < cutoff,
                )
                .order_by(WebhookDelivery.id)
            )
        ).all()
    )
    report: list[dict[str, object]] = []
    for delivery in rows:
        related = await session.scalar(
            select(ReviewJob.id).where(ReviewJob.delivery_id == delivery.delivery_id)
        )
        reason = (
            "JOB_ALREADY_ENQUEUED" if related is not None else DELIVERY_MANUAL_REDELIVERY_REQUIRED
        )
        report.append(
            {
                "delivery_id": delivery.delivery_id,
                "job_id": related,
                "reason": reason,
                "attempt_count": delivery.attempt_count,
            }
        )
        if not apply:
            continue
        if delivery.attempt_count >= max_attempts:
            delivery.status = DELIVERY_FAILED_FINAL
            delivery.safe_reason = "STALE_PROCESSING_MAX_ATTEMPTS"
        elif related is not None:
            delivery.status = DELIVERY_PROCESSED
            delivery.safe_reason = "JOB_ALREADY_ENQUEUED"
        else:
            delivery.status = DELIVERY_FAILED_FINAL
            delivery.safe_reason = DELIVERY_MANUAL_REDELIVERY_REQUIRED
        delivery.attempt_count += 1
        delivery.completed_at = datetime.now(UTC)
        delivery.response_status = 200
    if apply and rows:
        await session.commit()
    return report
