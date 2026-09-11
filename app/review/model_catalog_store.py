"""Transactional PostgreSQL model-catalog commands.

The store deliberately exposes no raw executor output.  Verification evidence
is bounded to identifiers, versions, timings, and safe error categories.
"""

import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import AdminAuditLog, CodexModelCatalog, CodexModelVerification
from app.review.model_catalog import (
    MODEL_ID_RE,
    VALID_EFFORTS,
    ModelSpec,
    catalog_version_for,
    load_db_catalog,
)


class CatalogConflict(ValueError):
    """Raised when an operator writes against a stale catalog snapshot."""


CLI_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")


def _require_actor_reason(actor: str, reason: str) -> tuple[str, str]:
    actor = actor.strip()
    reason = " ".join(reason.split()).strip()
    if not actor or not reason:
        raise ValueError("actor and reason are required")
    return actor[:255], reason[:300]


async def _check_version(session: AsyncSession, expected: str | None) -> str:
    current = catalog_version_for(await load_db_catalog(session))
    if expected is not None and expected != current:
        raise CatalogConflict("STALE_MODEL_CATALOG_VERSION")
    return current


async def _audit(
    session: AsyncSession,
    *,
    action: str,
    target_id: str,
    actor: str,
    reason: str,
    old_value: str = "",
    new_value: str = "",
    verification_id: str | None = None,
) -> None:
    values = f"reason={reason}; old={old_value[:180]}; new={new_value[:180]}"
    if verification_id:
        values += f"; verification_id={verification_id[:128]}"
    session.add(
        AdminAuditLog(
            actor_login=actor,
            action=action,
            target_type="model_catalog",
            target_id=target_id[:128],
            summary=values[:900],
        )
    )


async def add_candidate(
    session: AsyncSession,
    *,
    model_id: str,
    display_name: str,
    purpose_ko: str = "",
    description_ko: str,
    recommended: bool,
    actor: str,
    reason: str,
    expected_catalog_version: str | None = None,
) -> CodexModelCatalog:
    actor, reason = _require_actor_reason(actor, reason)
    model_id = model_id.strip()
    if not MODEL_ID_RE.fullmatch(model_id) or model_id == "CLI_DEFAULT":
        raise ValueError("model ID is invalid")
    if not display_name.strip():
        raise ValueError("display name is required")
    await _check_version(session, expected_catalog_version)
    row = await session.scalar(
        select(CodexModelCatalog).where(CodexModelCatalog.model_id == model_id).with_for_update()
    )
    if row is not None:
        return row
    row = CodexModelCatalog(
        model_id=model_id,
        display_name=display_name.strip()[:200],
        purpose_ko=purpose_ko.strip()[:64],
        description_ko=description_ko.strip()[:500],
        recommended=recommended,
        enabled=False,
        availability_status="CANDIDATE",
        source="OPERATOR",
        supported_efforts=[],
        default_effort=None,
        version=1,
    )
    session.add(row)
    await session.flush()
    await _audit(
        session,
        action="MODEL_CANDIDATE_ADDED",
        target_id=model_id,
        actor=actor,
        reason=reason,
        new_value="CANDIDATE",
    )
    await session.commit()
    await session.refresh(row)
    return row


async def start_verification(
    session: AsyncSession,
    *,
    model_id: str,
    reasoning_effort: str,
    cli_version: str,
    schema_hash: str,
    actor: str,
    reason: str,
    execution_id: str,
    fingerprint: str,
    expected_catalog_version: str | None = None,
) -> CodexModelVerification:
    actor, reason = _require_actor_reason(actor, reason)
    effort = reasoning_effort.strip().lower()
    if effort not in VALID_EFFORTS - {"default"}:
        raise ValueError("reasoning effort is not allowed")
    await _check_version(session, expected_catalog_version)
    row = await session.scalar(
        select(CodexModelCatalog)
        .where(CodexModelCatalog.model_id == model_id.strip())
        .with_for_update()
    )
    if row is None:
        raise ValueError("model candidate does not exist")
    prior = await session.scalar(
        select(CodexModelVerification)
        .where(
            CodexModelVerification.model_id == row.model_id,
            CodexModelVerification.reasoning_effort == effort,
        )
        .order_by(CodexModelVerification.id.desc())
    )
    if prior is not None:
        if prior.status == "SUCCEEDED":
            return prior
        raise ValueError("verification already has a terminal result")
    verification_id = f"mv-{uuid4().hex}"
    verification = CodexModelVerification(
        verification_id=verification_id,
        model_id=row.model_id,
        reasoning_effort=effort,
        status="RUNNING",
        cli_version=cli_version[:64],
        schema_hash=schema_hash[:64],
        execution_id=execution_id[:64],
        fingerprint=fingerprint[:64],
        actor_login=actor,
    )
    session.add(verification)
    row.availability_status = "VERIFYING"
    row.version += 1
    await _audit(
        session,
        action="MODEL_VERIFICATION_STARTED",
        target_id=row.model_id,
        actor=actor,
        reason=reason,
        old_value="CANDIDATE",
        new_value="VERIFYING",
        verification_id=verification_id,
    )
    await session.commit()
    await session.refresh(verification)
    return verification


