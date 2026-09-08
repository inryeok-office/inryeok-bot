import hashlib
import re

from app.codex.schemas import Finding


def fingerprint(finding: Finding) -> str:
    core = re.sub(r"\s+", " ", finding.body.strip().lower())[:500]
    # Location is optional for FILE/PR wire findings.  Those findings are not
    # currently publishable, but fingerprints must remain total and safe.
    path = (finding.path or "").lower()
    value = f"{path}\0{finding.title.strip().lower()}\0{core}"
    return hashlib.sha256(value.encode()).hexdigest()
