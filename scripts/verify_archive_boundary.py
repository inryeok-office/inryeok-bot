"""Linux-capable symlink boundary smoke without credentials or model execution."""

import base64
import io
import tarfile
import tempfile
from pathlib import Path

from app.codex.executor import _extract_archive
from app.codex.executor_client import _archive_workspace
from app.codex.runner import CodexError
from app.review.context import select_context


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="review-boundary-") as temporary:
        root = Path(temporary)
        outside = root / "outside.java"
        outside.write_text("class Caller { Changed changed; }", encoding="utf-8")
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "Changed.java").write_text("class Changed {}", encoding="utf-8")
        (workspace / "link.java").symlink_to(outside)
        pack = select_context(workspace, ["Changed.java"], "+new Changed();", (), [])
        if pack.files:
            raise RuntimeError("symlink source leaked into context")
        try:
            _archive_workspace(workspace)
        except CodexError as error:
            if error.code != "EXECUTOR_UNSAFE_WORKSPACE":
                raise
        else:
            raise RuntimeError("symlink workspace was archived")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as stream:
            member = tarfile.TarInfo("link.java")
            member.type = tarfile.SYMTYPE
            member.linkname = "../outside.java"
            stream.addfile(member)
        destination = root / "unpack"
        destination.mkdir()
        try:
            _extract_archive(base64.b64encode(archive.getvalue()).decode(), destination)
        except ValueError:
            pass
        else:
            raise RuntimeError("symlink archive was unpacked")
        if list(destination.iterdir()):
            raise RuntimeError("rejected archive mutated destination")
    print("symlink context/archive boundaries passed; codex_processes=0")


if __name__ == "__main__":
    main()
