from dataclasses import dataclass
from fnmatch import fnmatch

from app.config import Settings
from app.jobs.models import (
    GlobalReviewSettings,
    ReasoningEffort,
    RepositorySettings,
    ReviewDomainMode,
    ReviewLanguage,
    ReviewProfile,
)
from app.review.model_catalog import validate_model_effort

MAX_FINDINGS = 50
MIN_CONFIDENCE = 0.8
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


# A profile is a review policy, not merely a presentation label.  These values
# are deliberately conservative lower bounds; explicit repository overrides
# still take precedence in ``resolve``.
@dataclass(frozen=True)
class ProfileDefaults:
    minimum_confidence: float
    minimum_severity: str
    include_low_severity: bool
    max_findings: int


PROFILE_DEFAULTS: dict[str, ProfileDefaults] = {
    "CONSERVATIVE": ProfileDefaults(0.90, "HIGH", False, 10),
    "BALANCED": ProfileDefaults(0.80, "MEDIUM", False, 20),
    # Keep the validator's established confidence floor while broadening
    # severity and finding budget for detailed reviews.
    "THOROUGH": ProfileDefaults(0.80, "LOW", True, 30),
}


@dataclass(frozen=True)
class EffectiveReviewSettings:
    enabled: bool
    auto_review_enabled: bool
    command_review_enabled: bool
    language: str
    review_profile: str
    model: str | None
    reasoning_effort: str
    max_findings: int
    minimum_confidence: float
    include_low_severity: bool
    minimum_severity: str
    enabled_categories: tuple[str, ...]
    ignored_paths: tuple[str, ...]
    codex_timeout_seconds: int
    review_on_opened: bool
    review_on_reopened: bool
    review_on_ready_for_review: bool
    review_on_synchronize: bool
    synchronize_debounce_seconds: int
    command_cooldown_seconds: int
    review_domain_mode: str
    manual_review_domains: str


def validate_choice(
    language: str,
    profile: str,
    model: str | None,
    settings: Settings,
    reasoning_effort: str = ReasoningEffort.MEDIUM.value,
) -> None:
    if language not in {item.value for item in ReviewLanguage}:
        raise ValueError("unsupported language")
    if profile not in {item.value for item in ReviewProfile}:
        raise ValueError("unsupported review profile")
    try:
        validate_model_effort(settings, model, reasoning_effort)
    except ValueError:
        raise


def validate_paths(patterns: str) -> tuple[str, ...]:
    values = tuple(line.strip() for line in patterns.splitlines() if line.strip())
    if any(
        value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/")
        for value in values
    ):
        raise ValueError("ignore path must stay within the repository")
    return values


