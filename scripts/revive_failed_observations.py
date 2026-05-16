#!/usr/bin/env python3
"""
Revive observations stuck at status='failed' by flipping them back to 'pending'
so the next consolidation tick re-processes them.

Failed observations accumulate when the consolidation LLM call raises (e.g.
botocore missing, network blip, rate limit). The pre-fix cron unlinked the
snapshot in a finally block, so the buffer was lost AND the DB rows kept
status='failed' indefinitely.

Usage:
    uv run python3 scripts/revive_failed_observations.py --dry-run
    uv run python3 scripts/revive_failed_observations.py
    uv run python3 scripts/revive_failed_observations.py --session-prefix cron-20260515
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db  # noqa: E402
from core.models import Observation  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [revive] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count failed observations without flipping them",
    )
    parser.add_argument(
        "--session-prefix",
        default=None,
        help="Only revive observations whose session_id starts with this prefix",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help=(
            "Path to the project memory dir containing index.db (e.g. "
            "~/.claude/projects/<slug>/memory). Defaults to the global memesis dir."
        ),
    )
    args = parser.parse_args()

    init_db(base_dir=args.base_dir)
    try:
        query = Observation.select().where(Observation.status == "failed")
        if args.session_prefix:
            query = query.where(
                Observation.session_id.startswith(args.session_prefix)
            )
        rows = list(query)
        logger.info("found %d failed observation(s)", len(rows))

        by_session: dict[str, int] = {}
        for o in rows:
            sid = o.session_id or "<null>"
            by_session[sid] = by_session.get(sid, 0) + 1
        for sid, n in sorted(by_session.items()):
            logger.info("  %s: %d", sid, n)

        if args.dry_run:
            logger.info("dry-run: no changes")
            return

        if not rows:
            return

        ids = [o.id for o in rows]
        n = (
            Observation.update(status="pending")
            .where(Observation.id.in_(ids))
            .execute()
        )
        logger.info("flipped %d row(s) failed -> pending", n)
    finally:
        close_db()


if __name__ == "__main__":
    main()
