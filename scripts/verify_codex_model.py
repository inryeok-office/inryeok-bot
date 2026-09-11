"""Run one safe model verification request through the production executor socket.

This fixture never calls GitHub and never prints the executor response body.
It is intended for one-off operator-approved verification from the worker
container, not for normal review jobs.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import tarfile
import tempfile
from pathlib import Path

import httpx

FIXTURE_PROMPT = (
    "Return a valid review result for this verification fixture. "
    "The fixture has no review finding; return findings=[] and a short summary."
)


def _archive() -> str:
    with tempfile.TemporaryDirectory(prefix="model-verification-") as name:
        root = Path(name)
        (root / ".git").mkdir()
        (root / "changed.txt").write_text("safe verification fixture\n", encoding="utf-8")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(root.rglob("*")):
                archive.add(path, arcname=path.relative_to(root))
        return base64.b64encode(buffer.getvalue()).decode("ascii")


async def _verify(socket: str, model: str | None, effort: str, execution_id: str) -> int:
    payload = {
        "archive": _archive(),
        "prompt": FIXTURE_PROMPT,
        "model": model,
        "reasoning_effort": effort,
        "timeout": 300,
        "execution_id": execution_id,
    }
    transport = httpx.AsyncHTTPTransport(uds=socket)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://executor", timeout=360
    ) as client:
        response = await client.post("/review", json=payload)
    result: object
    try:
        result = response.json()
    except (ValueError, TypeError):
        result = {}
    if response.status_code != 200 or not isinstance(result, dict):
        print(
            json.dumps(
                {
                    "success": False,
                    "http_status": response.status_code,
                    "model": model,
                    "reasoning_effort": effort,
                    "execution_id": execution_id,
                    "structured_output": False,
                    "github_write": 0,
                },
                sort_keys=True,
            )
        )
        return 1
    valid = isinstance(result.get("summary"), str) and result.get("findings") == []
    print(
        json.dumps(
            {
                "success": valid,
                "http_status": response.status_code,
                "model": model,
                "reasoning_effort": effort,
                "execution_id": execution_id,
                "structured_output": valid,
                "github_write": 0,
                "fixture_workspace": "temporary_deleted",
            },
            sort_keys=True,
        )
    )
    return 0 if valid else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/inryeok-bot/executor.sock")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default="medium", choices=("default", "low", "medium", "high"))
    parser.add_argument("--execution-id", required=True)
    args = parser.parse_args()
    if (
        not args.execution_id.isascii()
        or not args.execution_id.replace("-", "").replace("_", "").isalnum()
    ):
        parser.error("execution id must be filename-safe ASCII")
    raise SystemExit(asyncio.run(_verify(args.socket, args.model, args.effort, args.execution_id)))


if __name__ == "__main__":
    main()
