#!/usr/bin/env python3
"""
Lifecycle heartbeat — the missing orchestrator.

Runs the full memory lifecycle pipeline that ties together the dormant
components: crystallization, relevance maintenance, self-reflection,
and manifest regeneration.

The individual engines all exist and are tested, but nothing connected
them into a running pipeline.  This script is the heartbeat.

Usage:
    python3 scripts/heartbeat.py                    # Run full lifecycle
    python3 scripts/heartbeat.py --bootstrap         # Fix reinforcements + run lifecycle
    python3 scripts/heartbeat.py --dry-run           # Show what would happen
    python3 scripts/heartbeat.py --report            # Print lifecycle health only
"""

import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.crystallizer import Crystallizer
from core.lifecycle import LifecycleManager
from core.manifest import ManifestGenerator
from core.relevance import RelevanceEngine
from core.self_reflection import SelfReflector
from core.storage import MemoryStore
from core.threads import build_threads

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [heartbeat] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bootstrap: fix reinforcement counts for backfill memories
# ---------------------------------------------------------------------------

def bootstrap_reinforcements(store: MemoryStore, dry_run: bool = False) -> dict:
    """
    Fix reinforcement_count for backfill memories.

    The backfill pipeline (scan -> reduce -> consolidate -> seed) extracted
    memories from dozens of sessions.  A title mismatch between consolidation
    stages caused all reinforcement_counts to be seeded at 0.

    Strategy: try to match memories against the observations.db frequency
    counts using content keyword overlap.  For backfill memories that can't
    be matched, set a baseline of 3 (they earned it by surviving the full
    quality pipeline).

    Returns:
        {"matched": N, "baseline": N, "skipped": N}
    """
    observations_db = Path(__file__).parent.parent / "backfill-output" / "observations.db"

    # Load frequency counts from observations.db if available
    obs_keywords = {}  # {set_of_keywords: count}
    obs_by_title = {}
    if observations_db.exists():
        with sqlite3.connect(observations_db) as conn:
            rows = conn.execute(
                "SELECT title, content, count FROM observations WHERE count > 1"
            ).fetchall()
            for title, content, count in rows:
                obs_by_title[title] = count
                words = _significant_words(title + " " + (content or ""))
                if words:
                    obs_keywords[frozenset(words)] = count

    # Load all consolidated memories
    consolidated = store.list_by_stage("consolidated")
    matched = baseline = skipped = 0

    for memory in consolidated:
        if memory.get("reinforcement_count", 0) > 0:
            skipped += 1
            continue

        # Try direct title match first
        title = memory.get("title", "")
        if title in obs_by_title:
            count = obs_by_title[title]
            if not dry_run:
                store.update(memory["id"], metadata={"reinforcement_count": count})
            matched += 1
            logger.info("Matched '%s' -> reinforcement_count=%d", title[:50], count)
            continue

        # Try keyword overlap match
        full = store.get(memory["id"])
        mem_words = _significant_words(title + " " + (full.get("content") or ""))
        best_count = 0
        best_overlap = 0

        for obs_words, count in obs_keywords.items():
            overlap = len(mem_words & obs_words)
            if overlap > best_overlap and overlap >= 3:
                best_overlap = overlap
                best_count = count

        if best_count > 0:
            if not dry_run:
                store.update(memory["id"], metadata={"reinforcement_count": best_count})
            matched += 1
            logger.info("Fuzzy matched '%s' -> reinforcement_count=%d (overlap=%d)",
                        title[:50], best_count, best_overlap)
        else:
            # Baseline: survived the full pipeline, deserves promotion eligibility
            if not dry_run:
                store.update(memory["id"], metadata={"reinforcement_count": 3})
            baseline += 1
            logger.info("Baseline '%s' -> reinforcement_count=3", title[:50])

    return {"matched": matched, "baseline": baseline, "skipped": skipped}


def _significant_words(text: str) -> set[str]:
    """Extract significant words (4+ chars, alpha) from text."""
    return {w.lower() for w in text.split() if len(w) >= 4 and w.isalpha()}


# ---------------------------------------------------------------------------
# Lifecycle phases
# ---------------------------------------------------------------------------

def run_crystallization(
    store: MemoryStore,
    lifecycle: LifecycleManager,
    dry_run: bool = False,
) -> list[dict]:
    """
    Find promotion candidates and run them through crystallization.

    The crystallizer groups related memories, synthesizes them into denser
    insights via LLM, and archives the source memories.

    Returns:
        List of crystallization results.
    """
    candidates = lifecycle.get_promotion_candidates()
    if not candidates:
        logger.info("No promotion candidates (need reinforcement_count >= 3)")
        return []

    logger.info("Found %d promotion candidates", len(candidates))

    if dry_run:
        for c in candidates:
            logger.info("  Candidate: '%s' (reinforcement=%d)",
                        c.get("title", "?")[:60], c.get("reinforcement_count", 0))
        return []

    crystallizer = Crystallizer(store, lifecycle)
    results = crystallizer.crystallize_candidates()

    for r in results:
        logger.info("Crystallized: '%s' (from %d sources)",
                    r.get("title", "?")[:60], r.get("group_size", 0))

    return results


