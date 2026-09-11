import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import AdminPrincipal, csrf_token, require_admin, verify_csrf
from app.admin.control_plane import (
    resolve_repository_policy,
    set_processing_state,
    update_global_policy,
    update_repository_policy,
)
from app.admin.metrics import usage_metrics
from app.admin.presentation import format_admin_datetime
from app.config import Settings, get_settings
from app.db.session import get_session
from app.jobs.models import (
    AdminAuditLog,
    GlobalReviewSettings,
    JobStatus,
    OpsIncident,
    RepositorySettings,
    ReviewDomain,
    ReviewJob,
    ReviewRun,
)
from app.jobs.repository import JobRepository
from app.review.domains import PROMPT_VERSION, effective_domains
from app.review.model_catalog import load_db_catalog, spec_for
from app.review.settings import validate_choice, validate_paths

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _account_filter(column: Any, settings: Settings) -> Any:
    # The signed GitHub App installation is the tenant boundary.  Global
    # administrators may inspect every installation; organization login names
    # are metadata, not an authorization allowlist.
    del column, settings
    return True


def _ensure_allowed(repository_owner: str, settings: Settings) -> None:
    del repository_owner, settings


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
        "format_datetime": format_admin_datetime,
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
    job_counts = {
        status.value: await session.scalar(
            select(func.count(ReviewJob.id)).where(
                ReviewJob.status == status,
                _account_filter(ReviewJob.repository_owner, settings),
            )
        )
        for status in JobStatus
    }
    metrics = await usage_metrics(session, 1)
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
            job_counts=job_counts,
            usage_metrics=metrics,
        ),
    )


