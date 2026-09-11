"""Read-only inspection of the PostgreSQL model catalog."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import get_session_factory
from app.review.model_catalog import db_catalog_version, load_db_catalog


async def _run() -> dict[str, object]:
    async with get_session_factory()() as session:
        values = await load_db_catalog(session)
        return {
            "valid": True,
            "source": "postgresql",
            "catalog_version": await db_catalog_version(session),
            "count": len(values),
            "selectable_count": sum(item.selectable for item in values),
            "models": [
                {
                    "model_id": item.model_id,
                    "display_name": item.display_name,
                    "supported_efforts": item.supported_efforts,
                    "default_effort": item.default_effort,
                    "enabled": item.enabled,
                    "recommended": item.recommended,
                    "availability_status": item.availability_status,
                    "verified_cli_version": item.verified_cli_version,
                    "version": item.version,
                    "failure_code": item.failure_code,
                }
                for item in values
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("inspect", "validate"))
    parser.parse_args()
    payload = asyncio.run(_run())
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