def resolve(
    global_settings: GlobalReviewSettings,
    repository: RepositorySettings,
    settings: Settings,
    *,
    profile_defaults_inherited: bool | None = None,
) -> EffectiveReviewSettings:
    """Resolve one immutable review policy.

    ``profile_defaults_inherited`` is intentionally optional for backwards
    compatibility with the pre-provenance database schema.  Once the global
    settings row records whether its threshold fields are inherited, callers
    should pass that value explicitly.  Inferring provenance from threshold
    values is inherently ambiguous (an administrator may intentionally choose
    the same values as a profile), so the legacy heuristic remains only as a
    migration bridge.
    """

    def choose[T](override: T | None, global_value: T) -> T:
        return global_value if override is None else override

    language = choose(
        repository.override_language, global_settings.language or settings.default_review_language
    )
    profile = choose(
        repository.override_review_profile,
        global_settings.review_profile or settings.default_review_profile,
    )
    model = choose(repository.override_model, global_settings.model)
    reasoning_effort = choose(
        repository.override_reasoning_effort,
        global_settings.reasoning_effort or ReasoningEffort.MEDIUM.value,
    )
    validate_choice(language, profile, model, settings, reasoning_effort)
    profile_defaults = PROFILE_DEFAULTS[profile]
    # Before profile-aware defaults existed, the persisted global row used
    # 0.9/MEDIUM/10/false.  Treat that exact tuple as legacy defaults so a
    # selected profile takes effect without overwriting intentional settings.
    legacy_defaults = (
        profile_defaults_inherited
        if profile_defaults_inherited is not None
        else (
            getattr(global_settings, "profile_defaults_inherited", None)
            if getattr(global_settings, "profile_defaults_inherited", None) is not None
            else (
                global_settings.minimum_confidence in (None, 0.9)
                and (global_settings.minimum_severity or "MEDIUM").upper() == "MEDIUM"
                and (global_settings.max_findings or 10) == 10
                and not (global_settings.include_low_severity or False)
            )
        )
    )

    def profile_value[T](profile_default: T, current: T) -> T:
        return profile_default if legacy_defaults else current

    confidence = max(
        MIN_CONFIDENCE,
        choose(
            repository.override_minimum_confidence,
            profile_value(
                profile_defaults.minimum_confidence, global_settings.minimum_confidence or 0.9
            ),
        ),
    )
    maximum = min(
        MAX_FINDINGS,
        choose(
            repository.override_max_findings,
            profile_value(profile_defaults.max_findings, global_settings.max_findings or 10),
        ),
    )
    timeout = min(
        3600,
        max(
            30,
            choose(
                repository.override_timeout_seconds,
                global_settings.codex_timeout_seconds or settings.review_timeout_seconds,
            ),
        ),
    )
    patterns = choose(repository.override_ignored_paths, global_settings.ignored_paths or "")
    minimum_severity = choose(
        repository.override_minimum_severity,
        profile_value(
            profile_defaults.minimum_severity, global_settings.minimum_severity or "MEDIUM"
        ),
    ).upper()
    if minimum_severity not in SEVERITIES:
        raise ValueError("unsupported minimum severity")
    categories = choose(
        repository.override_enabled_categories, global_settings.enabled_categories or ""
    )
    domain_mode = choose(
        repository.override_review_domain_mode, global_settings.review_domain_mode or "AUTO"
    )
    if domain_mode not in {item.value for item in ReviewDomainMode}:
        raise ValueError("unsupported review domain mode")
    return EffectiveReviewSettings(
        # Keep the existing repository switches as a defensive lower bound.
        # An older repository row marked disabled must never become active merely
        # because a later global setting is enabled.
        enabled=(
            choose(repository.override_enabled, global_settings.enabled)
            and repository.enabled
            and repository.installed
        ),
        auto_review_enabled=(
            choose(repository.override_auto_review_enabled, global_settings.auto_review_enabled)
            and repository.auto_review
        ),
        command_review_enabled=choose(
            repository.override_command_review_enabled, global_settings.command_review_enabled
        ),
        language=language,
        review_profile=profile,
        model=model,
        reasoning_effort=reasoning_effort,
        max_findings=maximum,
        minimum_confidence=confidence,
        include_low_severity=choose(
            repository.override_include_low_severity,
            profile_value(
                profile_defaults.include_low_severity, global_settings.include_low_severity
            ),
        ),
        minimum_severity=minimum_severity,
        enabled_categories=tuple(
            value.strip().upper() for value in categories.split(",") if value.strip()
        ),
        ignored_paths=validate_paths(patterns),
        codex_timeout_seconds=timeout,
        review_on_opened=choose(
            repository.override_review_on_opened, global_settings.review_on_opened
        ),
        review_on_reopened=choose(
            repository.override_review_on_reopened, global_settings.review_on_reopened
        ),
        review_on_ready_for_review=choose(
            repository.override_review_on_ready_for_review,
            global_settings.review_on_ready_for_review,
        ),
        review_on_synchronize=choose(
            repository.override_review_on_synchronize,
            global_settings.review_on_synchronize
            if global_settings.review_on_synchronize is not None
            else False,
        ),
        synchronize_debounce_seconds=min(
            3600,
            max(
                0,
                choose(
                    repository.override_synchronize_debounce_seconds,
                    global_settings.synchronize_debounce_seconds or 60,
                ),
            ),
        ),
        command_cooldown_seconds=min(
            3600,
            max(
                0,
                choose(
                    repository.override_command_cooldown_seconds,
                    global_settings.command_cooldown_seconds or 60,
                ),
            ),
        ),
        review_domain_mode=domain_mode,
        manual_review_domains=choose(
            repository.override_manual_review_domains, global_settings.manual_review_domains or ""
        ),
    )


def path_is_ignored(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)