@router.get("/usage", response_class=HTMLResponse)
async def usage_page(
    request: Request,
    period: int = 1,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    """Render persisted review usage without exposing sensitive execution data."""
    metrics = await usage_metrics(session, period)
    return templates.TemplateResponse(
        request,
        "usage.html",
        _context(
            request,
            principal,
            settings,
            usage_metrics=metrics,
            selected_period=metrics.period_days,
        ),
    )


@router.get("/jobs", response_class=HTMLResponse)
async def jobs(
    request: Request,
    repository: str | None = None,
    status_filter: str | None = None,
    error_code: str | None = None,
    error_category: str | None = None,
    error_stage: str | None = None,
    retryable: bool | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    query = select(ReviewJob).where(_account_filter(ReviewJob.repository_owner, settings))
    if repository and "/" in repository:
        owner, name = repository.split("/", 1)
        query = query.where(
            func.lower(ReviewJob.repository_owner) == owner.casefold(),
            func.lower(ReviewJob.repository_name) == name.casefold(),
        )
    if status_filter and status_filter in {item.value for item in JobStatus}:
        query = query.where(ReviewJob.status == JobStatus(status_filter))
    if error_code:
        query = query.where(ReviewJob.error_code == error_code[:100])
    if error_category:
        query = query.where(ReviewJob.error_category == error_category[:32])
    if error_stage:
        query = query.where(ReviewJob.error_stage == error_stage[:32])
    if retryable is True:
        query = query.where(ReviewJob.retry_policy.is_not(None), ReviewJob.retry_policy != "NEVER")
    elif retryable is False:
        query = query.where(ReviewJob.retry_policy == "NEVER")
    values = (await session.scalars(query.order_by(ReviewJob.created_at.desc()).limit(100))).all()
    global_settings = await session.get(GlobalReviewSettings, 1)
    if global_settings is None:
        global_settings = GlobalReviewSettings(id=1)
        session.add(global_settings)
        await session.commit()
    return templates.TemplateResponse(
        request,
        "jobs.html",
        _context(
            request,
            principal,
            settings,
            jobs=values,
            repository_filter=repository,
            status_filter=status_filter,
            error_code_filter=error_code,
            error_category_filter=error_category,
            error_stage_filter=error_stage,
            retryable_filter=retryable,
        ),
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
    review_run = await session.scalar(
        select(ReviewRun).where(ReviewRun.job_id == job.id).order_by(ReviewRun.id.desc())
    )
    rejection_counts: dict[str, int] = {}
    if review_run and review_run.rejection_counts:
        try:
            parsed = json.loads(review_run.rejection_counts)
            if isinstance(parsed, dict):
                rejection_counts = {
                    str(key): int(value)
                    for key, value in parsed.items()
                    if isinstance(value, int) and value >= 0
                }
        except (TypeError, ValueError):
            rejection_counts = {}
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        _context(
            request,
            principal,
            settings,
            job=job,
            review_run=review_run,
            rejection_counts=rejection_counts,
        ),
    )


@router.get("/repositories", response_class=HTMLResponse)
async def repositories(
    request: Request,
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    query = select(RepositorySettings).where(
        _account_filter(RepositorySettings.repository_owner, settings)
    )
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            RepositorySettings.repository_owner.ilike(pattern)
            | RepositorySettings.repository_name.ilike(pattern)
        )
    values = (
        await session.scalars(
            query.order_by(RepositorySettings.repository_owner, RepositorySettings.repository_name)
        )
    ).all()
    global_settings = await session.get(GlobalReviewSettings, 1)
    if global_settings is None:
        global_settings = GlobalReviewSettings(id=1)
    catalog = await load_db_catalog(session)
    effective_by_repository = {
        repository.id: resolve_repository_policy(global_settings, repository, settings, catalog)
        for repository in values
    }
    latest_runs: dict[tuple[str, str], ReviewRun] = {}
    recent = (
        await session.execute(
            select(ReviewRun, ReviewJob)
            .join(ReviewJob, ReviewRun.job_id == ReviewJob.id)
            .order_by(ReviewRun.created_at.desc())
            .limit(200)
        )
    ).all()
    for run, job in recent:
        key = (job.repository_owner.casefold(), job.repository_name.casefold())
        latest_runs.setdefault(key, run)
    return templates.TemplateResponse(
        request,
        "repositories.html",
        _context(
            request,
            principal,
            settings,
            repositories=values,
            repository_query=q or "",
            effective_by_repository=effective_by_repository,
            latest_runs=latest_runs,
        ),
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
    catalog = await load_db_catalog(session)
    effective_model_spec = spec_for(
        settings, repository.override_model or global_settings.model, catalog
    )
    return templates.TemplateResponse(
        request,
        "repository_detail.html",
        _context(
            request,
            principal,
            settings,
            repository=repository,
            effective=resolve_repository_policy(global_settings, repository, settings, catalog),
            models=tuple(item.model_id for item in catalog if item.selectable),
            model_catalog=tuple(item for item in catalog if item.selectable),
            effective_model_spec=effective_model_spec,
            reasoning_efforts=(
                effective_model_spec.supported_efforts
                if effective_model_spec is not None
                else ("low", "medium", "high")
            ),
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
    catalog = await load_db_catalog(session)
    model_spec = spec_for(settings, value.model, catalog)
    return templates.TemplateResponse(
        request,
        "global_settings.html",
        _context(
            request,
            principal,
            settings,
            global_settings=value,
            models=tuple(item.model_id for item in catalog if item.selectable),
            model_catalog=tuple(item for item in catalog if item.selectable),
            model_spec=model_spec,
            reasoning_efforts=(
                model_spec.supported_efforts
                if model_spec is not None
                else ("low", "medium", "high")
            ),
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


@router.get("/models", response_class=HTMLResponse)
async def model_catalog_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    """Read-only catalog view; verification is deliberately CLI-operated."""

    catalog = await load_db_catalog(session)
    return templates.TemplateResponse(
        request,
        "model_catalog.html",
        _context(
            request,
            principal,
            settings,
            model_catalog=catalog,
        ),
    )


@router.get("/operations", response_class=HTMLResponse)
async def operations_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    value = await session.get(GlobalReviewSettings, 1)
    if value is None:
        value = GlobalReviewSettings(id=1, version=1)
    pending = await session.scalar(
        select(func.count(ReviewJob.id)).where(ReviewJob.status == JobStatus.PENDING)
    )
    running = await session.scalar(
        select(func.count(ReviewJob.id)).where(ReviewJob.status == JobStatus.RUNNING)
    )
    unresolved_incidents = await session.scalar(
        select(func.count(OpsIncident.id)).where(OpsIncident.status == "OPEN")
    )
    last_change = await session.scalar(
        select(AdminAuditLog)
        .where(AdminAuditLog.target_type == "global_processing")
        .order_by(AdminAuditLog.created_at.desc())
        .limit(1)
    )
    return templates.TemplateResponse(
        request,
        "operations.html",
        _context(
            request,
            principal,
            settings,
            global_settings=value,
            pending_count=int(pending or 0),
            running_count=int(running or 0),
            unresolved_incidents=int(unresolved_incidents or 0),
            last_change=last_change,
        ),
    )


async def _change_processing(
    request: Request,
    *,
    paused: bool,
    csrf: str,
    expected_version: int,
    reason: str,
    session: AsyncSession,
    settings: Settings,
    principal: AdminPrincipal,
) -> RedirectResponse:
    verify_csrf(csrf, principal, settings)
    try:
        await set_processing_state(
            session,
            paused=paused,
            actor_login=principal.github_login,
            reason=reason or "admin console",
            expected_version=expected_version,
        )
    except ValueError as exc:
        if str(exc) == "STALE_PROCESSING_VERSION":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "운영 상태가 다른 관리자에 의해 변경되었습니다. "
                "페이지를 새로고침한 뒤 다시 시도하세요.",
            ) from exc
        raise
    return RedirectResponse("/admin/operations?updated=1", status_code=303)


@router.post("/operations/resume")
async def resume_processing(
    request: Request,
    csrf: str = Form(..., alias="_csrf"),
    expected_version: int = Form(...),
    reason: str = Form("관리자 콘솔에서 재개"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> RedirectResponse:
    return await _change_processing(
        request,
        paused=False,
        csrf=csrf,
        expected_version=expected_version,
        reason=reason,
        session=session,
        settings=settings,
        principal=principal,
    )


@router.post("/operations/pause")
async def pause_processing(
    request: Request,
    csrf: str = Form(..., alias="_csrf"),
    expected_version: int = Form(...),
    reason: str = Form("관리자 콘솔에서 일시중지"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> RedirectResponse:
    return await _change_processing(
        request,
        paused=True,
        csrf=csrf,
        expected_version=expected_version,
        reason=reason,
        session=session,
        settings=settings,
        principal=principal,
    )


@router.post("/settings")
async def update_global_settings(
    request: Request,
    csrf: str = Form(..., alias="_csrf"),
    expected_version: int | None = Form(None),
    language: str = Form("ko"),
    review_profile: str = Form("BALANCED"),
    reasoning_effort: str = Form("medium"),
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
    synchronize_debounce_seconds: int = Form(60),
    command_cooldown_seconds: int = Form(60),
    review_domain_mode: str = Form("AUTO"),
    manual_review_domains: list[str] = Form([]),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> RedirectResponse:
    verify_csrf(csrf, principal, settings)
    catalog = await load_db_catalog(session)
    try:
        validate_choice(
            language, review_profile, model or None, settings, reasoning_effort, catalog
        )
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
            or not 0 <= synchronize_debounce_seconds <= 3600
            or not 0 <= command_cooldown_seconds <= 3600
        ):
            raise ValueError("setting outside safety limit")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    form = await request.form() if request is not None else None

    def submitted(name: str) -> bool:
        return form is not None and name in form

    patch: dict[str, Any] = {}
    values: dict[str, Any] = {
        "enabled": enabled,
        "auto_review_enabled": auto_review_enabled,
        "command_review_enabled": command_review_enabled,
        "language": language,
        "review_profile": review_profile,
        "model": model or None,
        "reasoning_effort": reasoning_effort,
        "max_findings": max_findings,
        "minimum_confidence": minimum_confidence,
        "codex_timeout_seconds": codex_timeout_seconds,
        "include_low_severity": include_low_severity,
        "ignored_paths": ignored_paths,
        "minimum_severity": minimum_severity.upper(),
        "enabled_categories": enabled_categories,
        "review_domain_mode": review_domain_mode,
        "manual_review_domains": manual_review_domains,
        "review_on_opened": review_on_opened,
        "review_on_reopened": review_on_reopened,
        "review_on_ready_for_review": review_on_ready_for_review,
        "review_on_synchronize": review_on_synchronize,
        "synchronize_debounce_seconds": synchronize_debounce_seconds,
        "command_cooldown_seconds": command_cooldown_seconds,
    }
    for name, value in values.items():
        if submitted(name):
            patch[name] = value
    try:
        await update_global_policy(
            session,
            patch=patch,
            settings=settings,
            actor_login=principal.github_login,
            reason="admin settings form",
            expected_version=expected_version,
            catalog=catalog,
        )
    except ValueError as exc:
        if str(exc) in {"STALE_GLOBAL_SETTINGS_VERSION", "MISSING_GLOBAL_SETTINGS_VERSION"}:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "설정이 다른 관리자에 의해 변경되었습니다. 새로고침 후 다시 저장하세요.",
            ) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/repositories/{repository_id}/settings")
async def update_repository(
    repository_id: int,
    request: Request,
    csrf: str = Form(..., alias="_csrf"),
    expected_version: int | None = Form(None),
    enabled: bool = Form(False),
    auto_review: bool = Form(False),
    min_confidence: float | None = Form(None),
    max_findings: int | None = Form(None),
    include_low_severity: bool = Form(False),
    ignore_draft: bool = Form(False),
    ignore_patterns: str = Form(""),
    override_enabled: str = Form("inherit"),
    override_auto_review_enabled: str = Form("inherit"),
    override_command_review_enabled: str = Form("inherit"),
    override_language: str = Form(""),
    override_review_profile: str = Form(""),
    override_model: str = Form(""),
    override_reasoning_effort: str = Form(""),
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
    override_synchronize_debounce_seconds: str = Form(""),
    override_command_cooldown_seconds: str = Form(""),
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
    catalog = await load_db_catalog(session)
    # A settings form is allowed to update one section at a time.  In
    # particular, unchecked checkboxes are absent from an HTML form; treating
    # an absent field as false silently overwrites unrelated settings.
    form = await request.form()
    policy_patch: dict[str, Any] = {}

    def present(name: str) -> bool:
        return name in form

    if present("enabled"):
        policy_patch["override_enabled"] = enabled
    if present("auto_review"):
        policy_patch["override_auto_review_enabled"] = auto_review
    if present("min_confidence") and min_confidence is not None:
        policy_patch["override_minimum_confidence"] = min_confidence
    if present("max_findings") and max_findings is not None:
        policy_patch["override_max_findings"] = max_findings
    if present("include_low_severity"):
        policy_patch["override_include_low_severity"] = include_low_severity
    if present("ignore_patterns"):
        policy_patch["override_ignored_paths"] = ignore_patterns
    try:
        validate_choice(
            override_language or "ko",
            override_review_profile or "BALANCED",
            override_model or None,
            settings,
            override_reasoning_effort or "medium",
            catalog,
        )
        if present("override_enabled"):
            policy_patch["override_enabled"] = _optional_bool(override_enabled)
        if present("override_auto_review_enabled"):
            policy_patch["override_auto_review_enabled"] = _optional_bool(
                override_auto_review_enabled
            )
        if present("override_command_review_enabled"):
            policy_patch["override_command_review_enabled"] = _optional_bool(
                override_command_review_enabled
            )
        if present("override_language"):
            policy_patch["override_language"] = override_language or None
        if present("override_review_profile"):
            policy_patch["override_review_profile"] = override_review_profile or None
        if present("override_model"):
            policy_patch["override_model"] = override_model or None
        if present("override_reasoning_effort"):
            policy_patch["override_reasoning_effort"] = override_reasoning_effort or None
        if present("override_max_findings"):
            policy_patch["override_max_findings"] = _optional_int(override_max_findings, 1, 50)
        if present("override_minimum_confidence"):
            policy_patch["override_minimum_confidence"] = _optional_float(
                override_minimum_confidence, 0.8, 1
            )
        if present("override_include_low_severity"):
            policy_patch["override_include_low_severity"] = _optional_bool(
                override_include_low_severity
            )
        if present("override_ignored_paths"):
            policy_patch["override_ignored_paths"] = override_ignored_paths or None
            if policy_patch["override_ignored_paths"] is not None:
                validate_paths(str(policy_patch["override_ignored_paths"]))
        if present("override_timeout_seconds"):
            policy_patch["override_timeout_seconds"] = _optional_int(
                override_timeout_seconds, 30, 3600
            )
        if present("override_minimum_severity"):
            if override_minimum_severity and override_minimum_severity not in {
                "CRITICAL",
                "HIGH",
                "MEDIUM",
                "LOW",
            }:
                raise ValueError("unsupported minimum severity")
            policy_patch["override_minimum_severity"] = override_minimum_severity or None
        if present("override_enabled_categories"):
            policy_patch["override_enabled_categories"] = override_enabled_categories or None
        for event in ("opened", "reopened", "ready_for_review", "synchronize"):
            name = f"override_review_on_{event}"
            if present(name):
                policy_patch[name] = _optional_bool(locals()[name])
        if present("override_synchronize_debounce_seconds"):
            policy_patch["override_synchronize_debounce_seconds"] = _optional_int(
                override_synchronize_debounce_seconds, 0, 3600
            )
        if present("override_command_cooldown_seconds"):
            policy_patch["override_command_cooldown_seconds"] = _optional_int(
                override_command_cooldown_seconds, 0, 3600
            )
        if present("override_review_domain_mode"):
            if override_review_domain_mode not in {"inherit", "AUTO", "MANUAL"}:
                raise ValueError("unsupported review domain mode")
            policy_patch["override_review_domain_mode"] = (
                None if override_review_domain_mode == "inherit" else override_review_domain_mode
            )
        if present("override_manual_review_domains"):
            policy_patch["override_manual_review_domains"] = (
                ",".join(override_manual_review_domains) or None
            )
        if policy_patch.get("override_review_domain_mode") == "MANUAL":
            effective_domains(
                "MANUAL", str(policy_patch.get("override_manual_review_domains") or ""), None
            )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    try:
        await update_repository_policy(
            session,
            repository=repository,
            patch=policy_patch,
            settings=settings,
            actor_login=principal.github_login,
            reason="admin repository settings form",
            expected_version=expected_version,
            catalog=catalog,
        )
    except ValueError as exc:
        if str(exc) in {"STALE_REPOSITORY_SETTINGS_VERSION", "MISSING_REPOSITORY_SETTINGS_VERSION"}:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "저장소 설정이 변경되었습니다. 새로고침 후 다시 저장하세요.",
            ) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
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
