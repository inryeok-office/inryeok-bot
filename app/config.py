from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: Literal["development", "test", "production"] = "production"
    public_base_url: str = ""
    database_url: str = "postgresql+psycopg://reviewbot@localhost/reviewbot"
    github_app_id: str = ""
    github_private_key: SecretStr = SecretStr("")
    github_private_key_path: Path | None = None
    github_webhook_secret: SecretStr = SecretStr("")
    github_bot_login: str = ""
    github_app_display_name: str = "Codex Review Bot"
    github_api_url: str = "https://api.github.com"
    allowed_github_accounts: str = ""
    allow_unlisted_github_accounts: bool = False
    codex_home: Path | None = None
    codex_command: str = "codex"
    codex_executor_url: str = ""
    work_root: Path = Path("work")
    review_timeout_seconds: int = Field(900, ge=30, le=3600)
    git_timeout_seconds: int = Field(60, ge=5, le=600)
    max_changed_files: int = Field(200, ge=1, le=1000)
    max_file_bytes: int = Field(1_000_000, ge=1024)
    max_diff_bytes: int = Field(5_000_000, ge=1024)
    max_webhook_body_bytes: int = Field(2_000_000, ge=16_384, le=20_000_000)
    min_work_free_bytes: int = Field(100_000_000, ge=1_000_000, le=100_000_000_000)
    default_min_confidence: float = Field(0.9, ge=0, le=1)
    default_max_findings: int = Field(10, ge=1, le=50)
    default_include_low_severity: bool = False
    default_ignore_draft: bool = True
    default_ignore_patterns: str = (
        "generated/**\n**/generated/**\nbuild/**\n**/build/**\n"
        "dist/**\n**/dist/**\n*.lock\n**/*.lock\npackage-lock.json\n**/package-lock.json"
    )
    admin_session_secret: SecretStr = SecretStr("")
    admin_github_client_id: str = ""
    admin_github_client_secret: SecretStr = SecretStr("")
    admin_local_bypass: bool = False
    worker_poll_seconds: float = Field(2.0, ge=0.1)
    worker_max_attempts: int = Field(3, ge=1, le=10)
    max_pending_jobs: int = Field(100, ge=1, le=10_000)
    max_repository_pending_jobs: int = Field(10, ge=1, le=1_000)
    stale_running_seconds: int = Field(1800, ge=60)
    codex_model_allowlist: str = ""
    default_review_language: str = "ko"
    default_review_profile: str = "BALANCED"

    @field_validator("default_ignore_patterns", mode="before")
    @classmethod
    def normalize_default_patterns(cls, value: object) -> object:
        if isinstance(value, str):
            return value.replace("\\n", "\n").replace(",", "\n")
        return value

    @field_validator("allowed_github_accounts", mode="before")
    @classmethod
    def normalize_allowed_accounts(cls, value: object) -> object:
        if isinstance(value, (list, tuple, set)):
            return ",".join(str(item) for item in value)
        return value

    @model_validator(mode="after")
    def validate_external_urls_and_secrets(self) -> "Settings":
        self.public_base_url = self.public_base_url.rstrip("/")
        parsed = urlsplit(self.public_base_url)
        is_localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if self.environment == "production":
            if parsed.scheme != "https" or not parsed.netloc or is_localhost:
                raise ValueError("production PUBLIC_BASE_URL must be a public HTTPS URL")
            if len(self.admin_session_secret.get_secret_value()) < 32:
                raise ValueError("production ADMIN_SESSION_SECRET must be at least 32 characters")
            if not self.github_bot_login:
                raise ValueError("production GITHUB_BOT_LOGIN is required")
            if not self.allowed_github_account_set:
                raise ValueError("production ALLOWED_GITHUB_ACCOUNTS must not be empty")
            if self.allow_unlisted_github_accounts:
                raise ValueError("ALLOW_UNLISTED_GITHUB_ACCOUNTS is only available in development")
        elif self.public_base_url and parsed.scheme not in {"http", "https"}:
            raise ValueError("PUBLIC_BASE_URL must use HTTP or HTTPS")
        return self

    @property
    def admin_bypass_enabled(self) -> bool:
        return self.environment == "development" and self.admin_local_bypass

    @property
    def admin_oauth_configured(self) -> bool:
        return bool(
            self.public_base_url
            and self.admin_github_client_id
            and self.admin_github_client_secret.get_secret_value()
            and self.admin_session_secret.get_secret_value()
        )

    @property
    def admin_callback_url(self) -> str:
        return f"{self.public_base_url}/auth/github/callback"

    @property
    def allowed_github_account_set(self) -> frozenset[str]:
        return frozenset(
            value.strip().casefold()
            for value in self.allowed_github_accounts.split(",")
            if value.strip()
        )

    def github_account_allowed(self, account: str) -> bool:
        if self.environment == "development" and self.allow_unlisted_github_accounts:
            return True
        return account.strip().casefold() in self.allowed_github_account_set

    @property
    def github_clone_base_url(self) -> str:
        parsed = urlsplit(self.github_api_url)
        if parsed.hostname == "api.github.com":
            return "https://github.com"
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def allowed_codex_models(self) -> tuple[str, ...]:
        return tuple(
            value.strip() for value in self.codex_model_allowlist.split(",") if value.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
