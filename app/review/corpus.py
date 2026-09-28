"""Offline corpus integrity and structured semantic defect comparison.

Identity annotations must be adjudicated from path, symbol, category and causal
chain together. Natural-language keywords alone never establish a true positive.
No model or GitHub calls are made by this module.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.review.diff import normalize_path, parse_unified_diff


@dataclass(frozen=True)
class DefectIdentity:
    path: str
    symbol: str
    category: str
    causal_chain: tuple[str, ...]

    def fingerprint(self) -> str:
        if not self.symbol or len(self.causal_chain) < 2 or not all(self.causal_chain):
            raise ValueError("identity requires symbol and causal chain")
        payload = [normalize_path(self.path), self.symbol, self.category, self.causal_chain]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def load_fixture(manifest_path: Path, diff_path: Path) -> dict[str, object]:
    manifest: dict[str, object] = json.loads(manifest_path.read_text(encoding="utf-8"))
    diff = diff_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if hashlib.sha256(diff.encode()).hexdigest() != manifest["diff_sha256"]:
        raise ValueError("fixture diff drift")
    files = parse_unified_diff(diff)
    if "changed_files" in manifest:
        paths = manifest["changed_files"]
        if not isinstance(paths, list) or sorted(files) != sorted(paths):
            raise ValueError("fixture file manifest drift")
    return manifest


def verify_remote_identity(manifest: dict[str, object], base: str, head: str) -> None:
    if (base, head) != (manifest["base_sha"], manifest["head_sha"]):
        raise ValueError("remote head drift; explicit historical SHA required")


def evaluate_identities(
    expected: set[DefectIdentity],
    allowed: set[DefectIdentity],
    rejected: set[DefectIdentity],
    candidates: list[DefectIdentity],
    publishable: list[DefectIdentity],
) -> dict[str, float | int | None]:
    if expected & allowed or expected & rejected or allowed & rejected:
        raise ValueError("corpus classifications overlap")
    for identity in expected | allowed | rejected | set(candidates) | set(publishable):
        identity.fingerprint()
    actual = set(candidates)
    accepted = set(publishable)
    if not accepted <= actual:
        raise ValueError("publishable identity absent from candidates")
    unsupported = actual - expected - allowed
    return {
        "expected_recall": len(actual & expected) / len(expected) if expected else None,
        "precision": len(actual & (expected | allowed)) / len(actual) if actual else None,
        "publishable_recall": len(accepted & expected) / len(expected) if expected else None,
        "unsupported": len(unsupported),
        "duplicate_count": len(candidates) - len(actual),
        "rejected_accepted": len(accepted & rejected),
        "process_count": 0,
        "usage": None,
        "latency": None,
    }