async def finish_verification(
    session: AsyncSession,
    *,
    verification_id: str,
    status: str,
    elapsed_seconds: float | None,
    process_exit_code: int | None,
    safe_error_code: str | None,
    safe_error_category: str | None,
    diagnostic_extraction_failed: bool,
    actor: str,
    reason: str,
) -> CodexModelVerification:
    actor, reason = _require_actor_reason(actor, reason)
    verification = await session.scalar(
        select(CodexModelVerification)
        .where(CodexModelVerification.verification_id == verification_id)
        .with_for_update()
    )
    if verification is None:
        raise ValueError("verification does not exist")
    if verification.status in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
        return verification
    if status not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
        raise ValueError("verification status is invalid")
    verification.status = status
    verification.elapsed_seconds = elapsed_seconds
    verification.process_exit_code = process_exit_code
    verification.safe_error_code = safe_error_code[:64] if safe_error_code else None
    verification.safe_error_category = safe_error_category[:64] if safe_error_category else None
    verification.diagnostic_extraction_failed = diagnostic_extraction_failed
    verification.verified_at = datetime.now(UTC)
    model = await session.scalar(
        select(CodexModelCatalog)
        .where(CodexModelCatalog.model_id == verification.model_id)
        .with_for_update()
    )
    if model is None:
        raise ValueError("model catalog row is missing")
    previous = list(model.supported_efforts or [])
    if status == "SUCCEEDED":
        efforts = list(dict.fromkeys([*previous, verification.reasoning_effort]))
        model.supported_efforts = efforts
        model.default_effort = model.default_effort or verification.reasoning_effort
        model.enabled = True
        model.availability_status = "VERIFIED"
        model.verified_cli_version = verification.cli_version
        model.schema_hash = verification.schema_hash
        model.verified_at = verification.verified_at
        model.verified_by = actor
        model.verification_id = verification.verification_id
        model.failure_code = None
        model.failure_message = None
        action = "MODEL_VERIFICATION_SUCCEEDED"
    else:
        model.enabled = bool(previous)
        model.availability_status = "VERIFIED" if previous else "FAILED"
        model.failure_code = verification.safe_error_code
        model.failure_message = safe_error_category[:300] if safe_error_category else None
        action = "MODEL_VERIFICATION_FAILED"
    model.version += 1
    await _audit(
        session,
        action=action,
        target_id=model.model_id,
        actor=actor,
        reason=reason,
        old_value=verification.reasoning_effort,
        new_value=status,
        verification_id=verification.verification_id,
    )
    await session.commit()
    await session.refresh(verification)
    return verification


async def annotate_verification_cli_version(
    session: AsyncSession,
    *,
    verification_id: str,
    cli_version: str,
    actor: str,
    reason: str,
) -> CodexModelVerification:
    """Record independently observed CLI metadata without rerunning Codex."""

    actor, reason = _require_actor_reason(actor, reason)
    cli_version = cli_version.strip()
    if not CLI_VERSION_RE.fullmatch(cli_version):
        raise ValueError("CLI version is invalid")
    verification = await session.scalar(
        select(CodexModelVerification)
        .where(CodexModelVerification.verification_id == verification_id)
        .with_for_update()
    )
    if verification is None:
        raise ValueError("verification does not exist")
    old_version = verification.cli_version or "unknown"
    verification.cli_version = cli_version
    model = await session.scalar(
        select(CodexModelCatalog)
        .where(CodexModelCatalog.model_id == verification.model_id)
        .with_for_update()
    )
    if model is not None and model.verification_id == verification.verification_id:
        model.verified_cli_version = cli_version
        model.version += 1
    await _audit(
        session,
        action="MODEL_VERIFICATION_METADATA_UPDATED",
        target_id=verification.model_id,
        actor=actor,
        reason=reason,
        old_value=old_version,
        new_value=cli_version,
        verification_id=verification.verification_id,
    )
    await session.commit()
    await session.refresh(verification)
    return verification


def selectable_efforts(spec: ModelSpec | None) -> tuple[str, ...]:
    if spec is None or not spec.selectable:
        return ()
    return tuple(value for value in spec.supported_efforts if value != "default")
