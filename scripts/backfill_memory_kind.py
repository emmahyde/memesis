#!/usr/bin/env python3
"""
Backfill memories.memory_kind where it is NULL.

Most pre-classification memories carry memory_kind=NULL, so the session-start
panel renders them with the _DEFAULT_EMOJI fallback (🔸). This script assigns
a curated kind to every non-ephemeral NULL row.

Strategy:
  Phase 1 — deterministic: if the row has an observation `kind`, map it via
    derive_memory_kind (free, no LLM).
  Phase 2 — LLM: classify the remainder via classify_memory_kind.
  open_question rows are skipped — memory_kind=NULL is correct for them.

All DB access goes through the Peewee `db` singleton (CLAUDE.md Rule 1).

Usage:
    uv run python3 scripts/backfill_memory_kind.py [--dry-run] [--verbose] [--limit N]
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db  # noqa: E402
from core.models import Memory  # noqa: E402
from core.validators import classify_memory_kind, derive_memory_kind  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Non-ephemeral stages — ephemeral rows are pre-curation and legitimately NULL.
_BACKFILL_STAGES = ("consolidated", "crystallized", "instinctive")


def run(dry_run: bool, verbose: bool, limit: int | None) -> None:
    init_db()

    query = Memory.select().where(
        Memory.memory_kind.is_null(True),
        Memory.stage.in_(_BACKFILL_STAGES),
    )
    rows = list(query.limit(limit) if limit else query)
    log.info("NULL memory_kind in %s: %d row(s)", "/".join(_BACKFILL_STAGES), len(rows))

    deterministic = 0
    llm = 0
    skipped = 0
    failed = 0

    for mem in rows:
        if mem.kind == "open_question":
            skipped += 1
            continue

        kind = derive_memory_kind(mem.kind)
        source = "derive"
        if kind is None:
            kind = classify_memory_kind(mem.title or "", mem.content or "")
            source = "llm"

        if kind is None:
            failed += 1
            log.warning("Unclassifiable: %s (%r)", mem.id[:8], (mem.title or "")[:60])
            continue

        if verbose:
            log.info("  %s [%s] %s -> %s", mem.id[:8], mem.stage, source, kind)

        if not dry_run:
            mem.memory_kind = kind
            mem.save()

        if source == "derive":
            deterministic += 1
        else:
            llm += 1

    log.info(
        "Done. deterministic=%d llm=%d skipped(open_question)=%d unclassifiable=%d",
        deterministic, llm, skipped, failed,
    )
    if dry_run:
        log.info("Dry run — no writes made.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="cap rows processed")
    args = parser.parse_args()
    run(dry_run=args.dry_run, verbose=args.verbose, limit=args.limit)


if __name__ == "__main__":
    main()
