import logging
import re
from collections.abc import Iterable

SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*(?:bearer|token)\s+)[^\s]+"),
    re.compile(r"(?i)((?:secret|private[_ -]?key|token)[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/]+:)[^@\s]+(@)"),
    re.compile(
        r"(?i)((?:password|client[_-]?secret|webhook[_-]?secret|session[_-]?secret)[=:]\s*)[^\s,;&]+"
    ),
    re.compile(r"(?i)([?&](?:token|secret|password|key)=[^&#\s]+)"),
    re.compile(r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----", re.S),
)


def redact(value: str, secrets: Iterable[str] = ()) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    for pattern in SENSITIVE_PATTERNS:
        result = pattern.sub(
            lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]", result
        )
    return result


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
