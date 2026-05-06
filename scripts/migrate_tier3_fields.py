"""
Tier-3 audit Wave A schema migration: add criterion_weights, rejected_options.

Usage:
    python scripts/migrate_tier3_fields.py [--db PATH]

Idempotent: skips columns that already exist.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db
from core.models import db


def _table_columns(table: str) -> set[str]:
    cursor = db.execute_sql(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


NEW_COLUMNS = [
    ("criterion_weights", "TEXT"),
    ("rejected_options",  "TEXT"),
]


def run_migration(db_path: str | None = None) -> None:
    if db_path:
        init_db(base_dir=str(Path(db_path).parent))
    else:
        init_db()

    existing_cols = _table_columns("memories")
    for col, typ in NEW_COLUMNS:
        if col not in existing_cols:
            try:
                db.execute_sql(f"ALTER TABLE memories ADD COLUMN {col} {typ}")
                print(f"  [migration] Added column: memories.{col}")
            except Exception as exc:
                print(f"  [migration] Could not add {col}: {exc}")
        else:
            print(f"  [migration] Already exists, skipping: memories.{col}")

    print("[migration] Tier-3 audit Wave A fields migration complete.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tier-3 audit Wave A schema migration")
    parser.add_argument("--db", default=None, help="Path to SQLite DB file")
    args = parser.parse_args()
    run_migration(db_path=args.db)
