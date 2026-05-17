#!/usr/bin/env python3
"""Ingest a single transcript JSONL through the full memory pipeline.

Usage:
    python3 scripts/ingest_one.py <path/to/session.jsonl>

Bypasses the cursor (always reads the full file from offset 0). Runs:
  1. extract_observations
  2. append_to_ephemeral
  3. Consolidator.consolidate_session  (rc++ on matched memories)
  4. Crystallizer.crystallize_candidates  (promote rc>=3 memories)

Reports stage deltas + any crystallizations.
"""

import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.crystallizer import Crystallizer
from core.database import close_db, init_db
from core.lifecycle import LifecycleManager
from core.models import Memory
from core.session_detector import detect_session_type
from core.transcript import extract_tool_uses, read_transcript_from
from core.transcript_ingest import (
    append_to_ephemeral,
    extract_observations_hierarchical,
    global_memory_dir,
    transcript_project_slug,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ingest_one] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _stage_counts() -> dict:
    return {
        s: Memory.select().where(Memory.stage == s, Memory.archived_at.is_null()).count()
        for s in ("ephemeral", "consolidated", "crystallized", "instinctive")
    }


def ingest(jsonl_path: Path) -> None:
    if not jsonl_path.exists():
        sys.exit(f"transcript not found: {jsonl_path}")

    # Single global store: one index.db at ~/.claude/memory. Project identity
    # is recorded in the `project` column via init_db(project=<slug>); the
    # ephemeral buffer stages under <global>/ephemeral/<slug>/.
    project_slug = transcript_project_slug(jsonl_path)
    mem_dir = global_memory_dir()
    init_db(project=project_slug)

    before = _stage_counts()
    logger.info("Stage counts before: %s", before)

    # Read full transcript from offset 0 (bypass cursor).
    entries, _, transcript_cwd = read_transcript_from(jsonl_path, 0)
    if not entries:
        logger.warning("transcript empty: %s", jsonl_path)
        close_db()
        return

    # cwd: returned directly by read_transcript_from (raw file scan).
    # tool_uses: entries are {role,text} — scan raw JSONL for tool_use blocks.
    session_cwd = transcript_cwd
    tool_uses = extract_tool_uses(jsonl_path)

    session_type = detect_session_type(session_cwd, tool_uses or None)
    logger.info("Session type: %s | %d entries | %d tool_uses",
                session_type, len(entries), len(tool_uses))

    # Windowed (map-reduce) extraction. A single flat extract_observations()
    # call on a whole transcript under-extracts badly — the model self-limits
    # its JSON array regardless of input size. Hierarchical extraction maps
    # over overlapping windows so coverage scales with session length.
    # ingest_one ingests the FULL session, so size max_windows to cover it.
    total_chars = sum(len(e.get("text", "")) for e in entries)
    n_windows = max(10, total_chars // 12800 + 2)  # 12800 = default stride
    result = extract_observations_hierarchical(
        entries,
        session_type=session_type,
        max_windows=n_windows,
        cwd=session_cwd,
    )
    obs_list = result["observations"]
    logger.info(
        "Hierarchical extraction: %d window(s), raw=%d → %d deduped observation(s)",
        result["windows"], result.get("raw_count", 0), len(obs_list),
    )
    for obs in obs_list:
        obs.setdefault("session_type", session_type)
    # Clear today's ephemeral so re-runs of the same transcript don't duplicate.
    eph_path = mem_dir / "ephemeral" / project_slug / f"session-{date.today().isoformat()}.md"
    if eph_path.exists():
        eph_path.unlink()
    n_appended = append_to_ephemeral(
        mem_dir, obs_list, dry_run=False, project_slug=project_slug
    )
    logger.info("Extracted %d observations, appended %d to ephemeral",
                len(obs_list), n_appended)

    # Consolidate today's ephemeral file.
    lifecycle = LifecycleManager()
    if not eph_path.exists():
        logger.warning("No ephemeral file at %s — nothing to consolidate", eph_path)
    else:
        consolidator = Consolidator(lifecycle=lifecycle)
        result = consolidator.consolidate_session(str(eph_path), session_id=jsonl_path.stem)
        logger.info("Consolidation: kept=%d pruned=%d promoted=%d conflicts=%d",
                    len(result.get("kept", [])),
                    len(result.get("pruned", [])),
                    len(result.get("promoted", [])),
                    len(result.get("conflicts", [])))

    # Crystallization pass.
    candidates = lifecycle.get_promotion_candidates()
    logger.info("Promotion candidates (rc>=3, spacing met): %d", len(candidates))
    if candidates:
        crystallizer = Crystallizer(lifecycle)
        crystallized = crystallizer.crystallize_candidates()
        for c in crystallized:
            logger.info("CRYSTALLIZED: %s (group=%d)",
                        c.get("title", "?")[:80], c.get("group_size", 0))
    else:
        crystallized = []

    after = _stage_counts()
    logger.info("Stage counts after:  %s", after)
    logger.info("Delta: %s",
                {k: after[k] - before[k] for k in after})

    if crystallized:
        print(f"\n>>> {len(crystallized)} memory(ies) crystallized this run <<<")

    close_db()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: ingest_one.py <transcript.jsonl>")
    ingest(Path(sys.argv[1]).expanduser().resolve())
