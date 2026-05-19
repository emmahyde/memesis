#!/usr/bin/env python3
"""Reset transcript cursors so tick() re-ingests from byte 0.

Setting last_byte_offset=0 is the right way to replay — deleting the row
makes tick() treat the session as new and seed at EOF (skipping everything).

Usage:
    uv run python3 scripts/reset_cursors.py --all
    uv run python3 scripts/reset_cursors.py --session ses_abc123
    uv run python3 scripts/reset_cursors.py --since 24h
    uv run python3 scripts/reset_cursors.py --all --dry-run
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cursors import CursorStore


def _parse_since(spec: str) -> int:
    units = {"h": 3600, "d": 86400, "m": 60}
    if spec[-1] in units:
        return int(spec[:-1]) * units[spec[-1]]
    return int(spec)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Reset every cursor")
    g.add_argument("--session", metavar="ID", help="Reset one session id")
    g.add_argument("--since", metavar="SPEC", help="Reset cursors last run within window (e.g. 24h, 7d)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    with CursorStore() as store:
        conn = store._conn
        if args.session:
            rows = conn.execute(
                "SELECT session_id, transcript_path FROM transcript_cursors WHERE session_id=?",
                (args.session,),
            ).fetchall()
        elif args.since:
            cutoff = int(time.time()) - _parse_since(args.since)
            rows = conn.execute(
                "SELECT session_id, transcript_path FROM transcript_cursors WHERE last_run_at >= ?",
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT session_id, transcript_path FROM transcript_cursors",
            ).fetchall()

        n = len(rows)
        if not n:
            print("No matching cursors.")
            return

        print(f"Resetting {n} cursor(s) to byte 0{' (dry-run)' if args.dry_run else ''}")
        if args.dry_run:
            for r in rows[:5]:
                print(f"  would reset: {r['session_id']}")
            if n > 5:
                print(f"  ... and {n-5} more")
            return

        conn.execute(
            f"UPDATE transcript_cursors SET last_byte_offset=0 WHERE session_id IN ({','.join('?'*n)})",
            tuple(r["session_id"] for r in rows),
        )
        conn.commit()
        print(f"Reset {n} cursor(s). Run scripts/transcript_cron.py to re-ingest.")


if __name__ == "__main__":
    main()
