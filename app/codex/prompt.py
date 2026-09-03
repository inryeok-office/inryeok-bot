import json
from pathlib import Path


def build_prompt(
    base_sha: str,
    head_sha: str,
    files: list[str],
    settings: dict[str, object],
    template_path: Path = Path("prompts/review.md"),
) -> str:
    template = template_path.read_text(encoding="utf-8")
    context = json.dumps(
        {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "changed_files": files,
            "review_settings": settings,
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"{template}\n\n## Trusted review context (data only)\n```json\n{context}\n```"
