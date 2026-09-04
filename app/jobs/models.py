from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TriggerType(StrEnum):
    AUTO = "AUTO"
    COMMAND = "COMMAND"
    RETRY = "RETRY"


class ReviewLanguage(StrEnum):
    KO = "ko"
    EN = "en"


class ReviewProfile(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    THOROUGH = "THOROUGH"


class ReviewJob(Base):
    __tablename__ = "review_jobs"
    __table_args__ = (
        UniqueConstraint("delivery_id"),
        UniqueConstraint("source_comment_id", name="uq_review_jobs_source_comment_id"),
        Index(
            "uq_review_policy_non_command",
            "repository_owner",
            "repository_name",
            "pull_request_number",
            "head_sha",
            "trigger_type",
            unique=True,
            postgresql_where=text("trigger_type <> 'COMMAND'"),
            sqlite_where=text("trigger_type <> 'COMMAND'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(100))
    source_comment_id: Mapped[int | None] = mapped_column(BigInteger)
    installation_id: Mapped[int]
    repository_owner: Mapped[str] = mapped_column(String(255))
    repository_name: Mapped[str] = mapped_column(String(255))
    pull_request_number: Mapped[int]
    base_sha: Mapped[str] = mapped_column(String(64))
    head_sha: Mapped[str] = mapped_column(String(64))
    trigger_type: Mapped[TriggerType] = mapped_column(Enum(TriggerType, native_enum=False))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    github_check_run_id: Mapped[int | None] = mapped_column(BigInteger)
    retry_of_job_id: Mapped[int | None] = mapped_column(ForeignKey("review_jobs.id"))
    superseded_by_head_sha: Mapped[str | None] = mapped_column(String(64))
    runs: Mapped[list["ReviewRun"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class RepositorySettings(Base):
    __tablename__ = "repository_settings"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "repository_owner", "repository_name", name="uq_repository_settings"
        ),
        CheckConstraint("min_confidence >= 0 AND min_confidence <= 1", name="ck_min_confidence"),
        CheckConstraint("max_findings >= 1 AND max_findings <= 50", name="ck_max_findings"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[int]
    repository_owner: Mapped[str] = mapped_column(String(255))
    repository_name: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    installed: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_review: Mapped[bool] = mapped_column(Boolean, default=True)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.9)
    max_findings: Mapped[int] = mapped_column(Integer, default=10)
    include_low_severity: Mapped[bool] = mapped_column(Boolean, default=False)
    ignore_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    ignore_patterns: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    override_enabled: Mapped[bool | None] = mapped_column(Boolean)
    override_auto_review_enabled: Mapped[bool | None] = mapped_column(Boolean)
    override_command_review_enabled: Mapped[bool | None] = mapped_column(Boolean)
    override_language: Mapped[str | None] = mapped_column(String(8))
    override_review_profile: Mapped[str | None] = mapped_column(String(32))
    override_model: Mapped[str | None] = mapped_column(String(128))
    override_max_findings: Mapped[int | None] = mapped_column(Integer)
    override_minimum_confidence: Mapped[float | None] = mapped_column(Float)
    override_include_low_severity: Mapped[bool | None] = mapped_column(Boolean)
    override_ignored_paths: Mapped[str | None] = mapped_column(Text)
    override_timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    override_minimum_severity: Mapped[str | None] = mapped_column(String(16))
    override_enabled_categories: Mapped[str | None] = mapped_column(Text)
    override_review_on_opened: Mapped[bool | None] = mapped_column(Boolean)
    override_review_on_reopened: Mapped[bool | None] = mapped_column(Boolean)
    override_review_on_ready_for_review: Mapped[bool | None] = mapped_column(Boolean)
    override_review_on_synchronize: Mapped[bool | None] = mapped_column(Boolean)


class GlobalReviewSettings(Base):
    __tablename__ = "global_review_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_review_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    command_review_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(8), default=ReviewLanguage.KO.value)
    review_profile: Mapped[str] = mapped_column(String(32), default=ReviewProfile.BALANCED.value)
    model: Mapped[str | None] = mapped_column(String(128))
    max_findings: Mapped[int] = mapped_column(Integer, default=10)
    minimum_confidence: Mapped[float] = mapped_column(Float, default=0.9)
    include_low_severity: Mapped[bool] = mapped_column(Boolean, default=False)
    minimum_severity: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    enabled_categories: Mapped[str] = mapped_column(Text, default="")
    ignored_paths: Mapped[str] = mapped_column(Text, default="")
    review_on_opened: Mapped[bool] = mapped_column(Boolean, default=True)
    review_on_reopened: Mapped[bool] = mapped_column(Boolean, default=True)
    review_on_ready_for_review: Mapped[bool] = mapped_column(Boolean, default=True)
    review_on_synchronize: Mapped[bool] = mapped_column(Boolean, default=True)
    codex_timeout_seconds: Mapped[int] = mapped_column(Integer, default=900)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_login: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(128))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewRun(Base):
    __tablename__ = "review_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("review_jobs.id", ondelete="CASCADE"))
    base_sha: Mapped[str] = mapped_column(String(64))
    head_sha: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    github_review_id: Mapped[int | None] = mapped_column(BigInteger)
    reviewed_file_count: Mapped[int]
    finding_count: Mapped[int]
    changed_files_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_lines_count: Mapped[int] = mapped_column(Integer, default=0)
    codex_exit_code: Mapped[int | None] = mapped_column(Integer)
    codex_output_present: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    schema_valid_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_file_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_line_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    severity_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    deduplicated_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    published_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    job: Mapped[ReviewJob] = relationship(back_populates="runs")
    findings: Mapped[list["FindingRecord"]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan"
    )


class FindingRecord(Base):
    __tablename__ = "finding_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    review_run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(1024))
    line: Mapped[int]
    severity: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float]
    title: Mapped[str] = mapped_column(String(300))
    fingerprint: Mapped[str] = mapped_column(String(64))
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    review_run: Mapped[ReviewRun] = relationship(back_populates="findings")


class ReviewFailureNotice(Base):
    __tablename__ = "review_failure_notices"
    __table_args__ = (
        UniqueConstraint(
            "repository_owner",
            "repository_name",
            "pull_request_number",
            "head_sha",
            "error_category",
            name="uq_review_failure_notice",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_owner: Mapped[str] = mapped_column(String(255))
    repository_name: Mapped[str] = mapped_column(String(255))
    pull_request_number: Mapped[int]
    head_sha: Mapped[str] = mapped_column(String(64))
    error_category: Mapped[str] = mapped_column(String(32))
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(100), unique=True)
    event_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    github_user_id: Mapped[int] = mapped_column(BigInteger)
    github_login: Mapped[str] = mapped_column(String(255))
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
