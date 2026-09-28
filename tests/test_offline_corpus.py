from pathlib import Path

import pytest

from app.review.corpus import (
    DefectIdentity,
    evaluate_identities,
    load_fixture,
    verify_remote_identity,
)

ROOT = Path("tests/fixtures")


@pytest.mark.parametrize("pr", [171, 190, 348])
def test_pinned_fixture_hash(pr):
    suffix = "_initial" if pr != 348 else ""
    manifest = load_fixture(ROOT / f"pr{pr}{suffix}_manifest.json", ROOT / f"pr{pr}{suffix}.diff")
    verify_remote_identity(manifest, manifest["base_sha"], manifest["head_sha"])
    with pytest.raises(ValueError, match="drift"):
        verify_remote_identity(manifest, manifest["base_sha"], "f" * 40)


def test_initial_and_follow_up_are_separate():
    manifest = load_fixture(ROOT / "pr171_initial_manifest.json", ROOT / "pr171_initial.diff")
    assert not manifest["expected"]
    assert all(not item["initial_recall_target"] for item in manifest["follow_up"])
    manifest = load_fixture(ROOT / "pr190_initial_manifest.json", ROOT / "pr190_initial.diff")
    assert len(manifest["expected"]) == 5
    assert all(item["initial_head_present"] for item in manifest["expected"])


def test_semantic_identity_requires_full_causal_contract():
    identity = DefectIdentity("src/A.java", "send", "API_CONTRACT", ("byte overflow", "discard"))
    other = DefectIdentity("src/A.java", "send", "API_CONTRACT", ("retry", "duplicate"))
    assert identity.fingerprint() != other.fingerprint()
    assert (
        identity.fingerprint()
        == DefectIdentity(
            "src\\A.java", "send", "API_CONTRACT", identity.causal_chain
        ).fingerprint()
    )
    metrics = evaluate_identities(
        {identity}, set(), {other}, [identity, identity, other], [identity]
    )
    assert metrics["expected_recall"] == 1
    assert metrics["unsupported"] == 1
    assert metrics["duplicate_count"] == 1
    assert metrics["rejected_accepted"] == 0
    with pytest.raises(ValueError):
        DefectIdentity("src/A.java", "", "SECURITY", ("secret",)).fingerprint()
