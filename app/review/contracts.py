"""Stable storage contracts shared by review snapshots and validation."""

PROMPT_VERSION_MAX_LENGTH = 128


def validate_prompt_version(value: str) -> str:
    """Reject an identifier before it can fail during a database flush."""
    if len(value) > PROMPT_VERSION_MAX_LENGTH:
        raise ValueError(f"prompt version exceeds {PROMPT_VERSION_MAX_LENGTH} characters")
    return value
