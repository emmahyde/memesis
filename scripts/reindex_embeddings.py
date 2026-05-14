#!/usr/bin/env python3
"""
Reindex all memory embeddings using the current embedding provider (fastembed).

Run after migrating from sqlite-vec to the numpy-backed VecStore. Embeddings
from the old vec0 table are NOT preserved across the migration; this script
recomputes them from Memory.title + summary + content.

At ~1K memories × ~15ms (fastembed CPU) ≈ 15s + ~3s model warmup.

Usage:
    python3 -m scripts.reindex_embeddings
    python3 -m scripts.reindex_embeddings --base-dir /path/to/memory
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Make the project root importable when invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import close_db, get_vec_store, init_db  # noqa: E402
from core.embeddings import embed_for_memory  # noqa: E402
from core.models import Memory  # noqa: E402

logger = logging.getLogger("reindex_embeddings")


def reindex(base_dir: str | None = None) -> tuple[int, int]:
    """Recompute embeddings for every Memory row.

    Returns (succeeded, failed)."""
    init_db(base_dir=base_dir)
    vec_store = get_vec_store()
    if vec_store is None or not vec_store.available:
        logger.error("VecStore unavailable; aborting.")
        close_db()
        return (0, 0)

    rows = list(Memory.select())
    total = len(rows)
    succeeded = 0
    failed = 0
    started = time.time()

    for i, mem in enumerate(rows, 1):
        try:
            emb = embed_for_memory(
                mem.title or "", mem.summary or "", mem.content or ""
            )
            if emb is None:
                failed += 1
                continue
            vec_store.store_embedding(mem.id, emb)
            succeeded += 1
        except Exception as exc:
            logger.warning("Reindex failed for %s: %s", mem.id, exc)
            failed += 1

        if i % 50 == 0 or i == total:
            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            print(f"  {i}/{total} ({rate:.1f}/s, {succeeded} ok, {failed} failed)")

    close_db()
    return (succeeded, failed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reindex memory embeddings.")
    parser.add_argument(
        "--base-dir",
        default=None,
        help="Override memory base directory (default: ~/.claude/memory).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    succeeded, failed = reindex(base_dir=args.base_dir)
    print(f"\nReindex complete: {succeeded} succeeded, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
