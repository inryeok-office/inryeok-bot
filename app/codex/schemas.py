from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Category(StrEnum):
    BUG = "BUG"
    CORRECTNESS = "CORRECTNESS"
    BEHAVIOR = "BEHAVIOR"
    REGRESSION = "REGRESSION"
    SECURITY = "SECURITY"
    DATA_INTEGRITY = "DATA_INTEGRITY"
    TRANSACTION = "TRANSACTION"
    CONCURRENCY = "CONCURRENCY"
    NULL_SAFETY = "NULL_SAFETY"
    ERROR_HANDLING = "ERROR_HANDLING"
    API_CONTRACT = "API_CONTRACT"
    PERFORMANCE = "PERFORMANCE"
    RESOURCE_LEAK = "RESOURCE_LEAK"
    TESTING = "TESTING"
    SIMPLIFICATION = "SIMPLIFICATION"


class FindingScope(StrEnum):
    """Where a finding applies.

    LINE is the legacy/default representation.  FILE and PR are accepted by
    the wire model so newer prompts can represent findings that cannot be
    honestly attached to one added line; publication policy decides whether
    they are renderable.
    """

    LINE = "LINE"
    FILE = "FILE"
    PR = "PR"


class ChangeRelation(StrEnum):
    DIRECT_CHANGE = "DIRECT_CHANGE"
    CHANGED_FILE_CONTEXT = "CHANGED_FILE_CONTEXT"
    CROSS_FILE_IMPACT = "CROSS_FILE_IMPACT"
    PR_WIDE = "PR_WIDE"
    PRE_EXISTING_UNRELATED = "PRE_EXISTING_UNRELATED"


class ChangedAnchorKind(StrEnum):
    ADDED_LINE = "ADDED_LINE"
    CHANGED_FILE = "CHANGED_FILE"


class ChangedFileAnchor(BaseModel):
    """A machine-checkable location in the supplied PR diff.

    This is deliberately separate from ``causal_evidence``: prose explains
    the mechanism, while the anchor proves that the explanation starts at a
    changed file/line.
    """

    model_config = ConfigDict(extra="forbid")
    kind: ChangedAnchorKind
    path: str = Field(min_length=1, max_length=1024)
    line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_line_shape(self) -> "ChangedFileAnchor":
        if self.kind == ChangedAnchorKind.ADDED_LINE and self.line is None:
            raise ValueError("ADDED_LINE anchors require a line")
        if self.kind == ChangedAnchorKind.CHANGED_FILE and self.line is not None:
            raise ValueError("CHANGED_FILE anchors cannot include a line")
        return self


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: FindingScope = FindingScope.LINE
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    line: int | None = Field(default=None, ge=1)
    # Legacy structured output omitted side for line findings; RIGHT is the
    # only publishable side and remains the safe backwards-compatible default.
    side: Literal["RIGHT"] | None = None
    category: Category
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=4000)
    # Optional for backwards compatibility with outputs produced before the
    # structured-evidence contract.  New prompts can provide concise,
    # redaction-safe evidence without embedding source files.
    condition: str | None = Field(default=None, max_length=1000)
    impact: str | None = Field(default=None, max_length=1000)
    evidence: str | None = Field(default=None, max_length=1200)
    suggested_fix: str | None = Field(default=None, max_length=1200)
    domain: str | None = Field(default=None, max_length=64)
    relation_to_change: ChangeRelation | None = None
    introduced_by_pr: bool | None = None
    changed_symbol: str | None = Field(default=None, max_length=300)
    causal_evidence: str | None = Field(default=None, max_length=1200)
    changed_file_anchor: ChangedFileAnchor | None = None

    @model_validator(mode="before")
    @classmethod
    def default_line_side(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("scope", "LINE") == "LINE":
            value = dict(value)
            value.setdefault("side", "RIGHT")
        return value


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=10000)
    findings: list[Finding] = Field(max_length=100)
