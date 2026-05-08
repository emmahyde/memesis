"""
Stage 1.5 schema migration: add temporal_scope, extraction_confidence, actor, polarity, revisable.

Usage:
    python scripts/migrate_stage15_fields.py [--db PATH]

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
    ("temporal_scope",        "TEXT"),
    ("extraction_confidence", "REAL"),
    ("actor",                 "TEXT"),
    ("polarity",              "TEXT"),
    ("revisable",             "TEXT DEFAULT '0'"),
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

    print("[migration] Stage 1.5 fields migration complete.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Stage 1.5 schema migration")
    parser.add_argument("--db", default=None, help="Path to SQLite DB file")
    args = parser.parse_args()
    run_migration(db_path=args.db)
