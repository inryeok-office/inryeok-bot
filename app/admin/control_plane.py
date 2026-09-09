"""Canonical control-plane policy and administrator read models.

This module is intentionally independent from templates.  The admin UI, webhook
handlers and worker can consume the same resolved policy without reinterpreting
nullable repository fields.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.jobs.models import (
    AdminAuditLog,
    GlobalReviewSettings,
    RepositorySettings,
    ReviewJob,
    ReviewRun,
)
from app.review.settings import (
    EffectiveReviewSettings,
    resolve,
    validate_choice,
    validate_paths,
)


class PolicySource(StrEnum):
    SYSTEM = "SYSTEM"
    GLOBAL_DEFAULT = "GLOBAL_DEFAULT"
    REPOSITORY_OVERRIDE = "REPOSITORY_OVERRIDE"
    INSTALLATION_STATE = "INSTALLATION_STATE"
    PROCESSING_STATE = "PROCESSING_STATE"
    PROFILE_DEFAULT = "PROFILE_DEFAULT"
    ENVIRONMENT_LIMIT = "ENVIRONMENT_LIMIT"


class BlockReason(StrEnum):
    GLOBAL_PROCESSING_PAUSED = "GLOBAL_PROCESSING_PAUSED"
    INSTALLATION_INACTIVE = "INSTALLATION_INACTIVE"
    REPOSITORY_NOT_SELECTED = "REPOSITORY_NOT_SELECTED"
    REVIEW_DISABLED = "REVIEW_DISABLED"
    AUTO_REVIEW_DISABLED = "AUTO_REVIEW_DISABLED"
    COMMAND_REVIEW_DISABLED = "COMMAND_REVIEW_DISABLED"


@dataclass(frozen=True)
class EffectiveRepositoryPolicy:
    """Resolved policy plus provenance and execution gates."""

    settings: EffectiveReviewSettings
    installed: bool
    processing_paused: bool
    provenance: dict[str, PolicySource] = field(default_factory=dict)
    block_reasons: tuple[BlockReason, ...] = ()

    @property
    def review_enabled(self) -> bool:
        return self.settings.enabled and not self.processing_paused

    @property
    def enabled(self) -> bool:
        """Legacy template/API alias for the resolved review switch."""

        return self.review_enabled

    @property
    def auto_review_enabled(self) -> bool:
        return self.settings.auto_review_enabled and not self.processing_paused

    @property
    def auto_review(self) -> bool:
        """Legacy template/API alias for the resolved automatic switch."""

        return self.auto_review_enabled

    @property
    def command_review_enabled(self) -> bool:
        return self.settings.command_review_enabled and not self.processing_paused

    @property
    def manual_review_eligible(self) -> bool:
        return self.installed and self.review_enabled and self.command_review_enabled

    @property
    def automatic_review_eligible(self) -> bool:
        return self.installed and self.review_enabled and self.auto_review_enabled

    # The small aliases below keep existing server-rendered templates working
    # while the next console consumes this object directly.
    @property
    def review_profile(self) -> str:
        return self.settings.review_profile

    @property
    def language(self) -> str:
        return self.settings.language

    @property
    def model(self) -> str | None:
        return self.settings.model

    @property
    def reasoning_effort(self) -> str:
        return self.settings.reasoning_effort

    @property
    def minimum_confidence(self) -> float:
        return self.settings.minimum_confidence

    @property
    def minimum_severity(self) -> str:
        return self.settings.minimum_severity

    @property
    def include_low_severity(self) -> bool:
        return self.settings.include_low_severity

    @property
    def max_findings(self) -> int:
        return self.settings.max_findings


def _source(repository_override: Any, global_value: Any) -> PolicySource:
    return (
        PolicySource.REPOSITORY_OVERRIDE
        if repository_override is not None
        else PolicySource.GLOBAL_DEFAULT
    )


def resolve_repository_policy(
    global_settings: GlobalReviewSettings,
    repository: RepositorySettings,
    settings: Settings,
) -> EffectiveRepositoryPolicy:
    """Resolve the policy used by admin read models and execution gates.

    ``enabled`` and ``auto_review`` are legacy materialized columns.  Their
    authoritative meaning is the nullable override plus the global default;
    installation access remains an independent system gate.  The resolver
    therefore reports provenance explicitly and never treats a stale legacy
    false value as an administrator decision when its override is NULL.
    """

    effective = resolve(global_settings, repository, settings)
    # resolve() historically includes the materialized flags as a lower bound.
    # Correct that legacy artefact at the canonical boundary so stale rows with
    # NULL overrides inherit the global policy.  Explicit overrides still win.
    inherited_enabled = (
        global_settings.enabled
        if repository.override_enabled is None
        else repository.override_enabled
    )
    inherited_auto = (
        global_settings.auto_review_enabled
        if repository.override_auto_review_enabled is None
        else repository.override_auto_review_enabled
    )
    effective = EffectiveReviewSettings(
        **{
            **effective.__dict__,
            "enabled": bool(inherited_enabled and repository.installed),
            "auto_review_enabled": bool(inherited_auto and repository.installed),
        }
    )
    paused = bool(global_settings.processing_paused)
    reasons: list[BlockReason] = []
    if paused:
        reasons.append(BlockReason.GLOBAL_PROCESSING_PAUSED)
    if not repository.installed:
        reasons.append(BlockReason.INSTALLATION_INACTIVE)
    if not effective.enabled:
        reasons.append(BlockReason.REVIEW_DISABLED)
    if not effective.auto_review_enabled:
        reasons.append(BlockReason.AUTO_REVIEW_DISABLED)
    if not effective.command_review_enabled:
        reasons.append(BlockReason.COMMAND_REVIEW_DISABLED)
    provenance = {
        "review_enabled": _source(repository.override_enabled, global_settings.enabled),
        "auto_review_enabled": _source(
            repository.override_auto_review_enabled, global_settings.auto_review_enabled
        ),
        "command_review_enabled": _source(
            repository.override_command_review_enabled, global_settings.command_review_enabled
        ),
        "review_profile": _source(
            repository.override_review_profile, global_settings.review_profile
        ),
        "model": _source(repository.override_model, global_settings.model),
        "reasoning_effort": _source(
            repository.override_reasoning_effort, global_settings.reasoning_effort
        ),
    }
    return EffectiveRepositoryPolicy(
        settings=effective,
        installed=repository.installed,
        processing_paused=paused,
        provenance=provenance,
        block_reasons=tuple(dict.fromkeys(reasons)),
    )


_REPOSITORY_PATCH_FIELDS = frozenset(
    {
        "override_enabled",
        "override_auto_review_enabled",
        "override_command_review_enabled",
        "override_language",
        "override_review_profile",
        "override_model",
        "override_reasoning_effort",
        "override_max_findings",
        "override_minimum_confidence",
        "override_include_low_severity",
        "override_ignored_paths",
        "override_minimum_severity",
        "override_review_on_opened",
        "override_review_on_reopened",
        "override_review_on_ready_for_review",
        "override_review_on_synchronize",
        "override_review_domain_mode",
        "override_manual_review_domains",
    }
)


def validate_repository_policy_patch(
    patch: Mapping[str, Any], settings: Settings
) -> dict[str, Any]:
    """Validate and normalize a repository policy PATCH.

    Missing keys remain missing; callers must not use this helper as a full
    replacement DTO.  ``None`` is an explicit request to return to inheritance.
    """

    unknown = set(patch) - _REPOSITORY_PATCH_FIELDS
    if unknown:
        raise ValueError(f"unsupported policy fields: {', '.join(sorted(unknown))}")
    normalized = dict(patch)
    for name in (
        "override_enabled",
        "override_auto_review_enabled",
        "override_command_review_enabled",
        "override_include_low_severity",
        "override_review_on_opened",
        "override_review_on_reopened",
        "override_review_on_ready_for_review",
        "override_review_on_synchronize",
    ):
        if name in normalized and normalized[name] not in {None, True, False}:
            raise ValueError(f"{name} must be true, false, or null")
    if "override_language" in normalized and normalized["override_language"] is not None:
        language = str(normalized["override_language"])
        if language not in {"ko", "en"}:
            raise ValueError("unsupported language")
        normalized["override_language"] = language
    if (
        "override_review_profile" in normalized
        and normalized["override_review_profile"] is not None
    ):
        profile = str(normalized["override_review_profile"])
        validate_choice("ko", profile, None, settings)
        normalized["override_review_profile"] = profile
    if "override_model" in normalized and normalized["override_model"]:
        normalized["override_model"] = str(normalized["override_model"])
        validate_choice("ko", "BALANCED", normalized["override_model"], settings)
    if "override_reasoning_effort" in normalized and normalized["override_reasoning_effort"]:
        validate_choice(
            "ko", "BALANCED", None, settings, str(normalized["override_reasoning_effort"])
        )
    if "override_max_findings" in normalized and normalized["override_max_findings"] is not None:
        value = int(normalized["override_max_findings"])
        if not 1 <= value <= 50:
            raise ValueError("maximum findings outside safety limit")
        normalized["override_max_findings"] = value
    if (
        "override_minimum_confidence" in normalized
        and normalized["override_minimum_confidence"] is not None
    ):
        confidence = float(normalized["override_minimum_confidence"])
        if not 0.8 <= confidence <= 1:
            raise ValueError("minimum confidence outside safety limit")
        normalized["override_minimum_confidence"] = confidence
    if "override_ignored_paths" in normalized and normalized["override_ignored_paths"] is not None:
        normalized["override_ignored_paths"] = "\n".join(
            validate_paths(str(normalized["override_ignored_paths"]))
        )
    if "override_minimum_severity" in normalized and normalized["override_minimum_severity"]:
        severity = str(normalized["override_minimum_severity"]).upper()
        if severity not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            raise ValueError("unsupported minimum severity")
        normalized["override_minimum_severity"] = severity
    if "override_review_domain_mode" in normalized and normalized["override_review_domain_mode"]:
        mode = str(normalized["override_review_domain_mode"]).upper()
        if mode not in {"AUTO", "MANUAL"}:
            raise ValueError("unsupported review domain mode")
        normalized["override_review_domain_mode"] = mode
    if "override_manual_review_domains" in normalized:
        domains = normalized["override_manual_review_domains"]
        if domains is not None:
            if isinstance(domains, str):
                domains = [item.strip() for item in domains.split(",") if item.strip()]
            normalized["override_manual_review_domains"] = ",".join(str(item) for item in domains)
    return normalized


def apply_repository_policy_patch(
    repository: RepositorySettings, patch: Mapping[str, Any], settings: Settings
) -> tuple[str, ...]:
    """Apply only explicitly supplied fields and return changed field names."""

    normalized = validate_repository_policy_patch(patch, settings)
    changed: list[str] = []
    for field_name, value in normalized.items():
        if getattr(repository, field_name) != value:
            setattr(repository, field_name, value)
            changed.append(field_name)
    return tuple(changed)


_GLOBAL_PATCH_FIELDS = frozenset(
    {
        "enabled",
        "auto_review_enabled",
        "command_review_enabled",
        "language",
        "review_profile",
        "model",
        "reasoning_effort",
        "max_findings",
        "minimum_confidence",
        "include_low_severity",
        "minimum_severity",
        "ignored_paths",
        "enabled_categories",
        "review_on_opened",
        "review_on_reopened",
        "review_on_ready_for_review",
        "review_on_synchronize",
        "synchronize_debounce_seconds",
        "command_cooldown_seconds",
        "codex_timeout_seconds",
        "review_domain_mode",
        "manual_review_domains",
    }
)


def validate_global_policy_patch(patch: Mapping[str, Any], settings: Settings) -> dict[str, Any]:
    """Validate a global review-default PATCH without applying omitted fields."""

    unknown = set(patch) - _GLOBAL_PATCH_FIELDS
    if unknown:
        raise ValueError(f"unsupported policy fields: {', '.join(sorted(unknown))}")
    normalized = dict(patch)
    for name in (
        "enabled",
        "auto_review_enabled",
        "command_review_enabled",
        "include_low_severity",
        "review_on_opened",
        "review_on_reopened",
        "review_on_ready_for_review",
        "review_on_synchronize",
    ):
        if name in normalized and not isinstance(normalized[name], bool):
            raise ValueError(f"{name} must be boolean")
    if "language" in normalized and normalized["language"] not in {"ko", "en"}:
        raise ValueError("unsupported language")
    if "review_profile" in normalized:
        validate_choice("ko", str(normalized["review_profile"]), None, settings)
    if "model" in normalized and normalized["model"]:
        validate_choice("ko", "BALANCED", str(normalized["model"]), settings)
    if "reasoning_effort" in normalized:
        validate_choice("ko", "BALANCED", None, settings, str(normalized["reasoning_effort"]))
    if "max_findings" in normalized:
        value = int(normalized["max_findings"])
        if not 1 <= value <= 50:
            raise ValueError("maximum findings outside safety limit")
        normalized["max_findings"] = value
    if "minimum_confidence" in normalized:
        confidence = float(normalized["minimum_confidence"])
        if not 0.8 <= confidence <= 1:
            raise ValueError("minimum confidence outside safety limit")
        normalized["minimum_confidence"] = confidence
    if "minimum_severity" in normalized:
        severity = str(normalized["minimum_severity"]).upper()
        if severity not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            raise ValueError("unsupported minimum severity")
        normalized["minimum_severity"] = severity
    if "ignored_paths" in normalized and normalized["ignored_paths"] is not None:
        normalized["ignored_paths"] = "\n".join(validate_paths(str(normalized["ignored_paths"])))
    for name in (
        "synchronize_debounce_seconds",
        "command_cooldown_seconds",
        "codex_timeout_seconds",
    ):
        if name in normalized:
            value = int(normalized[name])
            minimum = 30 if name == "codex_timeout_seconds" else 0
            if not minimum <= value <= 3600:
                raise ValueError(f"{name} outside safety limit")
            normalized[name] = value
    if "review_domain_mode" in normalized:
        mode = str(normalized["review_domain_mode"]).upper()
        if mode not in {"AUTO", "MANUAL"}:
            raise ValueError("unsupported review domain mode")
        normalized["review_domain_mode"] = mode
    if "manual_review_domains" in normalized:
        domains = normalized["manual_review_domains"]
        if isinstance(domains, (tuple, list, set)):
            normalized["manual_review_domains"] = ",".join(str(item) for item in domains)
    return normalized


def apply_global_policy_patch(
    global_settings: GlobalReviewSettings,
    patch: Mapping[str, Any],
    settings: Settings,
) -> tuple[str, ...]:
    """Apply a validated global PATCH and preserve profile provenance."""

    normalized = validate_global_policy_patch(patch, settings)
    changed: list[str] = []
    for field_name, value in normalized.items():
        if getattr(global_settings, field_name) != value:
            setattr(global_settings, field_name, value)
            changed.append(field_name)
    quality_fields = {
        "minimum_confidence",
        "minimum_severity",
        "include_low_severity",
        "max_findings",
    }
    if "review_profile" in normalized and not quality_fields.intersection(normalized):
        if global_settings.profile_defaults_inherited is not True:
            global_settings.profile_defaults_inherited = True
            changed.append("profile_defaults_inherited")
    elif quality_fields.intersection(normalized):
        if global_settings.profile_defaults_inherited is not False:
            global_settings.profile_defaults_inherited = False
            changed.append("profile_defaults_inherited")
    return tuple(changed)


async def set_processing_state(
    session: AsyncSession,
    *,
    paused: bool,
    actor_login: str,
    reason: str,
    expected_version: int,
) -> GlobalReviewSettings:
    """Change only the queue gate, with an audited optimistic lock.

    Repository policy columns are deliberately not touched here.  Both the
    administrator route and operational CLI use this command so pause/resume
    has one transaction and one audit contract.
    """
    value = await session.scalar(
        select(GlobalReviewSettings).where(GlobalReviewSettings.id == 1).with_for_update()
    )
    if value is None:
        value = GlobalReviewSettings(id=1, version=1)
        session.add(value)
        await session.flush()
    current_version = int(value.version or 1)
    if expected_version != current_version:
        raise ValueError("STALE_PROCESSING_VERSION")
    changed = bool(value.processing_paused) != paused
    if changed:
        value.processing_paused = paused
        value.version = current_version + 1
        value.updated_by = actor_login
    else:
        # Idempotent requests do not mutate the row or its version, but remain
        # auditable so an operator can explain the request.
        value.version = current_version
    session.add(
        AdminAuditLog(
            actor_login=actor_login,
            action="resume_processing" if not paused else "pause_processing",
            target_type="global_processing",
            target_id="1",
            summary=(
                f"processing_paused={'true' if paused else 'false'}; "
                f"changed={'true' if changed else 'false'}; reason={reason[:240]}"
            ),
        )
    )
    await session.commit()
    await session.refresh(value)
    return value


async def update_global_policy(
    session: AsyncSession,
    *,
    patch: Mapping[str, Any],
    settings: Settings,
    actor_login: str,
    reason: str,
    expected_version: int | None,
) -> GlobalReviewSettings:
    """Apply a validated global policy patch through one audited writer."""
    if expected_version is None:
        raise ValueError("MISSING_GLOBAL_SETTINGS_VERSION")
    value = await session.scalar(
        select(GlobalReviewSettings).where(GlobalReviewSettings.id == 1).with_for_update()
    )
    if value is None:
        value = GlobalReviewSettings(id=1, version=1)
        session.add(value)
        await session.flush()
    current_version = int(value.version or 1)
    if expected_version != current_version:
        raise ValueError("STALE_GLOBAL_SETTINGS_VERSION")
    changed = apply_global_policy_patch(value, patch, settings)
    if not changed:
        await session.commit()
        await session.refresh(value)
        return value
    value.version = current_version + 1
    value.updated_by = actor_login
    session.add(
        AdminAuditLog(
            actor_login=actor_login,
            action="update",
            target_type="global_settings",
            target_id="1",
            summary=f"fields={','.join(changed)}; reason={reason[:240]}",
        )
    )
    await session.commit()
    await session.refresh(value)
    return value


async def update_repository_policy(
    session: AsyncSession,
    *,
    repository: RepositorySettings,
    patch: Mapping[str, Any],
    settings: Settings,
    actor_login: str,
    reason: str,
    expected_version: int | None,
) -> RepositorySettings:
    """Apply a repository policy patch through one audited writer."""
    if expected_version is None:
        raise ValueError("MISSING_REPOSITORY_SETTINGS_VERSION")
    locked = await session.scalar(
        select(RepositorySettings).where(RepositorySettings.id == repository.id).with_for_update()
    )
    if locked is None:
        raise ValueError("REPOSITORY_NOT_FOUND")
    current_version = int(locked.version or 1)
    if expected_version != current_version:
        raise ValueError("STALE_REPOSITORY_SETTINGS_VERSION")
    changed = apply_repository_policy_patch(locked, patch, settings)
    if changed:
        locked.version = current_version + 1
        session.add(
            AdminAuditLog(
                actor_login=actor_login,
                action="update",
                target_type="repository_settings",
                target_id=str(locked.id),
                summary=f"fields={','.join(changed)}; reason={reason[:240]}",
            )
        )
    await session.commit()
    await session.refresh(locked)
    return locked


@dataclass(frozen=True)
class RepositorySummary:
    repository_id: int
    full_name: str
    installation_id: int
    installed: bool
    policy: EffectiveRepositoryPolicy
    recent_job_id: int | None = None
    recent_job_status: str | None = None
    recent_review_id: int | None = None
    recent_webhook_id: str | None = None


async def repository_summaries(
    session: AsyncSession, settings: Settings, *, query: str | None = None
) -> list[RepositorySummary]:
    """Return stable admin rows; templates never calculate effective policy."""

    repositories = list(
        (
            await session.scalars(
                select(RepositorySettings).order_by(
                    RepositorySettings.repository_owner,
                    RepositorySettings.repository_name,
                )
            )
        ).all()
    )
    if query:
        needle = query.casefold()
        repositories = [
            item
            for item in repositories
            if needle in f"{item.repository_owner}/{item.repository_name}".casefold()
        ]
    global_settings = await session.get(GlobalReviewSettings, 1)
    if global_settings is None:
        global_settings = GlobalReviewSettings(id=1)
    summaries: list[RepositorySummary] = []
    for repository in repositories:
        policy = resolve_repository_policy(global_settings, repository, settings)
        recent_job = await session.scalar(
            select(ReviewJob)
            .where(
                ReviewJob.repository_owner == repository.repository_owner,
                ReviewJob.repository_name == repository.repository_name,
            )
            .order_by(ReviewJob.created_at.desc())
            .limit(1)
        )
        recent_run = None
        if recent_job is not None:
            recent_run = await session.scalar(
                select(ReviewRun)
                .where(ReviewRun.job_id == recent_job.id)
                .order_by(ReviewRun.id.desc())
                .limit(1)
            )
        summaries.append(
            RepositorySummary(
                repository_id=repository.id,
                full_name=f"{repository.repository_owner}/{repository.repository_name}",
                installation_id=repository.installation_id,
                installed=repository.installed,
                policy=policy,
                recent_job_id=recent_job.id if recent_job else None,
                recent_job_status=recent_job.status.value if recent_job else None,
                recent_review_id=recent_run.id if recent_run else None,
                # The legacy delivery table has no repository relation.  Do
                # not present a global latest delivery as this repository's
                # activity; the dedicated delivery read model returns UNKNOWN
                # until that relation is available.
                recent_webhook_id=None,
            )
        )
    return summaries


def job_list_query() -> Select[tuple[ReviewJob]]:
    """Shared base query for future admin/API job read models."""

    return select(ReviewJob).order_by(ReviewJob.created_at.desc())


def status_counts_query() -> Select[tuple[Any, int]]:
    return select(ReviewJob.status, func.count(ReviewJob.id)).group_by(ReviewJob.status)
