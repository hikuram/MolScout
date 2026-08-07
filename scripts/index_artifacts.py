#!/usr/bin/env python3
"""Build or refresh the PostgreSQL artifact catalog from the data directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
for entry in (str(APP_DIR), str(PROJECT_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app_core.artifact_manager import scan_all_artifacts
from app_core.database import ensure_database
from app_core.paths import DATA_DIR, ensure_app_dirs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register selected MolScout files in the PostgreSQL artifact catalog."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count eligible files without changing PostgreSQL.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_app_dirs()
    ensure_database()
    summary = scan_all_artifacts(source="index_script", dry_run=args.dry_run)
    print(f"Data directory: {DATA_DIR}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
