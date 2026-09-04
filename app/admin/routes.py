from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import AdminPrincipal, csrf_token, require_admin, verify_csrf
from app.config import Settings, get_settings
from app.db.session import get_session
from app.jobs.models import (
    AdminAuditLog,
    GlobalReviewSettings,
    RepositorySettings,
    ReviewDomain,
    ReviewJob,
)
from app.jobs.repository import JobRepository
from app.review.domains import PROMPT_VERSION, effective_domains
from app.review.settings import resolve, validate_choice, validate_paths

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _account_filter(column: Any, settings: Settings) -> Any:
    if settings.environment == "development" and settings.allow_unlisted_github_accounts:
        return True
    return func.lower(column).in_(settings.allowed_github_account_set)


def _ensure_allowed(repository_owner: str, settings: Settings) -> None:
    if not settings.github_account_allowed(repository_owner):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")


def _context(
    request: Request,
    principal: AdminPrincipal,
    settings: Settings,
    **values: Any,
) -> dict[str, Any]:
    return {
        "request": request,
        "principal": principal,
        "csrf_token": csrf_token(principal, settings),
        "app_name": settings.github_app_display_name,
        **values,
    }


def _optional_bool(value: str) -> bool | None:
    if value == "inherit":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("invalid override")


def _optional_int(value: str, minimum: int, maximum: int) -> int | None:
    if not value:
        return None
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError("numeric override outside safety limit")
    return parsed


def _optional_float(value: str, minimum: float, maximum: float) -> float | None:
    if not value:
        return None
    parsed = float(value)
    if not minimum <= parsed <= maximum:
        raise ValueError("numeric override outside safety limit")
    return parsed


