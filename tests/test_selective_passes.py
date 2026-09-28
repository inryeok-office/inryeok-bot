import os

import pytest
from sqlalchemy import select

from app.codex.executor_client import _archive_workspace
from app.codex.runner import CodexError, FakeRunner
from app.codex.schemas import Finding, ReviewOutput
from app.config import Settings
from app.jobs.models import (
    GlobalReviewSettings,
    RepositorySettings,
    ReviewJob,
    ReviewPass,
    TriggerType,
)
from app.review.deduplicator import fingerprint
from app.review.diff import ChangedFile
from app.review.passes import execute_passes, plan_passes
from app.review.settings import resolve
from app.review.validator import validate_findings_with_diagnostics


@pytest.mark.parametrize(
    "signals,expected",
    [
        ((), ("GENERAL",)),
        (("LOGGING",), ("GENERAL", "SECURITY_PRIVACY")),
        (("AWS", "CLOUDWATCH", "BATCHING"), ("GENERAL", "EXTERNAL_CONTRACT")),
        (("TRANSACTION", "REDIS"), ("GENERAL", "DATA_CONCURRENCY")),
    ],
)
def test_pass_selection(signals, expected):
    assert plan_passes(signals, True, 2) == expected
    assert plan_passes(signals, False, 3) == ("GENERAL",)
    assert plan_passes(signals, True, 1) == ("GENERAL",)


def test_complex_risks_never_exceed_three_passes():
    signals = (
        "LOGGING",
        "SECURITY",
        "PRIVACY",
        "AWS",
        "CLOUDWATCH",
        "BATCHING",
        "TRANSACTION",
        "REDIS",
    )
    assert len(plan_passes(signals, True, 3)) == 3


def test_semantic_dedup_keeps_strongest_and_distinct_mechanisms():
    values = dict(
        scope="FILE",
        path="A.java",
        category="SECURITY",
        severity="HIGH",
        confidence=0.92,
        title="secret exposed",
        body="new logs expose a credential",
        condition="exception happens",
        impact="credential exposure",
        evidence="new logging path",
        introduced_by_pr=True,
        relation_to_change="DIRECT_CHANGE",
        changed_symbol="send",
        causal_evidence="send now adds throwable data causing exposed credentials",
        changed_file_anchor={"kind": "ADDED_LINE", "path": "A.java", "line": 2},
        defect_identity="webhook_token_exposure",
        causal_chain=["throwable_payload", "missing_url_mask", "credential_exposure"],
    )
    first = Finding(**values)
    stronger = first.model_copy(
        update={"title": "different phrasing", "body": "same defect", "confidence": 0.97}
    )
    distinct = first.model_copy(
        update={
            "defect_identity": "sql_pii_exposure",
            "causal_chain": ["sql_failure", "raw_value", "pii_leak"],
        }
    )
    assert fingerprint(first) == fingerprint(stronger)
    assert fingerprint(first) != fingerprint(distinct)
    result = validate_findings_with_diagnostics(
        [first, stronger, distinct],
        {"A.java": ChangedFile("A.java", frozenset({2}))},
        0.9,
        False,
        10,
    )
    assert result.findings == [stronger, distinct]
    assert result.rejection_counts == {"DUPLICATE": 1}


def test_archive_is_stable_across_mtime_and_workspace_paths(tmp_path):
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        (root / "A.py").write_text("x = 1\n", encoding="utf-8")
    os.utime(tmp_path / "two/A.py", (1, 1))
    assert _archive_workspace(tmp_path / "one") == _archive_workspace(tmp_path / "two")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,fail_at,partial",
    [
        ("CODEX_OUTPUT_SCHEMA_MISMATCH", 2, True),
        ("CODEX_OUTPUT_SCHEMA_MISMATCH", 1, False),
        ("EXECUTOR_UNKNOWN_OUTCOME", 2, False),
        ("CODEX_AUTH", 2, False),
    ],
)
async def test_failure_policy_and_durable_pass_records(
    app_client, tmp_path, failure, fail_at, partial
):
    _, factory = app_client

    class Runner(FakeRunner):
        calls = 0

        async def run(self, *args, **kwargs):
            self.calls += 1
            if self.calls == fail_at:
                raise CodexError(failure, "safe failure")
            return self.output

    async with factory() as session:
        global_settings = GlobalReviewSettings(id=1)
        repo = RepositorySettings(
            installation_id=1, repository_owner="acme", repository_name="repo"
        )
        session.add_all([global_settings, repo])
        job = ReviewJob(
            delivery_id="passes",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.AUTO,
        )
        session.add(job)
        await session.commit()
        settings = Settings(environment="test", selective_multi_pass=True)
        effective = resolve(global_settings, repo, settings)
        runner = Runner(ReviewOutput(summary="general succeeded", findings=[]))
        args = (
            session,
            job,
            settings,
            effective,
            runner,
            tmp_path,
            "prompt",
            ("LOGGING",),
            "e" * 32,
        )
        if partial:
            result = await execute_passes(*args)
            assert result.partial and result.summary == "general succeeded"
        else:
            with pytest.raises(CodexError, match="safe failure"):
                await execute_passes(*args)
        records = (await session.scalars(select(ReviewPass).order_by(ReviewPass.id))).all()
        assert len(records) == fail_at
        assert len({record.execution_id for record in records}) == fail_at
        assert len({record.fingerprint for record in records}) == fail_at
        calls = runner.calls
        with pytest.raises(CodexError, match="automatically repeated"):
            await execute_passes(*args)
        # A successful GENERAL may load its durable result again, but the failed
        # perspective is never invoked again. FakeRunner represents that lookup.
        assert runner.calls <= calls + int(fail_at == 2)


@pytest.mark.asyncio
async def test_process_budget_skips_extra_and_records_partial(app_client, tmp_path):
    _, factory = app_client
    async with factory() as session:
        global_settings = GlobalReviewSettings(id=1)
        repo = RepositorySettings(
            installation_id=1, repository_owner="acme", repository_name="repo"
        )
        job = ReviewJob(
            delivery_id="budget",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.AUTO,
        )
        session.add_all([global_settings, repo, job])
        await session.commit()
        settings = Settings(
            environment="test", selective_multi_pass=True, review_processes_per_hour=1
        )
        result = await execute_passes(
            session,
            job,
            settings,
            resolve(global_settings, repo, settings),
            FakeRunner(ReviewOutput(summary="ok", findings=[])),
            tmp_path,
            "prompt",
            ("LOGGING",),
            "f" * 32,
        )
        assert result.partial
        records = (await session.scalars(select(ReviewPass).order_by(ReviewPass.id))).all()
        assert [record.state for record in records] == ["SUCCEEDED", "SKIPPED"]
        assert records[-1].reason == "PROCESS_BUDGET"
