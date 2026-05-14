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
from core.transcript import read_transcript_from, summarize
from core.transcript_ingest import (
    append_to_ephemeral,
    extract_observations,
    project_memory_dir,
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

    mem_dir = project_memory_dir(jsonl_path)
    # Use base_dir directly — project_context would re-slug and write elsewhere.
    init_db(base_dir=str(mem_dir))

    before = _stage_counts()
    logger.info("Stage counts before: %s", before)

    # Read full transcript from offset 0 (bypass cursor).
    entries, _, _ = read_transcript_from(jsonl_path, 0)
    if not entries:
        logger.warning("transcript empty: %s", jsonl_path)
        close_db()
        return

    rendered = summarize(entries)

    session_cwd = None
    tool_uses = []
    for entry in entries:
        msg = entry.get("message") or {}
        if not session_cwd:
            session_cwd = entry.get("cwd") or msg.get("cwd")
        if entry.get("type") == "tool_use" or msg.get("type") == "tool_use":
            tool_name = entry.get("tool_name") or msg.get("name") or ""
            file_path = entry.get("input", {}).get("file_path") or ""
            if tool_name:
                tool_uses.append({"tool_name": tool_name, "file_path": file_path})

    session_type = detect_session_type(session_cwd, tool_uses or None)
    logger.info("Session type: %s | %d entries | %d tool_uses",
                session_type, len(entries), len(tool_uses))

    obs_list = extract_observations(rendered, session_type=session_type)
    for obs in obs_list:
        obs.setdefault("session_type", session_type)
    # Clear today's ephemeral so re-runs of the same transcript don't duplicate.
    eph_clear = mem_dir / "ephemeral" / f"session-{date.today().isoformat()}.md"
    if eph_clear.exists():
        eph_clear.unlink()
    n_appended = append_to_ephemeral(mem_dir, obs_list, dry_run=False)
    logger.info("Extracted %d observations, appended %d to ephemeral",
                len(obs_list), n_appended)

    # Consolidate today's ephemeral file.
    eph_path = mem_dir / "ephemeral" / f"session-{date.today().isoformat()}.md"
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
