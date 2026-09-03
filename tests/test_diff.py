import base64

import pytest

from app.review.diff import _git_authorization_header, ignored, normalize_path, parse_unified_diff


def test_git_authorization_header_uses_github_app_basic_auth() -> None:
    credential = "example-installation-credential"
    expected = base64.b64encode(f"x-access-token:{credential}".encode()).decode("ascii")
    assert _git_authorization_header(credential) == f"AUTHORIZATION: basic {expected}"


def test_added_lines_rename_and_deletions():
    diff = """diff --git a/old.py b/new.py
similarity index 80%
rename from old.py
rename to new.py
@@ -1,2 +1,2 @@
-old
+new
 same
diff --git a/gone.py b/gone.py
@@ -5,1 +5,0 @@
-deleted
"""
    files = parse_unified_diff(diff)
    assert files["new.py"].added_lines == {1}
    assert files["gone.py"].added_lines == set()


def test_binary_and_ignore_patterns():
    diff = "diff --git a/a.png b/a.png\nBinary files a/a.png and b/a.png differ\n"
    assert parse_unified_diff(diff) == {}
    assert ignored("vendor/a.py", ["vendor/**"])


def test_deleted_file_has_no_inline_lines() -> None:
    diff = "diff --git a/gone.py b/gone.py\n@@ -1 +0,0 @@\n-old\n"
    assert parse_unified_diff(diff)["gone.py"].added_lines == set()


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../../x", "x\x00y"])
def test_path_traversal_rejected(path):
    with pytest.raises(ValueError):
        normalize_path(path)
