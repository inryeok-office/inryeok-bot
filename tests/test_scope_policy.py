from app.codex.schemas import ChangedAnchorKind, ChangedFileAnchor, ChangeRelation, FindingScope
from app.review.diff import ChangedFile, parse_unified_diff
from app.review.validator import validate_findings_with_diagnostics
from tests.test_validation import finding


def test_explicit_unrelated_finding_is_rejected():
    result = validate_findings_with_diagnostics(
        [
            finding(
                relation_to_change=ChangeRelation.PRE_EXISTING_UNRELATED,
                introduced_by_pr=False,
            )
        ],
        {"app.py": ChangedFile("app.py", frozenset({2}))},
        0.8,
        True,
        10,
    )
    assert not result.findings
    assert result.rejection_counts == {"PRE_EXISTING_UNRELATED": 1}


def test_cross_file_requires_changed_path_or_symbol_in_causal_evidence():
    changed = {"api.py": ChangedFile("api.py", frozenset({4}))}
    rejected = validate_findings_with_diagnostics(
        [
            finding(
                scope=FindingScope.PR,
                path=None,
                relation_to_change=ChangeRelation.CROSS_FILE_IMPACT,
                introduced_by_pr=True,
                causal_evidence="same directory",
                evidence="caller and callee differ",
            )
        ],
        changed,
        0.8,
        True,
        10,
    )
    assert rejected.rejection_counts == {"CAUSAL_ANCHOR_MISSING": 1}
    accepted = validate_findings_with_diagnostics(
        [
            finding(
                scope=FindingScope.PR,
                path=None,
                relation_to_change=ChangeRelation.CROSS_FILE_IMPACT,
                introduced_by_pr=True,
                changed_symbol="Api.fetch",
                causal_evidence="api.py changes Api.fetch contract; caller receives no value",
                evidence="api.py Api.fetch now returns no value",
                condition="caller invokes Api.fetch",
                impact="request fails",
                changed_file_anchor=ChangedFileAnchor(
                    kind=ChangedAnchorKind.ADDED_LINE,
                    path="api.py",
                    line=4,
                ),
            )
        ],
        changed,
        0.8,
        True,
        10,
    )
    assert len(accepted.findings) == 1


def test_explicit_unchanged_line_uses_scope_rejection_reason():
    result = validate_findings_with_diagnostics(
        [
            finding(
                line=99,
                relation_to_change=ChangeRelation.DIRECT_CHANGE,
                introduced_by_pr=True,
            )
        ],
        {"app.py": ChangedFile("app.py", frozenset({2}))},
        0.8,
        True,
        10,
    )
    assert result.rejection_counts == {"LINE_NOT_CHANGED": 1}


def test_rename_and_deleted_files_are_represented_without_inline_lines():
    files = parse_unified_diff(
        "diff --git a/old.py b/new.py\nrename from old.py\nrename to new.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/gone.py b/gone.py\n@@ -1 +0,0 @@\n-old\n"
    )
    assert files["new.py"].old_path == "old.py"
    assert files["new.py"].status == "renamed"
    assert files["gone.py"].status == "deleted"
    assert files["gone.py"].added_lines == frozenset()
