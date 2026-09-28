"""Content-free, versioned metadata for new review executions."""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.review.diff import normalize_path, parse_unified_diff


class ContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal["1"] = "1"
    changed_files: int = Field(ge=0)
    prompt_files: int = Field(ge=0)
    archive_files: int = Field(ge=0)
    ignored_files: int = Field(ge=0)
    binary_files: int = Field(ge=0)
    size_excluded_files: int = Field(default=0, ge=0)
    diff_bytes: int = Field(ge=0)
    prompt_diff_bytes: int = Field(ge=0)
    truncated: bool = False
    truncation_reasons: list[Literal["FILE_BUDGET", "BYTE_BUDGET", "FILE_SIZE"]] = []
    related_paths: list[str] = []
    config_files: int = Field(default=0, ge=0)
    test_files: int = Field(default=0, ge=0)
    caller_callee_files: int = Field(default=0, ge=0)
    risk_signals: list[str] = []

    @field_validator("related_paths")
    @classmethod
    def safe_paths(cls, values: list[str]) -> list[str]:
        return [normalize_path(value) for value in values]

    @field_validator("risk_signals")
    @classmethod
    def safe_signals(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[A-Z_]{2,32}", value) for value in values):
            raise ValueError("unsupported risk signal")
        return values

    @model_validator(mode="after")
    def counts(self) -> "ContextManifest":
        if self.prompt_files > self.changed_files or self.prompt_diff_bytes > self.diff_bytes:
            raise ValueError("context counts are inconsistent")
        return self


class StageCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    raw: int = Field(ge=0)
    schema_valid: int = Field(ge=0)
    scope_valid: int = Field(ge=0)
    evidence_valid: int = Field(ge=0)
    deduplicated: int = Field(ge=0)
    ranked: int = Field(ge=0)
    publishable: int = Field(ge=0)
    published: int = Field(ge=0)
    rejected: int = Field(ge=0)

    @model_validator(mode="after")
    def invariant(self) -> "StageCounts":
        values = [getattr(self, key) for key in type(self).model_fields if key != "rejected"]
        if any(left < right for left, right in zip(values, values[1:], strict=False)):
            raise ValueError("pipeline counts increased")
        if self.raw != self.publishable + self.rejected:
            raise ValueError("pipeline counts do not conserve candidates")
        return self


def context_manifest(root: Path, diff: str, files: list[str]) -> ContextManifest:
    all_files = parse_unified_diff(diff)
    binary = diff.count("\nBinary files ") + diff.count("\nGIT binary patch")
    archive_files = sum(
        candidate.is_file() and not candidate.is_symlink()
        for candidate in root.rglob("*")
        if ".git" not in candidate.relative_to(root).parts
    )
    return ContextManifest(
        changed_files=len(all_files) + binary,
        prompt_files=len(files),
        archive_files=archive_files,
        ignored_files=len(set(all_files) - set(files)),
        binary_files=binary,
        diff_bytes=len(diff.encode("utf-8")),
        prompt_diff_bytes=len(diff.encode("utf-8")),
    )
