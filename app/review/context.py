"""Bounded, deterministic repository context; no network or source persistence."""

import re
from dataclasses import dataclass
from pathlib import Path

from app.review.diff import ignored, normalize_path

SOURCE_SUFFIXES = {
    ".py",
    ".java",
    ".kt",
    ".ts",
    ".tsx",
    ".go",
    ".xml",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".properties",
    ".sql",
    ".gradle",
    ".kts",
}
EXCLUDED_PARTS = {".git", ".venv", "node_modules", "vendor", "generated", "build", "dist"}


@dataclass(frozen=True)
class ContextBudget:
    max_files: int = 12
    per_file_bytes: int = 16_384
    total_bytes: int = 98_304
    scan_bytes: int = 2_000_000
    scan_files: int = 2_000
    depth: int = 1
    config_reserve: int = 2
    test_reserve: int = 2

    def __post_init__(self) -> None:
        if (
            min(
                self.max_files,
                self.per_file_bytes,
                self.total_bytes,
                self.scan_bytes,
                self.scan_files,
            )
            < 1
            or self.depth != 1
        ):
            raise ValueError("invalid context budget")
        if self.config_reserve + self.test_reserve > self.max_files:
            raise ValueError("context reserves exceed budget")


@dataclass(frozen=True)
class ContextFile:
    path: str
    role: str
    text: str


@dataclass(frozen=True)
class ContextPack:
    files: tuple[ContextFile, ...]
    truncated: bool
    reasons: tuple[str, ...]
    excluded_size: int

    def render(self) -> str:
        return "\n".join(
            f"<related-file path={item.path!r} role={item.role!r}>\n{item.text}\n</related-file>"
            for item in self.files
        )


def select_context(
    root: Path,
    changed: list[str],
    diff: str,
    signals: tuple[str, ...],
    patterns: list[str],
    budget: ContextBudget = ContextBudget(),
) -> ContextPack:
    root = root.resolve()
    changed_set = {normalize_path(path) for path in changed}
    identifiers = set(re.findall(r"\b[A-Z][A-Za-z0-9_]{3,}\b", diff))
    symbols = set(re.findall(r"\b([a-z][A-Za-z0-9_]{3,})\s*\(", diff))
    references = identifiers | (symbols - {"return", "equals", "append", "contains"})
    changed_names = {Path(path).stem for path in changed_set}
    candidates: list[ContextFile] = []
    scanned = 0
    count = 0
    excluded_size = 0
    reasons: set[str] = set()
    for candidate in sorted(root.rglob("*")):
        parts = candidate.relative_to(root).parts
        if any(part in EXCLUDED_PARTS for part in parts) or candidate.is_symlink():
            continue
        if not candidate.is_file() or candidate.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        path = normalize_path(candidate.relative_to(root).as_posix())
        if ignored(path, patterns) or path in changed_set:
            continue
        if not candidate.resolve().is_relative_to(root):
            continue
        count += 1
        size = candidate.stat().st_size
        if count > budget.scan_files or scanned + size > budget.scan_bytes:
            reasons.add("BYTE_BUDGET")
            break
        if size > budget.per_file_bytes:
            excluded_size += 1
            reasons.add("FILE_SIZE")
            continue
        scanned += size
        try:
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        if "\x00" in text:
            continue
        words = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text))
        direct = bool(words & references or words & changed_names)
        is_test = "test" in path.casefold() and bool(words & changed_names)
        is_config = candidate.suffix in {".xml", ".yml", ".yaml", ".properties", ".toml"}
        logging_config = (
            "LOGGING" in signals
            and is_config
            and bool(re.search(r"appender|logback|logging|encoder", text, re.I))
        )
        contract_config = is_config and direct
        if is_test:
            role = "TEST"
        elif logging_config or contract_config:
            role = "CONFIG"
        elif direct:
            role = "CALLER_CALLEE"
        else:
            continue
        candidates.append(ContextFile(path, role, text))
    reserved = [item for item in candidates if item.role == "CONFIG"][: budget.config_reserve] + [
        item for item in candidates if item.role == "TEST"
    ][: budget.test_reserve]
    ordered = reserved + [item for item in candidates if item not in reserved]
    selected: list[ContextFile] = []
    used = 0
    for item in ordered:
        if len(selected) >= budget.max_files:
            reasons.add("FILE_BUDGET")
            continue
        length = len(item.text.encode("utf-8"))
        if used + length > budget.total_bytes:
            reasons.add("BYTE_BUDGET")
            continue
        selected.append(item)
        used += length
    return ContextPack(tuple(selected), bool(reasons), tuple(sorted(reasons)), excluded_size)
