"""Small, operator-managed Codex model catalog.

The CLI does not provide a stable public catalog contract, so this module
accepts an explicit JSON catalog from configuration.  The legacy allowlist is
retained as a compatibility fallback and is never widened implicitly.
"""

import json
from dataclasses import dataclass
from typing import Any

from app.config import Settings


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    display_name: str
    description_ko: str
    supported_efforts: tuple[str, ...]
    default_effort: str | None
    enabled: bool
    recommended: bool
    availability_status: str
    verified_cli_version: str | None
    version: str


VALID_EFFORTS = frozenset({"default", "low", "medium", "high"})
VALID_STATUS = frozenset({"VERIFIED", "UNVERIFIED", "UNAVAILABLE", "DEPRECATED"})


def _spec(value: Any) -> ModelSpec | None:
    if not isinstance(value, dict):
        return None
    model_id = str(value.get("model_id", "")).strip()
    efforts = tuple(str(item).strip() for item in value.get("supported_efforts", ()))
    status = str(value.get("availability_status", "UNVERIFIED")).upper()
    invalid = not model_id or not efforts or not set(efforts) <= VALID_EFFORTS
    if invalid or status not in VALID_STATUS:
        return None
    default = value.get("default_effort")
    if default is not None and str(default) not in efforts:
        return None
    return ModelSpec(
        model_id=model_id,
        display_name=str(value.get("display_name") or model_id),
        description_ko=str(value.get("description_ko", ""))[:500],
        supported_efforts=efforts,
        default_effort=str(default) if default is not None else None,
        enabled=bool(value.get("enabled", False)),
        recommended=bool(value.get("recommended", False)),
        availability_status=status,
        verified_cli_version=(
            str(value["verified_cli_version"]) if value.get("verified_cli_version") else None
        ),
        version=str(value.get("version", "1")),
    )


def load_catalog(settings: Settings) -> tuple[ModelSpec, ...]:
    raw = settings.codex_model_catalog_json.strip()
    if raw:
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid Codex model catalog JSON") from exc
        if not isinstance(values, list):
            raise ValueError("Codex model catalog must be a JSON array")
        specs = tuple(_spec(item) for item in values)
        if any(item is None for item in specs):
            raise ValueError("invalid Codex model catalog entry")
        return tuple(item for item in specs if item is not None)
    # Existing production deployments use this explicit allowlist.  Treat it
    # as operator-verified legacy configuration until a catalog is supplied.
    return tuple(
        ModelSpec(
            model_id=model,
            display_name=model,
            description_ko="기존 운영 allowlist에서 확인된 모델",
            supported_efforts=("default", "low", "medium", "high"),
            default_effort=None,
            enabled=True,
            recommended=False,
            availability_status="VERIFIED",
            verified_cli_version=None,
            version="legacy",
        )
        for model in (
            item.strip() for item in settings.codex_model_allowlist.split(",") if item.strip()
        )
    )


def catalog_version(settings: Settings) -> str:
    specs = load_catalog(settings)
    return ",".join(f"{item.model_id}:{item.version}" for item in specs)[:64] or "empty"


def available_model_ids(settings: Settings) -> tuple[str, ...]:
    return tuple(
        item.model_id
        for item in load_catalog(settings)
        if item.enabled and item.availability_status == "VERIFIED"
    )


def spec_for(settings: Settings, model: str | None) -> ModelSpec | None:
    return next((item for item in load_catalog(settings) if item.model_id == model), None)


def validate_model_effort(settings: Settings, model: str | None, effort: str) -> None:
    if effort not in VALID_EFFORTS:
        raise ValueError("reasoning effort is not allowed")
    if model is None:
        return
    spec = spec_for(settings, model)
    if spec is None or not spec.enabled or spec.availability_status != "VERIFIED":
        raise ValueError("model is not verified or enabled")
    if effort not in spec.supported_efforts:
        raise ValueError("reasoning effort is not supported by the selected model")
