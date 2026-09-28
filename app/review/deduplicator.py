import hashlib
import re

from app.codex.schemas import Finding


def fingerprint(finding: Finding) -> str:
    if (
        finding.defect_identity
        and finding.causal_chain
        and finding.changed_symbol
        and finding.causal_evidence
        and finding.introduced_by_pr is True
        and finding.changed_file_anchor is not None
        and all(re.fullmatch(r"[a-z][a-z0-9_]{1,127}", value) for value in finding.causal_chain)
    ):
        identity = "\0".join(
            (
                "semantic-v1",
                finding.changed_file_anchor.path.replace("\\", "/"),
                finding.changed_symbol,
                finding.category.value,
                finding.defect_identity,
                *finding.causal_chain,
            )
        )
        return hashlib.sha256(identity.encode()).hexdigest()
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
