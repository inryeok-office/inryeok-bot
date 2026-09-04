import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session
from app.github.client import GitHubClient
from app.github.schemas import IssueCommentEvent, PullRequestEvent, is_review_command
from app.github.verifier import verify_signature
from app.jobs.models import GlobalReviewSettings, RepositorySettings, TriggerType, WebhookDelivery
from app.jobs.repository import JobRepository
from app.review.settings import EffectiveReviewSettings, resolve

router = APIRouter()
logger = logging.getLogger(__name__)
SUPPORTED_PR_ACTIONS = {"opened", "reopened", "ready_for_review", "synchronize"}
ACCOUNT_SCOPED_EVENTS = {
    "pull_request",
    "issue_comment",
    "installation",
    "installation_repositories",
}


async def get_github(settings: Settings = Depends(get_settings)) -> GitHubClient:
    return GitHubClient(settings)


async def _repository_settings(
    session: AsyncSession, installation_id: int, owner: str, name: str, settings: Settings
) -> RepositorySettings:
    value = await session.scalar(
        select(RepositorySettings).where(
            RepositorySettings.installation_id == installation_id,
            RepositorySettings.repository_owner == owner,
            RepositorySettings.repository_name == name,
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
    else:
        value.installed = True
    return value


async def _disable_installation(session: AsyncSession, installation_id: int) -> None:
    values = (
        await session.scalars(
            select(RepositorySettings).where(RepositorySettings.installation_id == installation_id)
        )
    ).all()
    for value in values:
        value.enabled = False
        value.installed = False


async def _effective_settings(
    session: AsyncSession, repository: RepositorySettings, settings: Settings
) -> EffectiveReviewSettings:
    global_settings = await session.get(GlobalReviewSettings, 1)
    if global_settings is None:
        global_settings = GlobalReviewSettings(id=1)
        session.add(global_settings)
        await session.flush()
    return resolve(global_settings, repository, settings)


def _trigger_enabled(action: str, effective: EffectiveReviewSettings) -> bool:
    return bool(getattr(effective, f"review_on_{action}"))


async def _record_delivery(session: AsyncSession, delivery_id: str, event_name: str) -> bool:
    session.add(WebhookDelivery(delivery_id=delivery_id, event_name=event_name))
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


def _payload_accounts(payload: dict[str, object], event_name: str) -> set[str]:
    accounts: set[str] = set()
    installation = payload.get("installation")
    if isinstance(installation, dict):
        account = installation.get("account")
        if isinstance(account, dict) and isinstance(account.get("login"), str):
            accounts.add(account["login"])
    repository = payload.get("repository")
    if isinstance(repository, dict):
        owner = repository.get("owner")
        if isinstance(owner, dict) and isinstance(owner.get("login"), str):
            accounts.add(owner["login"])
    if event_name in {"installation", "installation_repositories"} and not accounts:
        return set()
    return accounts


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
    body = await request.body()
    if not verify_signature(
        body, x_hub_signature_256, settings.github_webhook_secret.get_secret_value()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")
    if not x_github_event or not x_github_delivery:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing GitHub webhook headers")
    if not await _record_delivery(session, x_github_delivery, x_github_event):
        return {"accepted": True, "ignored": "duplicate_delivery"}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid webhook payload") from exc
    if x_github_event in ACCOUNT_SCOPED_EVENTS:
        accounts = _payload_accounts(payload, x_github_event)
        if not accounts or not all(settings.github_account_allowed(value) for value in accounts):
            return {"accepted": True, "ignored": "account_not_allowed"}
    if x_github_event in {"installation", "installation_repositories"}:
        try:
            installation_id = int(payload["installation"]["id"])
            action = str(payload.get("action", ""))
            if x_github_event == "installation":
                if action in {"deleted", "suspend"}:
                    await _disable_installation(session, installation_id)
                    repositories: list[dict[str, object]] = []
                elif action in {"created", "unsuspend"}:
                    repositories = await github.list_installation_repositories(installation_id)
                else:
                    return {"accepted": True, "ignored": "unsupported_action"}
            else:
                if action not in {"added", "removed"}:
                    return {"accepted": True, "ignored": "unsupported_action"}
                repositories = list(payload.get("repositories_added", []))
                for removed in list(payload.get("repositories_removed", [])):
                    owner, name = str(removed["full_name"]).split("/", 1)
                    if not settings.github_account_allowed(owner):
                        continue
                    removed_setting = await session.scalar(
                        select(RepositorySettings).where(
                            RepositorySettings.installation_id == installation_id,
                            RepositorySettings.repository_owner == owner,
                            RepositorySettings.repository_name == name,
                        )
                    )
                    if removed_setting:
                        removed_setting.enabled = False
                        removed_setting.installed = False
            for repository in repositories:
                owner, name = str(repository["full_name"]).split("/", 1)
                if not settings.github_account_allowed(owner):
                    continue
                repository_setting = await _repository_settings(
                    session, installation_id, owner, name, settings
                )
                repository_setting.enabled = True
                repository_setting.installed = True
            await session.commit()
            return {"accepted": True, "synced": True}
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "invalid installation payload"
            ) from exc
    if x_github_event not in {"pull_request", "issue_comment"}:
        return {"accepted": True, "ignored": "unsupported_event"}
    try:
        if x_github_event == "pull_request":
            pr_event = PullRequestEvent.model_validate(payload)
            if pr_event.action not in SUPPORTED_PR_ACTIONS:
                return {"accepted": True, "ignored": "unsupported_action"}
            if pr_event.sender.login.lower() == settings.github_bot_login.lower():
                return {"accepted": True, "ignored": "bot_event"}
            repo_settings = await _repository_settings(
                session,
                pr_event.installation.id,
                pr_event.repository.owner.login,
                pr_event.repository.name,
                settings,
            )
            effective = await _effective_settings(session, repo_settings, settings)
            if not effective.enabled or not effective.auto_review_enabled:
                await session.commit()
                return {"accepted": True, "ignored": "repository_disabled"}
            if not _trigger_enabled(pr_event.action, effective):
                await session.commit()
                return {"accepted": True, "ignored": "trigger_disabled"}
            if pr_event.pull_request.draft and repo_settings.ignore_draft:
                await session.commit()
                return {"accepted": True, "ignored": "draft"}
            installation_id = pr_event.installation.id
            owner = pr_event.repository.owner.login
            repository_name = pr_event.repository.name
            pr_number = pr_event.pull_request.number
            base_sha = pr_event.pull_request.base.sha
            head_sha = pr_event.pull_request.head.sha
            trigger = TriggerType.AUTO
            source_comment_id = None
        else:
            comment_event = IssueCommentEvent.model_validate(payload)
            if comment_event.action != "created" or comment_event.issue.pull_request is None:
                return {"accepted": True, "ignored": "not_pr_comment"}
            if (
                comment_event.sender.login.lower() == settings.github_bot_login.lower()
                or comment_event.comment.user.login.lower() == settings.github_bot_login.lower()
            ):
                return {"accepted": True, "ignored": "bot_event"}
            if not is_review_command(comment_event.comment.body, settings.github_bot_login):
                return {"accepted": True, "ignored": "not_review_command"}
            permission = await github.get_collaborator_permission(
                comment_event.installation.id,
                comment_event.repository.owner.login,
                comment_event.repository.name,
                comment_event.comment.user.login,
            )
            if permission not in {"admin", "maintain", "write"}:
                return {"accepted": True, "ignored": "insufficient_permission"}
            repo_settings = await _repository_settings(
                session,
                comment_event.installation.id,
                comment_event.repository.owner.login,
                comment_event.repository.name,
                settings,
            )
            effective = await _effective_settings(session, repo_settings, settings)
            if not effective.enabled or not effective.command_review_enabled:
                await session.commit()
                return {"accepted": True, "ignored": "repository_disabled"}
            raw_pr = await github.get_pull_request(
                comment_event.installation.id,
                comment_event.repository.owner.login,
                comment_event.repository.name,
                comment_event.issue.number,
            )
            if raw_pr.get("draft") and repo_settings.ignore_draft:
                return {"accepted": True, "ignored": "draft"}
            installation_id = comment_event.installation.id
            owner = comment_event.repository.owner.login
            repository_name = comment_event.repository.name
            pr_number = comment_event.issue.number
            base_sha = str(raw_pr["base"]["sha"])
            head_sha = str(raw_pr["head"]["sha"])
            trigger = TriggerType.COMMAND
            source_comment_id = comment_event.comment.id
        job, created = await JobRepository(session).enqueue(
            delivery_id=x_github_delivery,
            installation_id=installation_id,
            repository_owner=owner,
            repository_name=repository_name,
            pull_request_number=pr_number,
            base_sha=base_sha,
            head_sha=head_sha,
            trigger_type=trigger,
            source_comment_id=source_comment_id,
        )
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
        return {"accepted": True, "created": created, "job_id": job.id if job else None}
    except (ValidationError, json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid webhook payload") from exc
