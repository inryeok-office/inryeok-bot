import pytest
from sqlalchemy import select

from app.jobs.models import AdminAuditLog, CodexModelCatalog, CodexModelVerification
from app.review.model_catalog import load_db_catalog
from app.review.model_catalog_store import (
    CatalogConflict,
    add_candidate,
    finish_verification,
    start_verification,
)

MODELS = (
    ("gpt-6-astra", "GPT-6 Astra", "복잡하고 높은 정확도가 필요한 리뷰", True),
    ("gpt-5.6-sol", "GPT-5.6 Sol", "일반적인 운영 코드 리뷰의 기본 모델", True),
    ("gpt-5.6-terra", "GPT-5.6 Terra", "속도와 분석 깊이의 균형이 필요한 리뷰", False),
    ("gpt-5.6-luna", "GPT-5.6 Luna", "빠른 피드백이 우선인 리뷰", False),
    ("gpt-5.5", "GPT-5.5", "이전 세대 모델과의 비교 또는 호환성 확인", False),
)


@pytest.mark.asyncio
async def test_candidates_are_db_backed_and_not_selectable_until_verified(app_client) -> None:
    _, factory = app_client
    async with factory() as session:
        for model_id, name, description, recommended in MODELS:
            await add_candidate(
                session,
                model_id=model_id,
                display_name=name,
                description_ko=description,
                recommended=recommended,
                actor="operator",
                reason="approved candidate list",
            )
        assert all(not item.selectable for item in await load_db_catalog(session))
        await add_candidate(
            session,
            model_id="gpt-5.6-sol",
            display_name="ignored duplicate",
            description_ko="ignored",
            recommended=False,
            actor="operator",
            reason="idempotent retry",
        )
        row = await session.scalar(
            select(CodexModelCatalog).where(CodexModelCatalog.model_id == "gpt-5.6-sol")
        )
        assert row is not None
        assert row.display_name == "GPT-5.6 Sol"


@pytest.mark.asyncio
async def test_only_successful_efforts_become_selectable_and_failures_are_recorded(
    app_client,
) -> None:
    _, factory = app_client
    async with factory() as session:
        await add_candidate(
            session,
            model_id="gpt-5.6-sol",
            display_name="GPT-5.6 Sol",
            description_ko="기본 모델",
            recommended=True,
            actor="operator",
            reason="test candidate",
        )
        medium = await start_verification(
            session,
            model_id="gpt-5.6-sol",
            reasoning_effort="medium",
            cli_version="0.151.0",
            schema_hash="a" * 64,
            actor="operator",
            reason="test medium",
            execution_id="verify-medium-123456",
            fingerprint="b" * 64,
        )
        await finish_verification(
            session,
            verification_id=medium.verification_id,
            status="SUCCEEDED",
            elapsed_seconds=1.2,
            process_exit_code=0,
            safe_error_code=None,
            safe_error_category=None,
            diagnostic_extraction_failed=False,
            actor="operator",
            reason="test success",
        )
        low = await start_verification(
            session,
            model_id="gpt-5.6-sol",
            reasoning_effort="low",
            cli_version="0.151.0",
            schema_hash="a" * 64,
            actor="operator",
            reason="test low",
            execution_id="verify-low-123456",
            fingerprint="c" * 64,
        )
        await finish_verification(
            session,
            verification_id=low.verification_id,
            status="FAILED",
            elapsed_seconds=0.2,
            process_exit_code=2,
            safe_error_code="CLI_ARGUMENT_UNSUPPORTED",
            safe_error_category="cli",
            diagnostic_extraction_failed=False,
            actor="operator",
            reason="test failure",
        )
        row = await session.scalar(
            select(CodexModelCatalog).where(CodexModelCatalog.model_id == "gpt-5.6-sol")
        )
        assert row is not None
        assert row.enabled is True
        assert row.availability_status == "VERIFIED"
        assert row.supported_efforts == ["medium"]
        failed = await session.scalar(
            select(CodexModelVerification).where(
                CodexModelVerification.verification_id == low.verification_id
            )
        )
        assert failed is not None
        assert failed.status == "FAILED"
        assert failed.safe_error_code == "CLI_ARGUMENT_UNSUPPORTED"


@pytest.mark.asyncio
async def test_stale_catalog_version_is_rejected(app_client) -> None:
    _, factory = app_client
    async with factory() as session:
        with pytest.raises(CatalogConflict, match="STALE_MODEL_CATALOG_VERSION"):
            await add_candidate(
                session,
                model_id="gpt-5.5",
                display_name="GPT-5.5",
                description_ko="호환성",
                recommended=False,
                actor="operator",
                reason="stale",
                expected_catalog_version="old",
            )


@pytest.mark.asyncio
async def test_catalog_audit_contains_safe_state_only(app_client) -> None:
    _, factory = app_client
    async with factory() as session:
        await add_candidate(
            session,
            model_id="gpt-5.5",
            display_name="GPT-5.5",
            description_ko="호환성",
            recommended=False,
            actor="operator",
            reason="candidate",
        )
        audits = (await session.scalars(select(AdminAuditLog))).all()
        assert any(item.action == "MODEL_CANDIDATE_ADDED" for item in audits)
        assert all("prompt" not in item.summary and "token" not in item.summary for item in audits)
