"""Deterministic risk signals from independent source evidence families."""

import re

# Each signal requires both families; filenames alone are never sufficient.
RULES: dict[str, tuple[str, str]] = {
    "LOGGING": (r"logback|logging|logger|appender|slf4j", r"log\.|message|format|encoder|layout"),
    "SECURITY": (r"authorization|credential|password|secret", r"token|auth|mask|redact|sanitize"),
    "AUTH": (r"authentication|authorization|jwt|oauth", r"permission|verify|token|role"),
    "PRIVACY": (r"personal|pii|email|password|token", r"log|serialize|exception|response"),
    "AWS": (r"amazonaws|awssdk|aws[._-]", r"client|region|request|batch"),
    "CLOUDWATCH": (r"cloudwatch|putlogevents", r"logevent|batch|message|loggroup"),
    "WEBHOOK": (r"webhook|discord", r"post|request|url|http|client"),
    "HTTP_CLIENT": (r"feign|httpclient|httpx|requests\.|fetch\(", r"send|request|post|timeout"),
    "SQL_JDBC": (r"jdbc|sqlexception|sqlintegrity|hibernate", r"exception|query|message|execute"),
    "DATABASE": (r"repository|sql|jdbc|database|entity", r"query|select|update|delete|save"),
    "REDIS": (r"redis|redisson", r"get|set|lock|client|cache"),
    "BATCHING": (r"batch|chunk", r"size|limit|flush|send|stream"),
    "PAGINATION": (r"pageable|pagination|cursor|offset", r"limit|next|page|query"),
    "SERIALIZATION": (r"serialize|deserialize|encoder|decoder|json", r"parse|format|read|write"),
    "ENCODING": (r"utf.?8|charset|encoding|escape", r"byte|length|substring|replace"),
    "EXCEPTION_HANDLING": (r"throwable|exception|catch", r"cause|throw|retry|return|truncate"),
    "CONCURRENCY": (r"synchronized|thread|locking|atomic|volatile", r"queue|lock|state|update"),
    "TRANSACTION": (r"transaction|aftercommit|requires_new", r"commit|rollback|flush|save|update"),
    "RETRY_TIMEOUT": (r"retry|timeout", r"attempt|delay|backoff|exception|unknown"),
    "FILE_IO": (r"file|path|stream", r"open|read|write|unlink|delete"),
    "CACHE": (r"cache|caffeine|redis", r"expire|invalidate|ttl|put|set"),
    "SCHEDULER": (r"scheduled|scheduler|cron", r"run|execute|job|task"),
}
SIGNALS = frozenset(RULES)


def detect_risks(diff: str, metadata: str = "") -> tuple[str, ...]:
    # Removed code cannot by itself activate a lens. Hunk labels provide symbol
    # context but repository-controlled instructions are never executed.
    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    text = (added + "\n" + metadata).casefold()
    return tuple(
        signal
        for signal, families in RULES.items()
        if all(re.search(pattern, text) for pattern in families)
    )
