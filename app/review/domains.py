# ruff: noqa: E501
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.jobs.models import ReviewDomain, ReviewDomainMode

PROMPT_VERSION = "domains-v1"


@dataclass(frozen=True)
class DomainDetection:
    domains: tuple[str, ...]
    reasons: tuple[str, ...]


def _has(paths: set[str], *names: str) -> bool:
    return any(path.rsplit("/", 1)[-1] in names for path in paths)


def detect_domains(files: list[str]) -> DomainDetection:
    paths = {str(PurePosixPath(value)) for value in files}
    suffixes = {PurePosixPath(path).suffix.lower() for path in paths}
    domains: list[str] = [ReviewDomain.GENERAL.value]
    reasons: list[str] = ["GENERAL: all pull requests"]

    def add(domain: ReviewDomain, reason: str) -> None:
        if domain.value not in domains:
            domains.append(domain.value)
            reasons.append(f"{domain.value}: {reason}")

    if _has(paths, "build.gradle", "build.gradle.kts", "pom.xml") or suffixes & {
        ".kt",
        ".java",
        ".go",
    }:
        add(ReviewDomain.BACKEND, "server build or source changes")
    if (
        suffixes & {".tsx", ".jsx", ".vue", ".svelte"}
        or _has(paths, "package.json")
        and any("src/" in path or "app/" in path for path in paths)
    ):
        add(ReviewDomain.WEB_FRONTEND, "browser component or package changes")
    if _has(paths, "pubspec.yaml", "AndroidManifest.xml", "Package.swift") or suffixes & {".swift"}:
        add(ReviewDomain.MOBILE, "mobile platform files")
    if any(
        path.rsplit("/", 1)[-1] in {"Dockerfile", "compose.yml", "docker-compose.yml", "Caddyfile"}
        or path.endswith((".tf", ".helm.yaml", ".helm.yml"))
        or "/.github/workflows/" in f"/{path}"
        or "/k8s/" in f"/{path}"
        for path in paths
    ):
        add(ReviewDomain.INFRASTRUCTURE, "deployment or infrastructure configuration")
    if suffixes & {".sql"} or any(
        marker in path.lower()
        for path in paths
        for marker in ("migration", "alembic", "flyway", "liquibase", "schema.prisma")
    ):
        add(ReviewDomain.DATABASE, "schema or migration changes")
    if suffixes & {".ipynb"} or any(
        marker in path.lower()
        for path in paths
        for marker in ("model", "training", "inference", "dataset", "prompt")
    ):
        add(ReviewDomain.DATA_AI, "data or model pipeline signals")
    if any(
        marker in path.lower()
        for path in paths
        for marker in ("cli", "command", "sdk", "public_api", "exports")
    ):
        add(ReviewDomain.LIBRARY_SDK_CLI, "public interface or command signals")
    return DomainDetection(tuple(domains), tuple(reasons))


def effective_domains(
    mode: str, manual_domains: str | None, detected: DomainDetection | None
) -> tuple[str, ...]:
    if mode == ReviewDomainMode.MANUAL.value:
        selected = tuple(
            value.strip().upper() for value in (manual_domains or "").split(",") if value.strip()
        )
        if not selected or any(
            value not in {item.value for item in ReviewDomain} for value in selected
        ):
            raise ValueError("manual domains must contain supported domains")
        return tuple(dict.fromkeys((ReviewDomain.GENERAL.value, *selected)))
    assert detected is not None
    return detected.domains


LENSES = {
    "GENERAL": "Review concrete correctness, boundary, null, failure, concurrency, resource, security, compatibility, and regression issues only.",
    "BACKEND": "Review API contracts, authorization, validation, transactions, idempotency, data integrity, N+1, timeouts, and backward compatibility when evidenced.",
    "WEB_FRONTEND": "Review actual UI state races, stale async updates, cleanup, unsafe HTML, duplicate submission, SSR/hydration, and blocking accessibility failures.",
    "MOBILE": "Review lifecycle/dispose safety, permission denial, navigation, offline behavior, excessive polling, and platform lifecycle issues when applicable.",
    "INFRASTRUCTURE": "Review concrete secret exposure, privilege, public exposure, persistent volume, healthcheck, rollout, TLS, and destructive command risks statically.",
    "DATABASE": "Review migration compatibility, loss, null/default/constraint transitions, external-ID overflow, locks, integrity, and deployment ordering.",
    "DATA_AI": "Review data leakage, preprocessing mismatch, unbounded context/batch cost, response validation, prompt trust boundaries, and sensitive-data exposure.",
    "LIBRARY_SDK_CLI": "Review public API compatibility, defaults, CLI exit/stdout/stderr contracts, path portability, partial failures, and cleanup.",
}


def lens_text(domains: tuple[str, ...]) -> str:
    return "\n".join(f"- {LENSES[domain]}" for domain in domains)
