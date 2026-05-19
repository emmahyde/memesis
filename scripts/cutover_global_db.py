#!/usr/bin/env python3
"""One-time cutover: relocate the per-project memesis store to the global location.

memesis moved from a per-project database path to a single global database.
This script relocates the live store from

    ~/.claude/projects/-Users-emmahyde-projects-memesis/memory/

to the canonical global location

    ~/.claude/memory/

It moves the SQLite database (index.db + WAL sidecars) AND the markdown memory
tree (MEMORY.md, consolidated/, ephemeral/, instinctive/, meta/, feedback_*.md),
since base_dir roots both. The stale contents already at the destination are
backed up and replaced.

SAFETY:
  - Refuses to run while the memesis launchd crons are loaded.
  - Refuses if another process holds the source database open.
  - Takes a full tar backup of both source and destination before moving.
  - Idempotent: exits 0 with "already migrated" if the cutover is already done.

Usage:
    # 1. unload the crons first (this script verifies they are down)
    launchctl unload ~/Library/LaunchAgents/com.emmahyde.memesis.consolidate-cron.plist
    launchctl unload ~/Library/LaunchAgents/com.emmahyde.memesis.transcript-cron.plist

    uv run python scripts/cutover_global_db.py --dry-run   # inspect
    uv run python scripts/cutover_global_db.py             # execute

    # then reload the crons
"""

import argparse
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

SOURCE = Path.home() / ".claude" / "projects" / "-Users-emmahyde-projects-memesis" / "memory"
DEST = Path.home() / ".claude" / "memory"

# Contents of the source memory dir that constitute live state and must move.
# Stale index.db.bak.* and memesis_state_bak_*.tar.gz are deliberately left behind.
DB_FILES = ("index.db", "index.db-wal", "index.db-shm")
MARKDOWN_DIRS = ("consolidated", "ephemeral", "instinctive", "meta")

CRON_LABELS = (
    "com.emmahyde.memesis.consolidate-cron",
    "com.emmahyde.memesis.transcript-cron",
)


def _fail(msg: str) -> None:
    print(f"ABORT: {msg}", file=sys.stderr)
    sys.exit(1)


def _crons_loaded() -> list[str]:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    return [label for label in CRON_LABELS if label in out]


def _db_in_use(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    res = subprocess.run(["lsof", str(db_path)], capture_output=True, text=True)
    return res.returncode == 0 and bool(res.stdout.strip())


def _already_migrated() -> bool:
    dest_db = DEST / "index.db"
    src_renamed = not SOURCE.exists()
    return src_renamed and dest_db.exists() and dest_db.stat().st_size > 1_000_000


def cutover(dry_run: bool) -> int:
    if _already_migrated():
        print("already migrated — destination populated and source renamed; nothing to do")
        return 0

    src_db = SOURCE / "index.db"
    if not src_db.exists() or src_db.stat().st_size < 1_000_000:
        _fail(f"source DB missing or suspiciously small: {src_db}")

    loaded = _crons_loaded()
    if loaded:
        _fail(
            "memesis crons still loaded: "
            + ", ".join(loaded)
            + "\n  Run: launchctl unload ~/Library/LaunchAgents/<label>.plist for each."
        )

    if _db_in_use(src_db):
        _fail(f"another process holds {src_db} open — close it before cutover")

    # Capture the pre-move row count for post-move verification.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.database import init_db, db, close_db

    init_db(base_dir=str(SOURCE))
    pre_memories = db.execute_sql("SELECT COUNT(*) FROM memories").fetchone()[0]
    pre_observations = db.execute_sql("SELECT COUNT(*) FROM observations").fetchone()[0]
    print(f"source: {pre_memories} memories, {pre_observations} observations")
    if not dry_run:
        db.execute_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    close_db()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path.home() / ".claude" / f"memesis-cutover-backup-{ts}.tar.gz"
    print(f"backup -> {backup}")
    print(f"move   -> {SOURCE}  =>  {DEST}")
    print(f"rename -> {SOURCE}  =>  {SOURCE.parent / ('memory.migrated-' + ts)}")

    if dry_run:
        print("\ndry-run: no changes made")
        return 0

    # 1. Full backup of both source and destination.
    with tarfile.open(backup, "w:gz") as tar:
        tar.add(SOURCE, arcname=f"source-memory-{ts}")
        if DEST.exists():
            tar.add(DEST, arcname=f"dest-memory-{ts}")
    print(f"  backup written ({backup.stat().st_size // 1024} KB)")

    # 2. Clear stale destination state (test-polluted index.db + dirs).
    DEST.mkdir(parents=True, exist_ok=True)
    for name in DB_FILES:
        (DEST / name).unlink(missing_ok=True)
    for name in MARKDOWN_DIRS:
        if (DEST / name).exists():
            shutil.rmtree(DEST / name)
    for stale in DEST.glob("MEMORY.md"):
        stale.unlink()

    # 3. Move DB files.
    for name in DB_FILES:
        src = SOURCE / name
        if src.exists():
            shutil.move(str(src), str(DEST / name))
            print(f"  moved {name}")

    # 4. Move markdown tree.
    for name in MARKDOWN_DIRS:
        src = SOURCE / name
        if src.exists():
            shutil.move(str(src), str(DEST / name))
            print(f"  moved {name}/")
    for md in list(SOURCE.glob("MEMORY.md")) + list(SOURCE.glob("feedback_*.md")) \
            + list(SOURCE.glob("system_*.md")):
        shutil.move(str(md), str(DEST / md.name))
        print(f"  moved {md.name}")

    # 5. Run migrations against the relocated DB at its new path.
    init_db()  # no args -> global ~/.claude/memory
    post_memories = db.execute_sql("SELECT COUNT(*) FROM memories").fetchone()[0]
    post_observations = db.execute_sql("SELECT COUNT(*) FROM observations").fetchone()[0]
    null_proj = db.execute_sql("SELECT COUNT(*) FROM memories WHERE project IS NULL").fetchone()[0]
    close_db()

    # 6. Verify.
    if post_memories != pre_memories:
        _fail(f"memory count mismatch: {pre_memories} -> {post_memories} (restore from {backup})")
    if post_observations != pre_observations:
        _fail(f"observation count mismatch: {pre_observations} -> {post_observations} (restore from {backup})")
    print(f"verified: {post_memories} memories, {post_observations} observations, "
          f"{null_proj} with NULL project")

    # 7. Rename the (now near-empty) source dir as a fallback.
    SOURCE.rename(SOURCE.parent / f"memory.migrated-{ts}")
    print(f"\ncutover complete. source renamed to memory.migrated-{ts}")
    print(f"backup retained at {backup}")
    print("\nNext: reload the crons —")
    for label in CRON_LABELS:
        print(f"  launchctl load ~/Library/LaunchAgents/{label}.plist")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print steps, change nothing")
    args = parser.parse_args()
    return cutover(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
