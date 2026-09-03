import hashlib
import re

from app.codex.schemas import Finding


def fingerprint(finding: Finding) -> str:
    core = re.sub(r"\s+", " ", finding.body.strip().lower())[:500]
    value = f"{finding.path.lower()}\0{finding.title.strip().lower()}\0{core}"
    return hashlib.sha256(value.encode()).hexdigest()
