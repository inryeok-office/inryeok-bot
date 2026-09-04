import asyncio
import base64
import fnmatch
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.config import Settings


class DiffError(RuntimeError):
    pass


def _git_authorization_header(token: str) -> str:
    credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    return f"AUTHORIZATION: basic {credentials}"


def normalize_path(value: str) -> str:
    value = value.replace("\\", "/").removeprefix("a/").removeprefix("b/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise ValueError("unsafe repository path")
    return path.as_posix()


def ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern.strip()) for pattern in patterns if pattern.strip())


@dataclass(frozen=True)
class ChangedFile:
    path: str
    added_lines: frozenset[int]


DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(
    text: str, ignore_patterns: list[str] | None = None
) -> dict[str, ChangedFile]:
    patterns = ignore_patterns or []
    result: dict[str, ChangedFile] = {}
    path: str | None = None
    lines: set[int] = set()
    new_line: int | None = None
    binary = False

    def save() -> None:
        if path and not binary and not ignored(path, patterns):
            result[path] = ChangedFile(path, frozenset(lines))

    for raw in text.splitlines():
        header = DIFF_HEADER.match(raw)
        if header:
            save()
            path = normalize_path(header.group(2))
            lines, new_line, binary = set(), None, False
            continue
        if raw.startswith("rename to "):
            path = normalize_path(raw[10:])
            continue
        if raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            binary = True
            continue
        hunk = HUNK.match(raw)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if new_line is None or raw.startswith(("+++", "---")):
            continue
        if raw.startswith("+"):
            lines.add(new_line)
            new_line += 1
        elif raw.startswith("-"):
            pass
        elif raw.startswith("\\ No newline"):
            pass
        else:
            new_line += 1
    save()
    return result


async def _git(
    args: list[str],
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
    max_bytes: int = 5_000_000,
) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise DiffError("git command timed out") from exc
    if len(stdout) > max_bytes or len(stderr) > 100_000:
        raise DiffError("git output exceeded configured limit")
    if process.returncode:
        raise DiffError(f"git command failed ({process.returncode})")
    return stdout.decode("utf-8", errors="replace")


class RepositoryCheckout:
    def __init__(self, settings: Settings, owner: str, repo: str, token: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", repo
        ):
            raise DiffError("invalid repository identifier")
        self.settings, self.owner, self.repo, self.token = settings, owner, repo, token
        self.path: Path | None = None
        self.diff_text = ""

    async def __aenter__(self) -> Path:
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        try:
            if shutil.disk_usage(self.settings.work_root).free < self.settings.min_work_free_bytes:
                raise DiffError("review workspace has insufficient free space")
        except OSError as exc:
            raise DiffError("unable to inspect review workspace capacity") from exc
        self.path = Path(tempfile.mkdtemp(prefix="review-", dir=self.settings.work_root)).resolve()
        await _git(["init"], self.path, self.settings.git_timeout_seconds)
        await _git(
            [
                "remote",
                "add",
                "origin",
                f"{self.settings.github_clone_base_url}/{self.owner}/{self.repo}.git",
            ],
            self.path,
            self.settings.git_timeout_seconds,
        )
        return self.path

    async def fetch_and_diff(
        self, base: str, head: str, patterns: list[str]
    ) -> dict[str, ChangedFile]:
        assert self.path
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base) or not re.fullmatch(
            r"[0-9a-fA-F]{40,64}", head
        ):
            raise DiffError("invalid commit SHA")
        env = os.environ.copy()
        env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": (f"http.{self.settings.github_clone_base_url}/.extraheader"),
                "GIT_CONFIG_VALUE_0": _git_authorization_header(self.token),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
            }
        )
        await _git(
            ["fetch", "--no-tags", "--filter=blob:none", "origin", base, head],
            self.path,
            self.settings.git_timeout_seconds,
            env,
        )
        await _git(
            ["checkout", "--detach", head],
            self.path,
            self.settings.git_timeout_seconds,
            env,
        )
        for candidate in self.path.rglob("*"):
            if candidate.is_symlink() and not candidate.resolve().is_relative_to(self.path):
                raise DiffError("checkout contains a symlink escaping the work directory")
        output = await _git(
            ["diff", "--no-ext-diff", "--unified=0", "--find-renames", f"{base}...{head}", "--"],
            self.path,
            self.settings.git_timeout_seconds,
            env=env,
            max_bytes=self.settings.max_diff_bytes,
        )
        self.diff_text = output
        files = parse_unified_diff(output, patterns)
        if len(files) > self.settings.max_changed_files:
            raise DiffError("changed file limit exceeded")
        for name in files:
            raw_candidate = self.path / name
            if not raw_candidate.exists() and not files[name].added_lines:
                continue
            candidate = raw_candidate.resolve()
            if (
                raw_candidate.is_symlink()
                or not candidate.is_relative_to(self.path)
                or not candidate.is_file()
            ):
                raise DiffError("unsafe changed file")
            if candidate.stat().st_size > self.settings.max_file_bytes:
                raise DiffError("changed file size limit exceeded")
        return files

    async def __aexit__(self, *_: object) -> None:
        if self.path:
            shutil.rmtree(self.path, ignore_errors=True)