def run_relevance_maintenance(
    store: MemoryStore,
    project_context: str = None,
    dry_run: bool = False,
) -> dict:
    """
    Archive stale memories and rehydrate relevant archived ones.

    Returns:
        {"archived": [...], "rehydrated": [...]}
    """
    engine = RelevanceEngine(store)

    if dry_run:
        archival = engine.get_archival_candidates(project_context)
        rehydration = engine.get_rehydration_candidates(project_context)
        logger.info("Would archive %d memories, rehydrate %d", len(archival), len(rehydration))
        for a in archival[:5]:
            logger.info("  Archive: '%s' (relevance=%.3f)",
                        a.get("title", "?")[:50], a.get("relevance", 0))
        for r in rehydration[:5]:
            logger.info("  Rehydrate: '%s' (relevance=%.3f)",
                        r.get("title", "?")[:50], r.get("relevance", 0))
        return {"archived": archival, "rehydrated": rehydration}

    return engine.run_maintenance(project_context)


def run_self_reflection(
    store: MemoryStore,
    force: bool = False,
    dry_run: bool = False,
) -> dict | None:
    """
    Run self-reflection if enough consolidation cycles have elapsed.

    Self-reflection runs every 5 consolidation cycles (checked via a
    counter file).  Pass force=True to run regardless.

    Returns:
        Reflection result dict, or None if skipped.
    """
    reflector = SelfReflector(store)

    # Ensure instinctive layer exists
    reflector.ensure_instinctive_layer()

    # Check counter
    counter_path = store.base_dir / "meta" / "consolidation-count.json"
    count = 0
    if counter_path.exists():
        try:
            count = json.loads(counter_path.read_text()).get("count", 0)
        except (json.JSONDecodeError, OSError):
            pass

    if not force and count % 5 != 0:
        logger.info("Self-reflection not due (consolidation count: %d, next at %d)",
                    count, count + (5 - count % 5))
        return None

    if dry_run:
        logger.info("Would run self-reflection (consolidation count: %d)", count)
        return None

    logger.info("Running self-reflection (consolidation count: %d)", count)
    reflection = reflector.reflect()

    if reflection.get("observations") or reflection.get("deprecated"):
        reflector.apply_reflection(reflection)
        logger.info("Self-reflection: %d new observations, %d deprecated",
                    len(reflection.get("observations", [])),
                    len(reflection.get("deprecated", [])))
    else:
        logger.info("Self-reflection: no changes")

    return reflection


def regenerate_manifest(store: MemoryStore, dry_run: bool = False) -> None:
    """Regenerate MEMORY.md from current store state."""
    manifest = ManifestGenerator(store)

    if dry_run:
        token_count, fraction = manifest.estimate_token_budget()
        logger.info("Would regenerate manifest (~%d tokens, %.1f%% of window)",
                    token_count, fraction * 100)
        return

    manifest.write_manifest()
    token_count, fraction = manifest.estimate_token_budget()
    logger.info("Manifest regenerated (~%d tokens, %.1f%% of window)",
                token_count, fraction * 100)


# ---------------------------------------------------------------------------
# Health report
# ---------------------------------------------------------------------------

