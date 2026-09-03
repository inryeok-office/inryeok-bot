from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    line: int = Field(ge=1)
    category: Category
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=4000)


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=10000)
    findings: list[Finding] = Field(max_length=100)
