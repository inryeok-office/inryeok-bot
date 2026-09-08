import hashlib
import re

from app.codex.schemas import Finding


def fingerprint(finding: Finding) -> str:
    core = re.sub(r"\s+", " ", finding.body.strip().lower())[:500]
    evidence = re.sub(r"\s+", " ", (finding.evidence or "").strip().lower())[:300]
    # Keep scope and the concrete location in the identity.  Without the line
    # number, two independent defects in one file were incorrectly collapsed;
    # without scope, a file summary could hide a distinct inline finding.
    path = (finding.path or "").lower()
    line = str(finding.line or "")
    value = "\0".join(
        (finding.scope.value, path, line, finding.title.strip().lower(), core, evidence)
    )
    return hashlib.sha256(value.encode()).hexdigest()
