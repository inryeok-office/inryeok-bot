import base64
import io
import tarfile

import pytest

from app.codex.executor import ReviewRequest, _extract_archive, _run_review
from app.codex.executor_client import ExecutorRunner, _archive_workspace
from app.codex.runner import CodexError
from app.codex.schemas import ReviewOutput


@pytest.mark.asyncio
async def test_executor_runner_supports_unix_socket(monkeypatch, tmp_path) -> None:
    (tmp_path / "source.py").write_text("value = 1", encoding="utf-8")
    seen: dict[str, object] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"summary": "ok", "findings": []}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, path: str, **kwargs: object) -> Response:
            assert path == "/review"
            assert "json" in kwargs
            return Response()

    monkeypatch.setattr(
        "app.codex.executor_client.httpx.AsyncHTTPTransport", lambda **kwargs: kwargs
    )
    monkeypatch.setattr("app.codex.executor_client.httpx.AsyncClient", Client)
    output = await ExecutorRunner("unix:///run/inryeok-bot/executor.sock").run(tmp_path, "review")
    assert output.summary == "ok"
    assert seen["base_url"] == "http://executor"
    assert seen["transport"] == {"uds": "/run/inryeok-bot/executor.sock"}


@pytest.mark.asyncio
async def test_executor_runner_preserves_safe_error_category(monkeypatch, tmp_path) -> None:
    class Response:
        status_code = 503

        def json(self) -> dict[str, object]:
            return {
                "error_code": "CODEX_SERVICE_UNAVAILABLE",
                "retryable": True,
                "correlation_id": "opaque-id",
                "error": "codex execution failed",
            }

    class Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, path: str, **kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr("app.codex.executor_client.httpx.AsyncClient", Client)
    with pytest.raises(CodexError) as raised:
        await ExecutorRunner("http://executor").run(tmp_path, "review")
    assert raised.value.code == "CODEX_SERVICE_UNAVAILABLE"
    assert raised.value.retryable
    assert "opaque-id" not in str(raised.value)


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


@pytest.mark.asyncio
async def test_executor_settings_do_not_read_dotenv(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class SettingsStub:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

    class RunnerStub:
        def __init__(self, settings: object) -> None:
            assert isinstance(settings, SettingsStub)

        async def run(self, *args: object) -> ReviewOutput:
            return ReviewOutput(summary="ok", findings=[])

    monkeypatch.setattr("app.codex.executor.Settings", SettingsStub)
    monkeypatch.setattr("app.codex.executor.CodexRunner", RunnerStub)
    request = ReviewRequest(archive=base64.b64encode(_archive()).decode(), prompt="review")

    output = await _run_review(request)

    assert output == {"summary": "ok", "findings": []}
    assert seen["_env_file"] is None
