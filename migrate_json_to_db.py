#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.paths import BASE_DIR  # noqa: E402
from app.storage import JsonSQLiteStore  # noqa: E402


def find_json_files() -> list[Path]:
    files = [
        BASE_DIR / "accounts.json",
        BASE_DIR / "country_codes.json",
        BASE_DIR / "groups.json",
        BASE_DIR / "platforms.json",
        BASE_DIR / "ranges_store.json",
        BASE_DIR / "runtime_config.json",
        BASE_DIR / "sent_codes_store.json",
        BASE_DIR / "token_cache.json",
    ]
    daily_dir = BASE_DIR / "daily_messages"
    if daily_dir.exists():
        files.extend(sorted(daily_dir.glob("messages_*.json")))
    return [p for p in files if p.exists()]


def backup_files(files: list[Path]) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = BASE_DIR / "data" / "legacy_json_backup" / ts
    for src in files:
        rel = src.relative_to(BASE_DIR)
        dst = backup_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return backup_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy JSON files to SQLite DB")
    parser.add_argument("--delete-json", action="store_true", help="Delete legacy JSON files after backup")
    args = parser.parse_args()

    # Initialize store + built-in migration.
    JsonSQLiteStore()

    files = find_json_files()
    if not files:
        print("No JSON files found.")
        return 0

    backup_root = backup_files(files)
    print(f"Backed up {len(files)} JSON file(s) to: {backup_root}")

    if args.delete_json:
        for path in files:
            path.unlink(missing_ok=True)
        print("Deleted legacy JSON files.")
    else:
        print("Legacy JSON files kept (use --delete-json to remove).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
