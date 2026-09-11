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
    # OAuth authentication and global administrator authorization are separate
    # concerns.  An empty value is intentionally allowed at model construction
    # time so non-production tooling can boot, but it makes every authenticated
    # user ineligible for the administrator console.
    superadmin_github_logins: str = ""
    worker_poll_seconds: float = Field(2.0, ge=0.1)
    worker_max_attempts: int = Field(3, ge=1, le=10)
    max_pending_jobs: int = Field(100, ge=1, le=10_000)
    max_repository_pending_jobs: int = Field(10, ge=1, le=1_000)
    stale_running_seconds: int = Field(1800, ge=60)
    codex_model_allowlist: str = ""
    # JSON is deliberately operator-managed rather than scraped from an
    # undocumented CLI endpoint. Empty means no selectable catalog entries.
    codex_model_catalog_json: str = ""
    codex_model_catalog_file: Path | None = None
    codex_cli_version: str = ""
    executor_runtime_version: str = ""
    ops_alert_webhook_url: SecretStr = SecretStr("")
    ops_alert_cooldown_seconds: int = Field(900, ge=60, le=86400)
    watchdog_webhook_seconds: int = Field(900, ge=60, le=86400)
    watchdog_pending_seconds: int = Field(1800, ge=60, le=172800)
    default_review_language: str = "ko"
    default_review_profile: str = "THOROUGH"

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

    @field_validator("codex_model_catalog_file", mode="before")
    @classmethod
    def normalize_catalog_file(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
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
            # Installation identity, not an organization-name allowlist, is the
            # trust boundary for GitHub webhooks.  Keep the legacy settings
            # fields readable for old environments, but never require or use
            # them to authorize a repository.  A signed webhook contains an
            # installation id and all GitHub API calls are made with that
            # installation's token.
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
    def superadmin_github_login_set(self) -> frozenset[str]:
        """Normalized GitHub logins allowed to use the global admin console."""

        return frozenset(
            value.strip().casefold()
            for value in self.superadmin_github_logins.split(",")
            if value.strip()
        )

    def is_superadmin_login(self, github_login: str) -> bool:
        """Return whether a GitHub login has global-console administrator access."""

        return github_login.strip().casefold() in self.superadmin_github_login_set

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
        """Legacy compatibility shim; installation id is the trust boundary.

        Callers must validate the signed webhook installation and use its
        installation token.  Account login strings are metadata and are not an
        authorization boundary (this method is intentionally permissive so an
        old caller cannot silently reintroduce single-tenant behavior).
        """
        del account
        return True

    @property
    def github_clone_base_url(self) -> str:
        parsed = urlsplit(self.github_api_url)
        if parsed.hostname == "api.github.com":
            return "https://github.com"
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def allowed_codex_models(self) -> tuple[str, ...]:
        from app.review.model_catalog import available_model_ids

        return available_model_ids(self)


@lru_cache
def get_settings() -> Settings:
    return Settings()
