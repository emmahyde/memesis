#!/usr/bin/env python3
"""One-shot backfill: collapse near-duplicate memories using cosine similarity.

Walks all non-archived non-ephemeral memories, computes pairwise cosine,
and merges pairs above MEMESIS_AUTO_PROMOTE_THRESHOLD (default 0.85).
The survivor inherits summed reinforcement_count; the loser is archived
with subsumed_by set to the survivor.

Survivor selection: higher importance wins, ties broken by older created_at
(established memories are more authoritative than fresh ones).

Usage:
    uv run python3 scripts/merge_dupes.py [--dry-run]
"""

import struct
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db
from core.linking import LINK_AUTO_PROMOTE_THRESHOLD, _cosine
from core.models import Memory, MemoryEmbedding


def _vec(emb_bytes: bytes) -> list[float]:
    n = len(emb_bytes) // 4
    return list(struct.unpack(f"{n}f", emb_bytes))


def main(dry_run: bool = False) -> None:
    # Single global store at ~/.claude/memory.
    init_db()

    mems = list(
        Memory.select().where(
            Memory.archived_at.is_null(),
            Memory.stage != "ephemeral",
        )
    )
    print(f"Loaded {len(mems)} non-archived memories")

    vecs: dict[str, list[float]] = {}
    for m in mems:
        try:
            row = MemoryEmbedding.get(MemoryEmbedding.memory_id == m.id)
            if row.embedding:
                vecs[m.id] = _vec(bytes(row.embedding))
        except MemoryEmbedding.DoesNotExist:
            continue
    print(f"With embeddings: {len(vecs)}")

    # Build adjacency: pairs above threshold
    pairs: list[tuple[float, str, str]] = []
    ids = list(vecs.keys())
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            score = _cosine(vecs[a], vecs[b])
            if score >= LINK_AUTO_PROMOTE_THRESHOLD:
                pairs.append((score, a, b))
    pairs.sort(reverse=True)
    print(f"Pairs above {LINK_AUTO_PROMOTE_THRESHOLD}: {len(pairs)}")

    by_id = {m.id: m for m in mems}
    archived_ids: set[str] = set()
    merges = 0
    for score, a, b in pairs:
        if a in archived_ids or b in archived_ids:
            continue
        ma, mb = by_id[a], by_id[b]
        # Pick survivor: higher importance, then older
        if (ma.importance or 0) > (mb.importance or 0):
            survivor, loser = ma, mb
        elif (mb.importance or 0) > (ma.importance or 0):
            survivor, loser = mb, ma
        elif (ma.created_at or "") <= (mb.created_at or ""):
            survivor, loser = ma, mb
        else:
            survivor, loser = mb, ma

        rc_total = (survivor.reinforcement_count or 0) + (loser.reinforcement_count or 0) + 1
        print(f"  merge cosine={score:.3f}  rc {survivor.reinforcement_count or 0}+{loser.reinforcement_count or 0}+1={rc_total}")
        print(f"    survivor: {(survivor.title or '')[:80]}")
        print(f"    loser:    {(loser.title or '')[:80]}")

        if not dry_run:
            survivor.reinforcement_count = rc_total
            survivor.save()
            Memory.update(
                archived_at=datetime.now().isoformat(),
                subsumed_by=survivor.id,
            ).where(Memory.id == loser.id).execute()
        archived_ids.add(loser.id)
        merges += 1

    print(f"\n{'DRY RUN — would merge' if dry_run else 'Merged'}: {merges} pairs")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
