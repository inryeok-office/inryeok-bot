from pathlib import Path

import pytest

from app.review.context import ContextBudget, select_context
from app.review.risks import detect_risks


def test_pr190_risk_signals():
    diff = Path("tests/fixtures/pr190_initial.diff").read_text(encoding="utf-8")
    signals = detect_risks(diff)
    assert {"LOGGING", "PRIVACY", "AWS", "CLOUDWATCH", "EXCEPTION_HANDLING"} <= set(signals)
    assert not detect_risks("diff --git a/CloudWatch.java b/CloudWatch.java")
    assert not detect_risks("-import software.amazon.awssdk;\n-client.request()")


def test_context_selects_callers_configs_tests_and_excludes_unrelated(tmp_path):
    files = {
        "CloudWatchAppender.java": "class CloudWatchAppender { void formatMessage() {} }",
        "logback-spring.xml": '<appender class="CloudWatchAppender"><encoder/></appender>',
        "Caller.java": "CloudWatchAppender.formatMessage();",
        "CloudWatchAppenderTest.java": "new CloudWatchAppender();",
        "Other.java": "class Other {}",
        "vendor/Bad.java": "new CloudWatchAppender();",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    pack = select_context(
        tmp_path, ["CloudWatchAppender.java"], "+new CloudWatchAppender();", ("LOGGING",), []
    )
    assert {item.path for item in pack.files} == {
        "logback-spring.xml",
        "Caller.java",
        "CloudWatchAppenderTest.java",
    }
    bounded = ContextBudget(max_files=2, config_reserve=1, test_reserve=1)
    first = select_context(
        tmp_path,
        ["CloudWatchAppender.java"],
        "+new CloudWatchAppender();",
        ("LOGGING",),
        [],
        bounded,
    )
    assert first == select_context(
        tmp_path,
        ["CloudWatchAppender.java"],
        "+new CloudWatchAppender();",
        ("LOGGING",),
        [],
        bounded,
    )
    assert first.truncated and "FILE_BUDGET" in first.reasons
    assert [item.role for item in first.files] == ["CONFIG", "TEST"]


def test_unsafe_changed_path_and_invalid_budget(tmp_path):
    with pytest.raises(ValueError):
        select_context(tmp_path, ["C:\\secret"], "", (), [])
    with pytest.raises(ValueError):
        ContextBudget(depth=2)
