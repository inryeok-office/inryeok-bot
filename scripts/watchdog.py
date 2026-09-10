"""Run the conservative unattended-state watchdog (dry-run by default)."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import get_session_factory
from app.ops.watchdog import inspect_and_recover


async def main(apply: bool) -> None:
    async with get_session_factory()() as session:
        print(json.dumps(await inspect_and_recover(session, get_settings(), apply=apply)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
