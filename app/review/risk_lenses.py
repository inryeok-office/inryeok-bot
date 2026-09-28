"""Versioned questions; numeric external contracts require verified evidence."""

LENSES: tuple[tuple[frozenset[str], str], ...] = (
    (
        frozenset({"LOGGING", "PRIVACY", "SECURITY", "WEBHOOK"}),
        "Does newly logged exception or client data expose credentials, webhook URL tokens or PII? "
        "Is the same event masked consistently in every active console/cloud appender?",
    ),
    (
        frozenset({"AWS", "CLOUDWATCH", "BATCHING", "ENCODING"}),
        "Are character counts confused with UTF-8 bytes? Can the whole batch exceed the external "
        "API contract and be discarded on partial or permanent failure? "
        "Verify applicable contracts "
        "against dependency/version evidence; do not invent numeric limits.",
    ),
    (
        frozenset({"EXCEPTION_HANDLING", "RETRY_TIMEOUT"}),
        "Can prefix truncation remove the root cause of an exception chain? Are permanent failures "
        "retried or recoverable failures discarded? "
        "Is a timed-out execution outcome unknown before "
        "re-execution?",
    ),
    (
        frozenset({"DATABASE", "SQL_JDBC", "TRANSACTION", "CONCURRENCY", "REDIS"}),
        "Do transaction and after-commit boundaries match external effects? Can stale entities, "
        "bulk updates, locks, cache ordering or retries violate integrity or idempotency? "
        "Do SQL/JDBC "
        "exception messages introduce personal data into new output paths?",
    ),
    (
        frozenset({"LOGGING", "SERIALIZATION", "FILE_IO", "CACHE", "SCHEDULER"}),
        "Does changed processing scan or copy unbounded strings/collections, "
        "repeat queries or I/O, "
        "or retain resources under a concrete production workload? Preserve masking at boundaries.",
    ),
)


def risk_lens_text(signals: tuple[str, ...]) -> str:
    selected = set(signals)
    return "\n".join(f"- {question}" for applicable, question in LENSES if applicable & selected)