async def _require_repository_admin(
    repository: RepositorySettings,
    principal: AdminPrincipal,
    settings: Settings,
) -> None:
    if principal.development:
        return
    if not repository.installed or not principal.access_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "GitHub App is not installed")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{settings.github_api_url}/repos/{repository.repository_owner}/{repository.repository_name}",
            headers={
                "Authorization": f"Bearer {principal.access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
    permissions = response.json().get("permissions", {}) if response.status_code == 200 else {}
    if not permissions.get("admin", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "repository admin permission required")


@router.get("", response_class=HTMLResponse)
async def index(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    jobs = (
        await session.scalars(
            select(ReviewJob)
            .where(_account_filter(ReviewJob.repository_owner, settings))
            .order_by(ReviewJob.created_at.desc())
            .limit(20)
        )
    ).all()
    repositories = (
        await session.scalars(
            select(RepositorySettings)
            .order_by(RepositorySettings.repository_owner, RepositorySettings.repository_name)
            .where(_account_filter(RepositorySettings.repository_owner, settings))
        )
    ).all()
    global_settings = await session.get(GlobalReviewSettings, 1)
    return templates.TemplateResponse(
        request,
        "index.html",
        _context(
            request,
            principal,
            settings,
            jobs=jobs,
            repositories=repositories,
            global_settings=global_settings,
        ),
    )


@router.get("/jobs", response_class=HTMLResponse)
async def jobs(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    values = (
        await session.scalars(
            select(ReviewJob)
            .where(_account_filter(ReviewJob.repository_owner, settings))
            .order_by(ReviewJob.created_at.desc())
            .limit(100)
        )
    ).all()
    global_settings = await session.get(GlobalReviewSettings, 1)
    if global_settings is None:
        global_settings = GlobalReviewSettings(id=1)
        session.add(global_settings)
        await session.commit()
    return templates.TemplateResponse(
        request, "jobs.html", _context(request, principal, settings, jobs=values)
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    job_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    job = await session.get(ReviewJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    _ensure_allowed(job.repository_owner, settings)
    return templates.TemplateResponse(
        request, "job_detail.html", _context(request, principal, settings, job=job)
    )


@router.get("/repositories", response_class=HTMLResponse)
async def repositories(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    values = (
        await session.scalars(
            select(RepositorySettings)
            .order_by(RepositorySettings.repository_owner, RepositorySettings.repository_name)
            .where(_account_filter(RepositorySettings.repository_owner, settings))
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "repositories.html",
        _context(request, principal, settings, repositories=values),
    )


@router.get("/repositories/{repository_id}", response_class=HTMLResponse)
async def repository_detail(
    repository_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    repository = await session.get(RepositorySettings, repository_id)
    if not repository:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")
    _ensure_allowed(repository.repository_owner, settings)
    global_settings = await session.get(GlobalReviewSettings, 1)
    if global_settings is None:
        global_settings = GlobalReviewSettings(id=1)
        session.add(global_settings)
        await session.commit()
    return templates.TemplateResponse(
        request,
        "repository_detail.html",
        _context(
            request,
            principal,
            settings,
            repository=repository,
            effective=resolve(global_settings, repository, settings),
            models=settings.allowed_codex_models,
            domains=[item.value for item in ReviewDomain],
            prompt_version=PROMPT_VERSION,
        ),
    )


@router.get("/settings", response_class=HTMLResponse)
async def global_settings_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    value = await session.get(GlobalReviewSettings, 1)
    if value is None:
        value = GlobalReviewSettings(id=1)
        session.add(value)
        await session.commit()
    return templates.TemplateResponse(
        request,
        "global_settings.html",
        _context(
            request,
            principal,
            settings,
            global_settings=value,
            models=settings.allowed_codex_models,
            domains=[item.value for item in ReviewDomain],
            prompt_version=PROMPT_VERSION,
        ),
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    entries = (
        await session.scalars(
            select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(100)
        )
    ).all()
    return templates.TemplateResponse(
        request, "audit.html", _context(request, principal, settings, entries=entries)
    )


@router.post("/settings")
async def update_global_settings(
    csrf: str = Form(..., alias="_csrf"),
    language: str = Form("ko"),
    review_profile: str = Form("BALANCED"),
    model: str = Form(""),
    max_findings: int = Form(10),
    minimum_confidence: float = Form(0.9),
    codex_timeout_seconds: int = Form(900),
    enabled: bool = Form(False),
    auto_review_enabled: bool = Form(False),
    command_review_enabled: bool = Form(False),
    include_low_severity: bool = Form(False),
    minimum_severity: str = Form("MEDIUM"),
    enabled_categories: str = Form(""),
    ignored_paths: str = Form(""),
    review_on_opened: bool = Form(False),
    review_on_reopened: bool = Form(False),
    review_on_ready_for_review: bool = Form(False),
    review_on_synchronize: bool = Form(False),
    review_domain_mode: str = Form("AUTO"),
    manual_review_domains: list[str] = Form([]),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> RedirectResponse:
    verify_csrf(csrf, principal, settings)
    try:
        validate_choice(language, review_profile, model or None, settings)
        if minimum_severity.upper() not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            raise ValueError("unsupported minimum severity")
        validate_paths(ignored_paths)
        if review_domain_mode == "MANUAL":
            effective_domains(review_domain_mode, ",".join(manual_review_domains), None)
        elif review_domain_mode != "AUTO":
            raise ValueError("unsupported review domain mode")
        if (
            not 0.8 <= minimum_confidence <= 1
            or not 1 <= max_findings <= 50
            or not 30 <= codex_timeout_seconds <= 3600
        ):
            raise ValueError("setting outside safety limit")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    value = await session.get(GlobalReviewSettings, 1) or GlobalReviewSettings(id=1)
    session.add(value)
    value.enabled, value.auto_review_enabled, value.command_review_enabled = (
        enabled,
        auto_review_enabled,
        command_review_enabled,
    )
    value.language, value.review_profile, value.model = language, review_profile, model or None
    value.max_findings, value.minimum_confidence, value.codex_timeout_seconds = (
        max_findings,
        minimum_confidence,
        codex_timeout_seconds,
    )
    value.include_low_severity, value.ignored_paths, value.updated_by = (
        include_low_severity,
        ignored_paths,
        principal.github_login,
    )
    value.minimum_severity = minimum_severity.upper()
    value.enabled_categories = enabled_categories
    value.review_domain_mode = review_domain_mode
    value.manual_review_domains = ",".join(manual_review_domains)
    (
        value.review_on_opened,
        value.review_on_reopened,
        value.review_on_ready_for_review,
        value.review_on_synchronize,
    ) = (
        review_on_opened,
        review_on_reopened,
        review_on_ready_for_review,
        review_on_synchronize,
    )
    session.add(
        AdminAuditLog(
            actor_login=principal.github_login,
            action="update",
            target_type="global_settings",
            target_id="1",
            summary="Updated review defaults",
        )
    )
    await session.commit()
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/repositories/{repository_id}/settings")
async def update_repository(
    repository_id: int,
    request: Request,
    csrf: str = Form(..., alias="_csrf"),
    enabled: bool = Form(False),
    auto_review: bool = Form(False),
    min_confidence: float = Form(...),
    max_findings: int = Form(...),
    include_low_severity: bool = Form(False),
    ignore_draft: bool = Form(False),
    ignore_patterns: str = Form(""),
    override_enabled: str = Form("inherit"),
    override_auto_review_enabled: str = Form("inherit"),
    override_command_review_enabled: str = Form("inherit"),
    override_language: str = Form(""),
    override_review_profile: str = Form(""),
    override_model: str = Form(""),
    override_max_findings: str = Form(""),
    override_minimum_confidence: str = Form(""),
    override_include_low_severity: str = Form("inherit"),
    override_ignored_paths: str = Form(""),
    override_timeout_seconds: str = Form(""),
    override_minimum_severity: str = Form(""),
    override_enabled_categories: str = Form(""),
    override_review_on_opened: str = Form("inherit"),
    override_review_on_reopened: str = Form("inherit"),
    override_review_on_ready_for_review: str = Form("inherit"),
    override_review_on_synchronize: str = Form("inherit"),
    override_review_domain_mode: str = Form("inherit"),
    override_manual_review_domains: list[str] = Form([]),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> RedirectResponse:
    verify_csrf(csrf, principal, settings)
    repository = await session.get(RepositorySettings, repository_id)
    if not repository:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")
    _ensure_allowed(repository.repository_owner, settings)
    await _require_repository_admin(repository, principal, settings)
    repository.enabled = enabled
    repository.auto_review = auto_review
    repository.min_confidence = max(0, min(1, min_confidence))
    repository.max_findings = max(1, min(50, max_findings))
    repository.include_low_severity = include_low_severity
    repository.ignore_draft = ignore_draft
    repository.ignore_patterns = ignore_patterns
    try:
        validate_choice(
            override_language or "ko",
            override_review_profile or "BALANCED",
            override_model or None,
            settings,
        )
        repository.override_enabled = _optional_bool(override_enabled)
        repository.override_auto_review_enabled = _optional_bool(override_auto_review_enabled)
        repository.override_command_review_enabled = _optional_bool(override_command_review_enabled)
        repository.override_language = override_language or None
        repository.override_review_profile = override_review_profile or None
        repository.override_model = override_model or None
        repository.override_max_findings = _optional_int(override_max_findings, 1, 50)
        repository.override_minimum_confidence = _optional_float(
            override_minimum_confidence, 0.8, 1
        )
        repository.override_include_low_severity = _optional_bool(override_include_low_severity)
        repository.override_ignored_paths = override_ignored_paths or None
        if repository.override_ignored_paths is not None:
            validate_paths(repository.override_ignored_paths)
        repository.override_timeout_seconds = _optional_int(override_timeout_seconds, 30, 3600)
        if override_minimum_severity and override_minimum_severity not in {
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
        }:
            raise ValueError("unsupported minimum severity")
        repository.override_minimum_severity = override_minimum_severity or None
        repository.override_enabled_categories = override_enabled_categories or None
        repository.override_review_on_opened = _optional_bool(override_review_on_opened)
        repository.override_review_on_reopened = _optional_bool(override_review_on_reopened)
        repository.override_review_on_ready_for_review = _optional_bool(
            override_review_on_ready_for_review
        )
        repository.override_review_on_synchronize = _optional_bool(override_review_on_synchronize)
        if override_review_domain_mode not in {"inherit", "AUTO", "MANUAL"}:
            raise ValueError("unsupported review domain mode")
        repository.override_review_domain_mode = (
            None if override_review_domain_mode == "inherit" else override_review_domain_mode
        )
        repository.override_manual_review_domains = ",".join(override_manual_review_domains) or None
        if repository.override_review_domain_mode == "MANUAL":
            effective_domains("MANUAL", repository.override_manual_review_domains, None)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    session.add(
        AdminAuditLog(
            actor_login=principal.github_login,
            action="update",
            target_type="repository_settings",
            target_id=str(repository.id),
            summary="Updated legacy repository review controls",
        )
    )
    await session.commit()
    return RedirectResponse(f"/admin/repositories/{repository.id}", status_code=303)


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: int,
    csrf: str = Form(..., alias="_csrf"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> RedirectResponse:
    verify_csrf(csrf, principal, settings)
    job = await session.get(ReviewJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    _ensure_allowed(job.repository_owner, settings)
    repository = await session.scalar(
        select(RepositorySettings).where(
            RepositorySettings.installation_id == job.installation_id,
            RepositorySettings.repository_owner == job.repository_owner,
            RepositorySettings.repository_name == job.repository_name,
        )
    )
    if not repository:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "GitHub App is not installed")
    await _require_repository_admin(repository, principal, settings)
    retry = await JobRepository(session).retry(job_id)
    if retry is not None:
        session.add(
            AdminAuditLog(
                actor_login=principal.github_login,
                action="retry",
                target_type="review_job",
                target_id=str(job_id),
                summary=f"Created retry job {retry.id}",
            )
        )
        await session.commit()
        return RedirectResponse(f"/admin/jobs/{retry.id}", status_code=303)
    return RedirectResponse(f"/admin/jobs/{job_id}", status_code=303)
