"""Credential-free schema/template preflight; never starts Codex or reads .env."""

import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.codex.runner import CodexRunner
from app.config import Settings
from app.review.domains import PROMPT_VERSION


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    schema = root / "review-schema.json"
    json.loads(schema.read_text(encoding="utf-8"))
    CodexRunner(Settings(_env_file=None, environment="test"), schema)._validate_schema_definition()
    templates = root / "app/admin/templates"
    environment = Environment(loader=FileSystemLoader(templates), autoescape=True)
    for path in sorted(templates.glob("*.html")):
        environment.parse(path.read_text(encoding="utf-8"))
    print(f"schema_sha256={hashlib.sha256(schema.read_bytes()).hexdigest()}")
    print(f"prompt_version={PROMPT_VERSION}")
    print(f"prompt_sha256={hashlib.sha256((root / 'prompts/review.md').read_bytes()).hexdigest()}")
    print(f"templates_compiled={len(list(templates.glob('*.html')))}")
    print("codex_processes=0")


if __name__ == "__main__":
    main()
