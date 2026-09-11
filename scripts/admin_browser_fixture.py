"""Serve a sanitized administrator-console fixture for browser QA.

This is a test-only process. It uses a temporary SQLite database, enables the
development-only admin bypass, and never talks to GitHub, Codex, or production.
"""

# The fixture intentionally contains long, user-visible Korean sample strings.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _configure_environment(database_path: Path) -> None:
    os.environ.update(
        {
            "ENVIRONMENT": "development",
            "ADMIN_LOCAL_BYPASS": "true",
            "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
            "PUBLIC_BASE_URL": "http://127.0.0.1:8765",
            "GITHUB_APP_DISPLAY_NAME": "Codex Review Bot Fixture",
            "GITHUB_BOT_LOGIN": "fixture-review-bot[bot]",
            "ADMIN_SESSION_SECRET": "fixture-only-admin-session-secret-32-chars",
            "GITHUB_WEBHOOK_SECRET": "fixture-only-webhook-secret",
            "CODEX_MODEL_CATALOG_JSON": "",
        }
    )


async def _seed(factory: object) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.jobs.models import (
        AdminAuditLog,
        GlobalReviewSettings,
        JobStatus,
        RepositorySettings,
        ReviewJob,
        ReviewRun,
        TriggerType,
    )

    assert isinstance(factory, async_sessionmaker)
    now = datetime.now(UTC).replace(microsecond=0)
    async with factory() as session:
        session.add(
            GlobalReviewSettings(
                id=1,
                enabled=True,
                auto_review_enabled=True,
                command_review_enabled=True,
                language="ko",
                review_profile="THOROUGH",
                model=None,
                reasoning_effort="medium",
                processing_paused=False,
            )
        )
        for index in range(1, 13):
            owner = "inryeok-office" if index < 10 else "organization-with-a-long-name"
            name = (
                "geti-app-v1-with-a-deliberately-long-repository-name"
                if index == 1
                else f"service-{index}"
            )
            session.add(
                RepositorySettings(
                    id=index,
                    installation_id=987654321012345 + index,
                    github_repository_id=1000000 + index,
                    repository_owner=owner,
                    repository_name=name,
                    enabled=index != 11,
                    installed=True,
                    auto_review=index % 3 != 0,
                    min_confidence=0.9,
                    max_findings=10,
                    include_low_severity=False,
                    ignore_draft=True,
                    ignore_patterns="",
                )
            )

        success = ReviewJob(
            id=1,
            delivery_id="fixture-delivery-success",
            source_comment_id=1001,
            installation_id=987654321012346,
            repository_owner="inryeok-office",
            repository_name="geti-app-v1-with-a-deliberately-long-repository-name",
            pull_request_number=42,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.COMMAND,
            status=JobStatus.SUCCEEDED,
            attempts=1,
            execution_id="exec-fixture-success-1234567890abcdef1234567890",
            correlation_id="corr-fixture-success-1234567890abcdef1234567890",
            created_at=now - timedelta(hours=2),
            started_at=now - timedelta(hours=2) + timedelta(seconds=5),
            finished_at=now - timedelta(hours=2) + timedelta(seconds=48),
            prompt_version="detailed-review-v2",
            reasoning_effort="medium",
            review_profile="THOROUGH",
            terminal_outcome="SUCCEEDED",
            reaction_cleanup_status="CLEANED",
        )
        failed = ReviewJob(
            id=2,
            delivery_id="fixture-delivery-failed",
            source_comment_id=1002,
            installation_id=987654321012346,
            repository_owner="inryeok-office",
            repository_name="service-2",
            pull_request_number=7,
            base_sha="c" * 40,
            head_sha="d" * 40,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.FAILED,
            attempts=1,
            error_code="OUTPUT_SCHEMA_MISMATCH",
            error_category="VALIDATION",
            error_stage="schema",
            retry_policy="NEVER",
            user_action_required=True,
            user_error_message="리뷰 결과 형식을 확인할 수 없습니다. 다시 실행하기 전에 운영자 확인이 필요합니다.",
            operator_error_message="fixture-safe operator message",
            output_field="__root__",
            validation_type="schema_validation_unknown",
            diagnostic_extraction_failed=True,
            created_at=now - timedelta(minutes=26),
            started_at=now - timedelta(minutes=25),
            finished_at=now - timedelta(minutes=24),
            prompt_version="detailed-review-v2",
            reasoning_effort="medium",
            review_profile="BALANCED",
            terminal_outcome="FAILED_FINAL",
            reaction_cleanup_status="CLEANED",
            execution_id="exec-fixture-failed-1234567890abcdef1234567890",
            correlation_id="corr-fixture-failed-1234567890abcdef1234567890",
        )
        session.add_all([success, failed])
        session.add(
            ReviewRun(
                id=1,
                job_id=1,
                base_sha="a" * 40,
                head_sha="b" * 40,
                summary="변경된 인증 흐름에서 확인이 필요한 문제 1건을 게시했습니다.",
                github_review_id=9001,
                reviewed_file_count=2,
                finding_count=1,
                changed_files_count=2,
                changed_lines_count=14,
                codex_exit_code=0,
                codex_output_present=True,
                raw_findings_count=2,
                schema_valid_findings_count=2,
                changed_file_findings_count=1,
                changed_line_findings_count=1,
                confidence_findings_count=1,
                severity_findings_count=1,
                evidence_findings_count=1,
                deduplicated_findings_count=1,
                scope_valid_findings_count=1,
                rejected_findings_count=1,
                published_findings_count=1,
                rejection_counts='{"PATH_NOT_CHANGED": 1}',
                comparison_new_count=1,
                comparison_still_count=0,
                comparison_not_detected_count=0,
                created_at=now - timedelta(hours=2) + timedelta(seconds=50),
            )
        )
        session.add(
            AdminAuditLog(
                id=1,
                actor_login="fixture-admin",
                action="FIXTURE_SEED",
                target_type="fixture",
                target_id="admin-console",
                summary="브라우저 QA용 비운영 fixture",
                created_at=now - timedelta(minutes=5),
            )
        )
        await session.commit()


async def _serve(port: int) -> None:
    with tempfile.TemporaryDirectory(prefix="inryeok-admin-fixture-") as directory:
        database_path = Path(directory) / "fixture.db"
        _configure_environment(database_path)

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import get_settings
        from app.db.base import Base
        from app.db.session import get_session
        from app.main import app

        engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async def session_override() -> object:
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_settings] = lambda: get_settings()
        await _seed(factory)

        import uvicorn

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(_serve(args.port))


if __name__ == "__main__":
    main()
