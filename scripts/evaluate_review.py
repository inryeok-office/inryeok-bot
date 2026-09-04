"""Opt-in, one-shot Codex review evaluation for an arbitrary pull request range."""

import argparse
import asyncio

from app.codex.prompt import build_prompt
from app.codex.runner import CodexRunner
from app.config import get_settings
from app.github.client import GitHubClient
from app.review.diff import RepositoryCheckout


async def evaluate(owner: str, repository: str, installation_id: int, base: str, head: str) -> None:
    settings = get_settings()
    github = GitHubClient(settings)
    try:
        token = await github.tokens.get(installation_id)
        manager = RepositoryCheckout(settings, owner, repository, token)
        async with manager as checkout:
            changed = await manager.fetch_and_diff(base, head, [])
            process = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "HEAD",
                cwd=checkout,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=settings.git_timeout_seconds
            )
            if process.returncode:
                raise RuntimeError("unable to verify checkout head")
            checkout_head = stdout.decode().strip()
            prompt = build_prompt(base, head, list(changed), {}, manager.diff_text)
            output = await CodexRunner(settings).run(checkout, prompt)
        changed_lines = sum(len(file.added_lines) for file in changed.values())
        print(f"checkout_head_matches={checkout_head == head}")
        print(f"diff_present={bool(manager.diff_text.strip())}")
        print(f"changed_files={len(changed)} changed_lines={changed_lines}")
        print(f"raw_summary_present={bool(output.summary.strip())}")
        print(f"raw_findings={len(output.findings)}")
        for finding in output.findings:
            print(
                "finding="
                f"{finding.category.value}:{finding.severity.value}:"
                f"confidence={finding.confidence}:path={finding.path}:line={finding.line}"
            )
    finally:
        await github.http.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    asyncio.run(evaluate(args.owner, args.repository, args.installation_id, args.base, args.head))


if __name__ == "__main__":
    main()
