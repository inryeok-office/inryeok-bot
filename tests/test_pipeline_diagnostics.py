import pytest
from pydantic import ValidationError

from app.review.diagnostics import ContextManifest, StageCounts


def test_counts_conserve_and_never_increase():
    values = dict(
        raw=2,
        schema_valid=2,
        scope_valid=1,
        evidence_valid=1,
        deduplicated=1,
        ranked=1,
        publishable=1,
        published=0,
        rejected=1,
    )
    assert StageCounts(**values).published == 0
    with pytest.raises(ValidationError):
        StageCounts(**{**values, "published": 2})
    with pytest.raises(ValidationError):
        StageCounts(**{**values, "rejected": 0})


@pytest.mark.parametrize("path", ["C:/secret", "../secret", "/secret", "x/../../secret"])
def test_manifest_rejects_unsafe_paths(path):
    with pytest.raises(ValidationError):
        ContextManifest(
            changed_files=1,
            prompt_files=1,
            archive_files=1,
            ignored_files=0,
            binary_files=0,
            diff_bytes=1,
            prompt_diff_bytes=1,
            related_paths=[path],
        )


def test_manifest_rejects_content_and_inverted_counts():
    values = dict(
        changed_files=1,
        prompt_files=1,
        archive_files=1,
        ignored_files=0,
        binary_files=0,
        diff_bytes=1,
        prompt_diff_bytes=1,
    )
    with pytest.raises(ValidationError):
        ContextManifest(**values, source="secret")
    with pytest.raises(ValidationError):
        ContextManifest(**{**values, "prompt_files": 2})
