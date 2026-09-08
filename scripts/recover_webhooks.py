"""Classify stale webhook deliveries; never replays a payload automatically."""

import argparse
import asyncio
import json

from app.db.session import get_session_factory
from app.github.webhook import recover_stale_deliveries


async def main(apply: bool, threshold: int) -> None:
    async with get_session_factory()() as session:
        report = await recover_stale_deliveries(session, threshold_seconds=threshold, apply=apply)
        print(json.dumps({"apply": apply, "count": len(report), "deliveries": report}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--threshold-seconds", type=int, default=900)
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.threshold_seconds))
