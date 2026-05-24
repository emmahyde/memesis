#!/usr/bin/env python3
"""
Backfill kind, subtitle, raw_importance, cwd on memories where these are NULL.

Strategy:
  1. For consolidated memories with a consolidation_log entry (action='kept'):
     - Parse llm_response.decisions, find the keep-decision matching this memory
       by subtitle ≈ title similarity (exact first, then fuzzy prefix).
     - Write kind/subtitle/raw_importance/cwd from that decision.
  2. For crystallized/instinctive memories without a usable log entry:
     - Run a lightweight LLM classification call on title+content.

Usage:
    uv run python3 scripts/backfill_enrichment_fields.py [--dry-run] [--verbose]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROJECT_DB = (
    Path.home() / ".claude" / "projects" / "-Users-emmahyde-projects-memesis" / "memory" / "index.db"
)


def find_matching_decision(decisions: list[dict], memory_title: str) -> dict | None:
    """Find the keep-decision in a batch that matches this memory by title/subtitle."""
    keeps = [d for d in decisions if d.get("action") == "keep"]
    if not keeps:
        return None

    # 1. Exact subtitle match
    for d in keeps:
        if d.get("subtitle", "").strip() == memory_title.strip():
            return d

    # 2. Subtitle starts-with title (truncation)
    title_lower = memory_title.lower()
    for d in keeps:
        sub = d.get("subtitle", "").lower()
        if sub.startswith(title_lower[:40]) or title_lower.startswith(sub[:40]):
            return d

    # 3. Title appears in facts
    for d in keeps:
        facts_text = " ".join(d.get("facts", []))
        if memory_title.lower() in facts_text.lower():
            return d

    # 4. Word-overlap similarity between title and subtitle
    title_words = set(title_lower.split())
    best_score = 0
    best_dec = None
    for d in keeps:
        sub_words = set(d.get("subtitle", "").lower().split())
        overlap = len(title_words & sub_words)
        if overlap > best_score:
            best_score = overlap
            best_dec = d
    if best_score >= 3:
        return best_dec

    # 5. Only one keep in batch — must be ours
    if len(keeps) == 1:
        return keeps[0]

    return None


def backfill_from_logs(conn, dry_run: bool, verbose: bool) -> tuple[int, int]:
    """
    Backfill enrichment for consolidated memories that have a consolidation_log entry.
    Returns (attempted, updated).
    """
    rows = conn.execute("""
        SELECT m.id, m.title, cl.llm_response
        FROM memories m
        JOIN consolidation_log cl ON cl.memory_id = m.id
        WHERE m.kind IS NULL
          AND cl.action = 'kept'
          AND cl.llm_response IS NOT NULL
    """).fetchall()

    attempted = 0
    updated = 0

    for mem_id, title, llm_resp_raw in rows:
        attempted += 1
        try:
            batch = json.loads(llm_resp_raw)
        except Exception as e:
            log.warning("Failed to parse llm_response for %s: %s", mem_id, e)
            continue

        decisions = batch.get("decisions", [])
        dec = find_matching_decision(decisions, title)
        if dec is None:
            log.warning("No matching decision found for %s (%r)", mem_id, title[:60])
            continue

        kind = dec.get("kind")
        subtitle = dec.get("subtitle")
        raw_importance = dec.get("raw_importance")
        cwd = dec.get("cwd")

        if verbose:
            log.info(
                "  %s: kind=%r subtitle=%r raw_imp=%r cwd=%r",
                mem_id[:8], kind, subtitle, raw_importance, cwd,
            )

        if not dry_run:
            conn.execute(
                """UPDATE memories
                   SET kind=?, subtitle=?, raw_importance=?, cwd=?
                   WHERE id=?""",
                (kind, subtitle, raw_importance, cwd, mem_id),
            )
        updated += 1

    return attempted, updated


def backfill_via_llm(conn, dry_run: bool, verbose: bool) -> tuple[int, int]:
    """
    Backfill enrichment for memories with no usable log entry via LLM classification.
    Returns (attempted, updated).
    """
    rows = conn.execute("""
        SELECT m.id, m.title, m.content, m.stage
        FROM memories m
        WHERE m.kind IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM consolidation_log cl
            WHERE cl.memory_id = m.id AND cl.action = 'kept' AND cl.llm_response IS NOT NULL
          )
    """).fetchall()

    if not rows:
        return 0, 0

    from core.llm import call_llm

    attempted = len(rows)
    updated = 0

    KIND_VALUES = ["decision", "finding", "preference", "constraint", "correction", "open_question"]
    KNOWLEDGE_TYPES = ["factual", "conceptual", "procedural", "metacognitive"]

    for mem_id, title, content, stage in rows:
        prompt = f"""Classify this memory entry and return a JSON object with these fields:
- kind: one of {KIND_VALUES}
- subtitle: a distinct 1-sentence elaboration of the title (different from the title itself)
- raw_importance: float 0.0-1.0 (how important is this to remember)
- knowledge_type: one of {KNOWLEDGE_TYPES}

Memory title: {title}

Memory content (first 400 chars):
{(content or '')[:400]}

Return ONLY valid JSON, no explanation."""

        try:
            response = call_llm(prompt, model="haiku", max_tokens=256)
            # Strip markdown code fences if present
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            dec = json.loads(text)
        except Exception as e:
            log.warning("LLM classification failed for %s: %s", mem_id, e)
            continue

        kind = dec.get("kind")
        subtitle = dec.get("subtitle")
        raw_importance = dec.get("raw_importance")

        if verbose:
            log.info(
                "  %s [%s]: kind=%r subtitle=%r", mem_id[:8], stage, kind, subtitle
            )

        if not dry_run:
            conn.execute(
                """UPDATE memories
                   SET kind=?, subtitle=?, raw_importance=?
                   WHERE id=?""",
                (kind, subtitle, raw_importance, mem_id),
            )
        updated += 1

    return attempted, updated


def run(dry_run: bool, verbose: bool) -> None:
    if not PROJECT_DB.exists():
        log.error("DB not found: %s", PROJECT_DB)
        sys.exit(1)

    import sqlite3
    # Rule-1 note: raw sqlite3.connect() is used here intentionally because
    # this script runs as a one-off migration tool, not as part of the live
    # Peewee-managed application path.  FTS5 sync for any memories rows
    # written here is handled automatically by the SQL triggers installed in
    # migration 0020 (memories_ai / memories_au / memories_ad), so these
    # writes are safe.  Do NOT replicate this pattern in application code —
    # use init_db() + Peewee models or db.execute_sql() instead.
    conn = sqlite3.connect(str(PROJECT_DB))
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA busy_timeout=5000")

    total_null = conn.execute("SELECT COUNT(*) FROM memories WHERE kind IS NULL").fetchone()[0]
    log.info("Memories with NULL kind: %d", total_null)

    log.info("=== Phase 1: backfill from consolidation_log ===")
    att1, upd1 = backfill_from_logs(conn, dry_run, verbose)
    log.info("Attempted: %d  Updated: %d", att1, upd1)

    if not dry_run:
        conn.commit()

    log.info("=== Phase 2: LLM classification for remainder ===")
    att2, upd2 = backfill_via_llm(conn, dry_run, verbose)
    log.info("Attempted: %d  Updated: %d", att2, upd2)

    if not dry_run:
        conn.commit()

    conn.close()

    remaining = total_null - upd1 - upd2 if not dry_run else total_null
    log.info("\nTotal: %d null → %d updated, %d remaining", total_null, upd1 + upd2, remaining)
    if dry_run:
        log.info("Dry run — no writes made.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    main()
