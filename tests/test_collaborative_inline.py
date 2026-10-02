from app.codex.schemas import Category, Finding, ReviewType, Severity
from app.review.diff import ChangedFile, no_reviewable_reason
from app.review.validator import validate_findings_with_diagnostics


def observation(review_type: ReviewType, **changes: object) -> Finding:
    values: dict[str, object] = {
        "path": "src/example.py",
        "line": 10,
        "category": Category.BUG,
        "severity": Severity.LOW,
        "confidence": 0.95,
        "title": "의도를 드러내는 이름을 사용할까요?",
        "body": "현재 동작은 유지되지만 변경한 분기 이름이 역할을 숨깁니다.",
        "review_type": review_type,
        "why_it_matters": "호출자가 분기의 정책과 실패 경로를 빠르게 구분할 수 있습니다.",
        "suggested_action": "정책을 드러내는 이름으로 바꿔 주세요.",
        "blocking": False,
    }
    values.update(changes)
    return Finding(**values)


def test_actionable_suggestion_and_specific_positive_are_publishable() -> None:
    changed = {"src/example.py": ChangedFile("src/example.py", frozenset({10, 11}))}
    result = validate_findings_with_diagnostics(
        [
            observation(ReviewType.SUGGESTION),
            observation(
                ReviewType.POSITIVE,
                line=11,
                title="경계에서 값을 정규화합니다",
                body="입력 직후 정규화해 이후 분기가 하나의 표현만 다룹니다.",
                suggested_action=None,
            ),
        ],
        changed,
        0.8,
        True,
        10,
        max_inline_comments=3,
    )
    assert [item.review_type for item in result.findings] == [
        ReviewType.SUGGESTION,
        ReviewType.POSITIVE,
    ]


def test_generic_positive_and_unactionable_question_are_rejected() -> None:
    changed = {"src/example.py": ChangedFile("src/example.py", frozenset({10, 11}))}
    result = validate_findings_with_diagnostics(
        [
            observation(ReviewType.POSITIVE, why_it_matters="좋습니다", suggested_action=None),
            observation(
                ReviewType.QUESTION,
                line=11,
                title="확인",
                body="정책을 검토합니다.",
                why_it_matters=None,
            ),
        ],
        changed,
        0.8,
        True,
        10,
    )
    assert result.findings == []
    assert result.rejection_counts == {
        "POSITIVE_NOT_SPECIFIC": 1,
        "QUESTION_NOT_ACTIONABLE": 1,
    }


def test_positive_is_limited_to_one_and_high_must_fix_gets_emergency_budget() -> None:
    changed = {"src/example.py": ChangedFile("src/example.py", frozenset(range(10, 18)))}
    findings = [
        observation(ReviewType.POSITIVE, line=10, suggested_action=None),
        observation(ReviewType.POSITIVE, line=11, suggested_action=None),
    ] + [
        observation(
            ReviewType.MUST_FIX,
            line=line,
            category=Category.BUG,
            severity=Severity.HIGH,
            condition="입력이 비어 있는 경우",
            impact="요청이 잘못된 계정으로 처리됩니다",
            suggested_action="빈 값을 먼저 거부해 주세요.",
        )
        for line in range(12, 17)
    ]
    result = validate_findings_with_diagnostics(
        findings, changed, 0.8, True, 10, max_inline_comments=3
    )
    assert len(result.findings) == 5
    assert sum(item.review_type == ReviewType.POSITIVE for item in result.findings) == 0


def test_suggested_patch_rejects_diff_content() -> None:
    changed = {"src/example.py": ChangedFile("src/example.py", frozenset({10}))}
    result = validate_findings_with_diagnostics(
        [observation(ReviewType.SUGGESTION, suggested_patch="@@ -1 +1 @@\n-old\n+new")],
        changed,
        0.8,
        True,
        10,
    )
    assert result.rejection_counts == {"SUGGESTED_PATCH_INVALID": 1}


def test_no_reviewable_reason_is_deterministic() -> None:
    assert no_reviewable_reason("Binary files a/x and b/x differ", {}) == "BINARY_ONLY"
    assert (
        no_reviewable_reason(
            "", {"package-lock.json": ChangedFile("package-lock.json", frozenset({1}))}
        )
        == "LOCKFILE_ONLY"
    )
    assert (
        no_reviewable_reason("", {"app.py": ChangedFile("app.py", frozenset())})
        == "NO_REVIEWABLE_CODE"
    )
