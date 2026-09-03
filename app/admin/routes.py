from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import AdminPrincipal, csrf_token, require_admin, verify_csrf
from app.config import Settings, get_settings
from app.db.session import get_session
from app.jobs.models import RepositorySettings, ReviewJob
from app.jobs.repository import JobRepository

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


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
        await session.scalars(select(ReviewJob).order_by(ReviewJob.created_at.desc()).limit(20))
    ).all()
    repositories = (
        await session.scalars(
            select(RepositorySettings).order_by(
                RepositorySettings.repository_owner, RepositorySettings.repository_name
            )
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        _context(request, principal, settings, jobs=jobs, repositories=repositories),
    )


@router.get("/jobs", response_class=HTMLResponse)
async def jobs(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> HTMLResponse:
    values = (
        await session.scalars(select(ReviewJob).order_by(ReviewJob.created_at.desc()).limit(100))
    ).all()
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
            select(RepositorySettings).order_by(
                RepositorySettings.repository_owner, RepositorySettings.repository_name
            )
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
    return templates.TemplateResponse(
        request,
        "repository_detail.html",
        _context(request, principal, settings, repository=repository),
    )


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
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> RedirectResponse:
    verify_csrf(csrf, principal, settings)
    repository = await session.get(RepositorySettings, repository_id)
    if not repository:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")
    await _require_repository_admin(repository, principal, settings)
    repository.enabled = enabled
    repository.auto_review = auto_review
    repository.min_confidence = max(0, min(1, min_confidence))
    repository.max_findings = max(1, min(50, max_findings))
    repository.include_low_severity = include_low_severity
    repository.ignore_draft = ignore_draft
    repository.ignore_patterns = ignore_patterns
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
    await JobRepository(session).retry(job_id)
    return RedirectResponse(f"/admin/jobs/{job_id}", status_code=303)
