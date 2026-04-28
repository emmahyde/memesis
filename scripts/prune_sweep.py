"""
prune_sweep.py — Hard-delete sweep for memories past the 2× TTL window.

Decision B1: two-stage enforcement.
  Stage 1 (soft-archive): log_shadow_prune() sets archived_at when SHADOW_ONLY=False.
  Stage 2 (hard-delete):  THIS SCRIPT deletes rows where time-since-archived > tier_ttl.

2× TTL window explanation
--------------------------
When a memory is soft-archived, archived_at is set to "now".  expires_at was
set at creation to (created_at + tier_ttl).  After soft-archive, the memory sits
in the archive for up to one more full TTL period before we hard-delete it.

Gate condition:
    int(time.time()) > archived_at_unix + tier_ttl(stage_to_tier(stage))

In other words: current time exceeds archived_at by one TTL period.  Since
expires_at already represents (created_at + TTL), the total elapsed time
since creation is at least 2× TTL before hard-deletion fires — hence "2× TTL window".

Cascade: Memory.hard_delete() handles FTS5 + vec_memories + primary row in one
db.atomic() block.  This script never issues raw DELETE FROM memories.

Usage
-----
    python scripts/prune_sweep.py [--dry-run] [--base-dir PATH]

Options
-------
--dry-run       Print candidates without deleting (always safe to run).
--base-dir PATH Override the default memory base directory.
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure repo root is on sys.path so core.* imports work.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.database import init_db, close_db  # noqa: E402
from core.models import Memory, db  # noqa: E402
from core.tiers import stage_to_tier, tier_ttl  # noqa: E402


def _archived_at_unix(archived_at_value: str) -> int | None:
    """Convert archived_at (ISO string or Unix int string) to a Unix timestamp.

    Memory.archived_at is a TextField that log_shadow_prune() writes as an
    ISO-8601 string (e.g. "2026-04-28T12:34:56.789Z").  Earlier code paths may
    have written a plain integer string.  Returns None if unparseable.
    """
    if not archived_at_value:
        return None
    # Try plain integer first (legacy path).
    try:
        return int(archived_at_value)
    except (ValueError, TypeError):
        pass
    # Try ISO-8601 (the current path from log_shadow_prune).
    raw = archived_at_value.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def find_hard_delete_candidates() -> list[Memory]:
    """Return archived memories past the 2× TTL window.

    Gate: archived_at IS NOT NULL AND expires_at IS NOT NULL
          AND int(time.time()) > archived_at_unix + tier_ttl(stage_to_tier(stage))

    Memories where tier_ttl returns None (T1 / instinctive) are never hard-deleted.
    """
    now = int(time.time())

    # Fetch all archived rows that have an expires_at value set.
    # Filtering further in Python is fine for sweep volumes (not a hot path).
    candidates = []
    rows = (
        Memory.select()
        .where(
            Memory.archived_at.is_null(False)
            & Memory.expires_at.is_null(False)
        )
    )
    for mem in rows:
        ttl = tier_ttl(stage_to_tier(mem.stage))
        if ttl is None:
            # T1 — instinctive memories never hard-delete.
            continue

        archived_unix = _archived_at_unix(mem.archived_at)
        if archived_unix is None:
            # Cannot parse archived_at; skip safely.
            continue

        # 2× TTL gate: current time must exceed archived_at + one full TTL period.
        # Total age since creation is at least: TTL (to expiry) + TTL (archive window)
        # = 2× TTL before this fires.
        if now > archived_unix + ttl:
            candidates.append(mem)

    return candidates


def run_sweep(dry_run: bool = False) -> int:
    """Scan for and hard-delete (or report) memories past the 2× TTL window.

    Parameters
    ----------
    dry_run:
        If True, print candidates without deleting.

    Returns
    -------
    int
        Number of memories deleted (0 in dry-run mode).
    """
    candidates = find_hard_delete_candidates()

    if not candidates:
        print(f"prune_sweep: 0 candidates found. Nothing to do.")
        return 0

    deleted = 0
    skipped = 0
    for mem in candidates:
        label = f"[{mem.id}] stage={mem.stage} archived_at={mem.archived_at} expires_at={mem.expires_at}"
        if dry_run:
            print(f"  DRY-RUN would hard-delete: {label}")
        else:
            try:
                Memory.hard_delete(mem.id)
                deleted += 1
            except Exception as exc:
                print(f"  ERROR hard-deleting {label}: {exc}", file=sys.stderr)
                skipped += 1

    if dry_run:
        print(f"prune_sweep: dry-run complete. {len(candidates)} candidate(s) identified.")
    else:
        print(
            f"prune_sweep: sweep complete. "
            f"deleted={deleted} skipped={skipped} total_candidates={len(candidates)}"
        )
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hard-delete archived memories past the 2× TTL window (Decision B1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print candidates without deleting.",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help="Override the default memory base directory passed to init_db().",
    )
    args = parser.parse_args()

    init_db(base_dir=args.base_dir)
    try:
        run_sweep(dry_run=args.dry_run)
    finally:
        close_db()


if __name__ == "__main__":
    main()
