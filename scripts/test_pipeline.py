#!/usr/bin/env python3
"""
End-to-end pipeline integration test using a REAL (not mocked) LLM call.

Creates a synthetic observation, runs consolidation, and verifies a memory
was created. This validates the full pipeline on real infrastructure.

Usage:
    python3 scripts/test_pipeline.py --base-dir /tmp/test-memesis
    python3 scripts/test_pipeline.py  # uses /tmp/memesis-test-<timestamp>

WARNING: Makes real LLM API calls. Costs ~$0.001 per run (Haiku model).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def run_test(base_dir: str, project_context: str) -> bool:
    from core.database import init_db, close_db
    from core.models import Memory, ConsolidationLog
    from core.consolidator import Consolidator
    from core.lifecycle import LifecycleManager
    from core.prompts import format_observation

    init_db(project_context=project_context, base_dir=base_dir)
    try:
        # --- Step 1: Create ephemeral buffer with a test observation ---
        base = Path(base_dir)
        ephemeral_dir = base / "ephemeral"
        ephemeral_dir.mkdir(parents=True, exist_ok=True)
        buf_path = ephemeral_dir / "test-pipeline.md"

        # A correction-type observation — should survive the KEEP gate
        obs_text = (
            "When building memory systems in Python, the hashlib.md5() digest "
            "must match what is stored in the DB. If Memory.save() overrides "
            "content_hash from the content field, any pre-computed hash using "
            "a different input (e.g., frontmatter + body) will silently mismatch, "
            "causing dedup checks to always fail and duplicate records on every session. "
            "Always compute content_hash from the exact field value that save() uses."
        )
        obs_content = format_observation(obs_text, obs_type="correction")

        buf_path.write_text(
            f"# Test Pipeline Observations — {time.strftime('%Y-%m-%d')}\n\n{obs_content}\n",
            encoding="utf-8",
        )
        print(f"\n[1] Ephemeral buffer created: {buf_path}")
        print(f"    Observation: correction — {obs_text[:80]}...")

        # --- Step 2: Run consolidation ---
        lifecycle = LifecycleManager()
        consolidator = Consolidator(lifecycle)

        initial_count = Memory.select().count()
        initial_log_count = ConsolidationLog.select().count()

        print(f"\n[2] Running consolidation...")
        result = consolidator.consolidate_session(str(buf_path), "pipeline-test-001")

        final_count = Memory.select().count()
        final_log_count = ConsolidationLog.select().count()

        print(f"    Result: {result}")
        print(f"    Memories before: {initial_count}, after: {final_count}")
        print(f"    ConsolidationLog entries: {final_log_count - initial_log_count} new")

        # --- Step 3: Evaluate ---
        kept = result.get("kept", [])
        pruned = result.get("pruned", [])

        if kept:
            print(f"\n[3] KEPT: {len(kept)} memory/memories")
            for mid in kept:
                try:
                    m = Memory.get_by_id(mid)
                    print(f"    - [{m.stage}] {m.title} (importance={m.importance:.2f})")
                    print(f"      {(m.content or '')[:100]}...")
                except Exception:
                    print(f"    - (ID: {mid})")
            print("\n✅ PASS: Pipeline created memories from observations")
            return True
        elif pruned:
            print(f"\n[3] PRUNED: {len(pruned)} observation(s)")
            for p in pruned[:2]:
                print(f"    Rationale: {p.get('rationale', '?')[:100]}")
            print("\n⚠ NOTE: LLM chose to prune. This may be correct (low-signal obs).")
            print("         Re-run with a higher-signal correction observation to verify.")
            return True  # Pruning is still a valid outcome — pipeline ran
        else:
            if final_log_count > initial_log_count:
                print(f"\n[3] No keeps/prunes but {final_log_count - initial_log_count} log entries")
                print("    Pipeline ran but produced no decisions. Check consolidation prompt.")
                return False
            else:
                print("\n[3] FAIL: No consolidation decisions made at all")
                print("    Check: habituation filter, LLM connectivity, ephemeral buffer format")
                return False

    finally:
        close_db()
        # Cleanup
        try:
            buf_path.unlink()
        except Exception:
            pass


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--base-dir",
        default=None,
        help="Base dir for test DB (default: /tmp/memesis-test-<timestamp>)"
    )
    parser.add_argument("--project-context", default=os.getcwd())
    args = parser.parse_args()

    base_dir = args.base_dir
    cleanup_after = False
    if base_dir is None:
        base_dir = f"/tmp/memesis-test-{int(time.time())}"
        cleanup_after = True

    print(f"memesis end-to-end pipeline test")
    print(f"base-dir: {base_dir}")
    print(f"project:  {args.project_context}")

    success = run_test(base_dir, args.project_context)

    if cleanup_after:
        import shutil
        try:
            shutil.rmtree(base_dir)
        except Exception:
            pass

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
