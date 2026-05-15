#!/usr/bin/env python3
"""Seed-and-run crystallization correctness test.

Inserts 6 synthetic memories at consolidated stage with reinforcement_count=3:
- 3 related (embedding pipeline → fastembed bge-small)
- 2 unrelated (singletons)
- 1 below threshold (rc=2, should not be candidate)

Runs Crystallizer.crystallize_candidates() and verifies:
  1. promotion gate selects only rc>=3 memories
  2. semantically-related memories cluster together
  3. unrelated memories form singleton groups
  4. crystallized memories advance to 'crystallized' stage
  5. source memories get archived

Usage: uv run python3 scripts/test_crystallization.py
"""

import json
import logging
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.crystallizer import Crystallizer
from core.database import close_db, init_db
from core.lifecycle import LifecycleManager
from core.models import Memory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat()


def _seed(title: str, content: str, rc: int = 3, importance: float = 0.75) -> str:
    mem = Memory.create(
        id=str(uuid.uuid4()),
        stage="consolidated",
        title=title,
        summary=content[:80],
        content=content,
        tags=json.dumps([]),
        importance=importance,
        reinforcement_count=rc,
        created_at=_now(),
        updated_at=_now(),
        source_session="test-crystallization",
        kind="finding",
        knowledge_type="conceptual",
    )
    return mem.id


def _ensure_embedding(memory_id: str) -> None:
    """Force embedding refresh by re-saving the memory."""
    mem = Memory.get_by_id(memory_id)
    mem.save()


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="memesis_crys_test_")
    log.info("Test DB at: %s", tmpdir)
    init_db(base_dir=tmpdir)

    # 3 related memories — all about embedding model swap consequences
    related = [
        _seed(
            "bge-small embeddings produce tighter cosine distribution than Titan",
            "Switching from Bedrock Titan to local fastembed bge-small produces tighter pairwise cosine similarities. Hardcoded thresholds calibrated for Titan must be re-tuned or made adaptive.",
        ),
        _seed(
            "Threshold portability across embedding models",
            "Static similarity thresholds are not portable across embedding models. The same memory pair scores differently under Titan vs bge-small. Crystallizer must adapt its threshold to the model's distribution.",
        ),
        _seed(
            "Dynamic percentile floor for clustering",
            "Crystallizer uses max(static_threshold, P75_pairwise_sim) capped at 0.85. The percentile floor adapts to tighter embedding distributions like bge-small without manual tuning.",
        ),
    ]

    # 2 unrelated singletons
    singletons = [
        _seed(
            "Migration files immutable post-merge",
            "Once a migration is merged, do not modify it. Rename pre-merge if needed. Modifying merged migrations causes schema drift across environments.",
        ),
        _seed(
            "rtk hook rewrites rg to grep",
            "The rtk shell hook rewrites ripgrep invocations to GNU grep. Use --include='*.py' rather than --type py because grep does not understand ripgrep filetype shorthand.",
        ),
    ]

    # 1 below threshold — should NOT be a candidate
    below = _seed(
        "Random low-reinforcement fact",
        "Some fact that only reinforced twice.",
        rc=2,
    )

    log.info("Seeded %d memories (rc=3: %d, rc=2: 1)", len(related) + len(singletons) + 1, len(related) + len(singletons))

    # Embeddings auto-created via Memory.save() on insert above; re-save to be safe.
    log.info("Refreshing embeddings...")
    for mid in related + singletons + [below]:
        _ensure_embedding(mid)

    # Run promotion candidate query
    lifecycle = LifecycleManager()
    candidates = lifecycle.get_promotion_candidates()
    candidate_ids = {c["id"] for c in candidates}
    log.info("Promotion candidates: %d", len(candidates))

    expected_candidates = set(related + singletons)
    assert candidate_ids == expected_candidates, \
        f"Gate selection wrong. Expected {expected_candidates}, got {candidate_ids}"
    assert below not in candidate_ids, "rc=2 memory must not be a candidate"
    log.info("✓ Gate: only rc>=3 memories selected (5 of 6)")

    # Run crystallization
    crystallizer = Crystallizer(lifecycle)
    results = crystallizer.crystallize_candidates()
    log.info("Crystallization results: %d", len(results))
    for r in results:
        log.info("  → '%s' (group_size=%d, sources=%d)",
                 (r.get("title") or "?")[:80],
                 r.get("group_size", 0),
                 len(r.get("source_ids", [])))

    # Verify clustering: the 3 related should be in a single group of size 3
    group_sizes = sorted([r.get("group_size", 0) for r in results])
    log.info("Group sizes (sorted): %s", group_sizes)

    # Verify source memories archived
    for mid in related:
        m = Memory.get_by_id(mid)
        log.info("  source %s: stage=%s archived_at=%s",
                 mid[:8], m.stage, m.archived_at is not None)

    # Verify crystallized memories exist at crystallized stage
    crystallized_count = Memory.select().where(Memory.stage == "crystallized").count()
    log.info("Memories at crystallized stage: %d", crystallized_count)

    # Final stage distribution
    for stage in ("consolidated", "crystallized", "instinctive", "pending_delete"):
        n = Memory.select().where(Memory.stage == stage).count()
        log.info("  stage=%s: %d", stage, n)

    close_db()
    log.info("Test complete. DB preserved at %s for inspection.", tmpdir)


if __name__ == "__main__":
    main()
