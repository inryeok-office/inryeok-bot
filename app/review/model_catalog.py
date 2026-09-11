"""Explicit, operator-managed Codex model catalog."""

import hashlib
import json
import re
from dataclasses import dataclass, fields
from typing import Any

from app.config import Settings

CLI_DEFAULT = "CLI_DEFAULT"
VALID_EFFORTS = frozenset({"default", "low", "medium", "high"})
VALID_STATUS = frozenset(
    {
        "CANDIDATE",
        "VERIFYING",
        "VERIFIED",
        "FAILED",
        "DISABLED",
        "RETIRED",
        "UNVERIFIED",
        "UNAVAILABLE",
        "DEPRECATED",
    }
)
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


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
    source: str
    schema_hash: str | None
    verified_at: str | None
    verified_by: str | None
    failure_code: str | None
    failure_message: str | None
    verification_id: str | None

    @property
    def selectable(self) -> bool:
        return self.enabled and self.availability_status == "VERIFIED"


def _spec(value: Any) -> ModelSpec | None:
    if not isinstance(value, dict):
        return None
    model_id = str(value.get("model_id", "")).strip()
    efforts = tuple(
        dict.fromkeys(str(item).strip().lower() for item in value.get("supported_efforts", ()))
    )
    status = str(value.get("availability_status", "UNVERIFIED")).strip().upper()
    source = str(value.get("source", "OPERATOR")).strip().upper()
    default = value.get("default_effort")
    if default is not None:
        default = str(default).strip().lower()
    if (
        not MODEL_ID_RE.fullmatch(model_id)
        or model_id == CLI_DEFAULT
        or not efforts
        or not set(efforts) <= VALID_EFFORTS
        or status not in VALID_STATUS
        or source != "OPERATOR"
        or (default is not None and default not in efforts)
    ):
        return None
    return ModelSpec(
        model_id=model_id,
        display_name=str(value.get("display_name") or model_id)[:200],
        description_ko=str(value.get("description_ko", ""))[:500],
        supported_efforts=efforts,
        default_effort=default,
        enabled=bool(value.get("enabled", False)),
        recommended=bool(value.get("recommended", False)),
        availability_status=status,
        verified_cli_version=(
            str(value["verified_cli_version"])[:64] if value.get("verified_cli_version") else None
        ),
        version=str(value.get("version", "1"))[:64],
        source=source,
        schema_hash=str(value["schema_hash"])[:64] if value.get("schema_hash") else None,
        verified_at=str(value["verified_at"])[:64] if value.get("verified_at") else None,
        verified_by=str(value["verified_by"])[:255] if value.get("verified_by") else None,
        failure_code=str(value["failure_code"])[:64] if value.get("failure_code") else None,
        failure_message=(
            str(value["failure_message"])[:300] if value.get("failure_message") else None
        ),
        verification_id=(
            str(value["verification_id"])[:128] if value.get("verification_id") else None
        ),
    )


def _raw_catalog(settings: Settings) -> str:
    raw = settings.codex_model_catalog_json.strip()
    if not raw and settings.codex_model_catalog_file is not None:
        try:
            raw = settings.codex_model_catalog_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            # A configured path may be provisioned before the first candidate
            # is approved.  Keep the safe CLI-default fallback operational.
            return ""
        except OSError as exc:
            raise ValueError("unable to read Codex model catalog file") from exc
    return raw


def load_catalog(settings: Settings) -> tuple[ModelSpec, ...]:
    raw = _raw_catalog(settings)
    if not raw:
        # Legacy CODEX_MODEL_ALLOWLIST is candidate input only. It must never
        # become selectable without explicit verification metadata.
        return ()
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


def catalog_version(settings: Settings) -> str:
    specs = load_catalog(settings)
    if not specs:
        return "empty"
    names = {field.name for field in fields(ModelSpec)}
    payload = json.dumps(
        [{name: getattr(item, name) for name in sorted(names)} for item in specs],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def available_model_ids(settings: Settings) -> tuple[str, ...]:
    return tuple(item.model_id for item in load_catalog(settings) if item.selectable)


def spec_for(settings: Settings, model: str | None) -> ModelSpec | None:
    normalized = model.strip() if isinstance(model, str) else model
    return next((item for item in load_catalog(settings) if item.model_id == normalized), None)


def normalize_model(model: str | None) -> str | None:
    """Map the UI sentinel to the nullable persisted CLI-default value."""

    if model is None:
        return None
    normalized = model.strip()
    return None if normalized in {"", CLI_DEFAULT} else normalized


def validate_model_effort(settings: Settings, model: str | None, effort: str) -> None:
    normalized_effort = effort.strip().lower()
    if normalized_effort not in VALID_EFFORTS:
        raise ValueError("reasoning effort is not allowed")
    model = normalize_model(model)
    if model is None:
        return
    normalized_model = model.strip()
    if not MODEL_ID_RE.fullmatch(normalized_model) or normalized_model == CLI_DEFAULT:
        raise ValueError("model ID is invalid")
    spec = spec_for(settings, normalized_model)
    if spec is None or not spec.selectable:
        raise ValueError("model is not verified or enabled")
    if normalized_effort not in spec.supported_efforts:
        raise ValueError("reasoning effort is not supported by the selected model")
