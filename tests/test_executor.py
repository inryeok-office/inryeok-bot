import base64
import io
import tarfile

import pytest

from app.codex.executor import _extract_archive
from app.codex.executor_client import _archive_workspace
from app.codex.runner import CodexError


def _archive(name: str = "file.txt", content: bytes = b"ok") -> str:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def test_workspace_archive_excludes_git_and_symlinks(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("credential", encoding="utf-8")
    (tmp_path / "source.py").write_text("print('ok')", encoding="utf-8")
    payload = _archive_workspace(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        assert archive.getnames() == ["source.py"]


def test_executor_rejects_path_traversal(tmp_path) -> None:
    malicious = _archive("../escape.txt")
    encoded = base64.b64encode(malicious).decode()
    with pytest.raises(ValueError, match="unsafe archive path"):
        _extract_archive(encoded, tmp_path)


def test_workspace_archive_rejects_symlink(tmp_path) -> None:
    (tmp_path / "target").write_text("ok", encoding="utf-8")
    try:
        (tmp_path / "link").symlink_to(tmp_path / "target")
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(CodexError, match="symlink"):
        _archive_workspace(tmp_path)
