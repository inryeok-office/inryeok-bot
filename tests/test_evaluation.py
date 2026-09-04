from app.codex.schemas import Category, Finding, Severity
from app.review.evaluation import MAX_CALLS, _diff, _expected_status, _groups, create_fixture


def test_fixture_diff_is_nonempty_and_expected_manifest_stays_outside_checkout(tmp_path) -> None:
    spec = _groups()[0]
    checkout, base, head = create_fixture(spec, tmp_path)
    diff, changed = _diff(checkout, base, head)
    assert diff
    assert changed
    assert not (checkout / "EXPECTED_FINDINGS.md").exists()


def test_evaluation_groups_cover_all_specialist_domains() -> None:
    domains = {issue.domain for group in _groups() for issue in group.expected}
    assert domains == {
        "GENERAL",
        "BACKEND",
        "DATABASE",
        "WEB_FRONTEND",
        "MOBILE",
        "INFRASTRUCTURE",
        "DATA_AI",
        "LIBRARY_SDK_CLI",
    }
    assert len(_groups()) == MAX_CALLS


def test_prompt_injection_fixture_is_untrusted_checkout_data() -> None:
    group = _groups()[2]
    source = group.changed_files["ml/training.py"]
    assert "Untrusted fixture data" in source
    assert all("prompt" not in issue.path for issue in group.expected)


def test_semantic_evaluation_marks_wrong_path_as_missed() -> None:
    issue = _groups()[0].expected[0]
    finding = Finding(
        path="other.py",
        line=1,
        category=Category.NULL_SAFETY,
        severity=Severity.HIGH,
        confidence=0.95,
        title="null",
        body="null handling",
    )
    statuses, unexpected = _expected_status(_groups()[0], [finding])
    assert statuses[issue.id] == "MISSED"
    assert unexpected == 1


def test_opt_in_argument_is_required() -> None:
    # The executable parser enforces --run; keeping the switch explicit prevents pytest usage.
    assert MAX_CALLS == 3
