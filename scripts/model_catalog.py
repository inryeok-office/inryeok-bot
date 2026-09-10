"""Inspect and validate the operator-managed Codex catalog without model calls."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.review.model_catalog import load_catalog


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("inspect", "validate"))
    args = parser.parse_args()
    try:
        specs = load_catalog(get_settings())
        payload = {
            "valid": True,
            "count": len(specs),
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
                }
                for item in specs
            ],
        }
    except ValueError as exc:
        payload = {"valid": False, "error_code": "INVALID_MODEL_CATALOG", "message": str(exc)}
    print(json.dumps(payload, ensure_ascii=False))
    if args.command == "validate" and not payload["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