def print_health(store: MemoryStore, project_context: str = None):
    """Print a concise lifecycle health report."""
    relevance = RelevanceEngine(store)
    lifecycle = LifecycleManager(store)

    print(f"\n{'=' * 55}")
    print(f"  LIFECYCLE HEARTBEAT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 55}")

    # Stage counts
    stages = {}
    total = 0
    for stage in ("instinctive", "crystallized", "consolidated", "ephemeral"):
        active = store.list_by_stage(stage)
        stages[stage] = len(active)
        total += len(active)
    archived = len(store.list_archived())

    print(f"\n  Stages:")
    for stage, count in stages.items():
        bar = "█" * min(count, 40)
        print(f"    {stage:14s} {count:4d}  {bar}")
    print(f"    {'archived':14s} {archived:4d}")
    print(f"    {'TOTAL':14s} {total:4d}")

    # Promotion readiness
    candidates = lifecycle.get_promotion_candidates()
    with sqlite3.connect(store.db_path) as conn:
        rc_dist = conn.execute("""
            SELECT reinforcement_count, COUNT(*) FROM memories
            WHERE stage = 'consolidated' GROUP BY reinforcement_count
            ORDER BY reinforcement_count DESC
        """).fetchall()

    print(f"\n  Promotion readiness:")
    print(f"    Candidates (reinforcement >= 3): {len(candidates)}")
    if rc_dist:
        for rc, cnt in rc_dist:
            print(f"    reinforcement_count={rc}: {cnt} memories")

    # Relevance summary
    scored = relevance.score_all(project_context)
    if scored:
        avg_rel = sum(s["relevance"] for s in scored) / len(scored)
        archival_candidates = relevance.get_archival_candidates(project_context)
        print(f"\n  Relevance:")
        print(f"    Average: {avg_rel:.3f}")
        print(f"    Archival candidates (below {relevance.archive_threshold}): {len(archival_candidates)}")

    # Demotion candidates
    demotion = lifecycle.get_demotion_candidates()
    if demotion:
        print(f"\n  Demotion candidates (injected 10+, never used): {len(demotion)}")

    # Consolidation counter
    counter_path = store.base_dir / "meta" / "consolidation-count.json"
    if counter_path.exists():
        try:
            count = json.loads(counter_path.read_text()).get("count", 0)
            next_refl = 5 - (count % 5)
            print(f"\n  Self-reflection: next in {next_refl} consolidation cycles (count={count})")
        except Exception:
            pass

    # Recommendations
    recs = []
    if len(candidates) == 0 and stages.get("consolidated", 0) > 0:
        with sqlite3.connect(store.db_path) as conn:
            zero_rc = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE stage='consolidated' AND reinforcement_count=0"
            ).fetchone()[0]
        if zero_rc > 0:
            recs.append(f"  {zero_rc} consolidated memories have reinforcement_count=0 "
                        f"— run --bootstrap to fix")
    if len(candidates) > 0 and stages.get("crystallized", 0) == 0:
        recs.append(f"  {len(candidates)} candidates ready — run without --dry-run to crystallize")
    if stages.get("instinctive", 0) == 0:
        recs.append("  No instinctive memories — self-reflection will seed them")

    if recs:
        print(f"\n  Recommendations:")
        for r in recs:
            print(f"  {r}")

    print(f"\n{'=' * 55}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    project_context = None
    dry_run = False
    bootstrap = False
    report_only = False
    force_reflect = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--project-context" and i + 1 < len(args):
            project_context = args[i + 1]; i += 2
        elif args[i] == "--dry-run":
            dry_run = True; i += 1
        elif args[i] == "--bootstrap":
            bootstrap = True; i += 1
        elif args[i] == "--report":
            report_only = True; i += 1
        elif args[i] == "--force-reflect":
            force_reflect = True; i += 1
        else:
            print(f"Unknown arg: {args[i]}", file=sys.stderr)
            sys.exit(1)

    store = MemoryStore(project_context=project_context)

    if report_only:
        print_health(store, project_context)
        store.close()
        return

    print_health(store, project_context)

    # Phase 0: Bootstrap reinforcements if requested
    if bootstrap:
        print(f"\n--- Phase 0: Bootstrap reinforcements ---")
        result = bootstrap_reinforcements(store, dry_run=dry_run)
        print(f"  Matched: {result['matched']}, Baseline: {result['baseline']}, "
              f"Skipped: {result['skipped']}")

    # Phase 1: Crystallization
    print(f"\n--- Phase 1: Crystallization ---")
    crystals = run_crystallization(store, LifecycleManager(store), dry_run=dry_run)
    print(f"  Crystallized: {len(crystals)} insights")

    # Phase 1.5: Narrative thread building
    print(f"\n--- Phase 1.5: Narrative threads ---")
    if dry_run:
        from core.threads import ThreadDetector
        detector = ThreadDetector(store)
        clusters = detector.detect_threads()
        print(f"  Would narrate {len(clusters)} thread clusters")
    else:
        threads = build_threads(store)
        print(f"  Built {len(threads)} narrative threads")
        for t in threads:
            print(f"    '{t['title']}' ({len(t['member_ids'])} memories)")

    # Phase 2: Relevance maintenance
    print(f"\n--- Phase 2: Relevance maintenance ---")
    maintenance = run_relevance_maintenance(store, project_context, dry_run=dry_run)
    print(f"  Archived: {len(maintenance.get('archived', []))}, "
          f"Rehydrated: {len(maintenance.get('rehydrated', []))}")

    # Phase 3: Self-reflection
    print(f"\n--- Phase 3: Self-reflection ---")
    reflection = run_self_reflection(store, force=force_reflect, dry_run=dry_run)
    if reflection:
        print(f"  Observations: {len(reflection.get('observations', []))}, "
              f"Deprecated: {len(reflection.get('deprecated', []))}")
    else:
        print(f"  Skipped (not due)")

    # Phase 4: Manifest regeneration
    print(f"\n--- Phase 4: Manifest regeneration ---")
    regenerate_manifest(store, dry_run=dry_run)

    # Final health
    if not dry_run:
        print()
        print_health(store, project_context)

    store.close()


if __name__ == "__main__":
    main()
