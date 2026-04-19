#!/usr/bin/env python3
"""
Transcript delta ingestion cron — run every 15 minutes via cron or launchd.

Reads new content from Claude Code session transcripts since the last cursor,
extracts durable observations via LLM, and appends them to the project's
ephemeral session buffer for downstream consolidation.

Usage:
    python3 scripts/transcript_cron.py
    python3 scripts/transcript_cron.py --dry-run
    python3 scripts/transcript_cron.py --dry-run --max-sessions 5

Install (crontab):
    */15 * * * * /usr/local/bin/python3 /path/to/scripts/transcript_cron.py >> /tmp/memesis-transcript.log 2>&1
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.transcript_ingest import tick  # type: ignore[import]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [transcript-cron] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcript delta ingestion cron")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print observations without writing anything",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=None,
        metavar="N",
        help="Cap the number of sessions processed per tick",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("Running in dry-run mode — no writes")

    results = tick(dry_run=args.dry_run, max_sessions=args.max_sessions)

    logger.info(
        "Done: %d session(s) processed, %d observation(s) appended, %d skipped",
        results["processed"],
        results["observations_total"],
        results["skipped"],
    )


if __name__ == "__main__":
    main()
