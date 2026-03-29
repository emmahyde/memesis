#!/usr/bin/env python3
"""PreCompact hook — sleep consolidation for memory lifecycle.

Uses the same lock-snapshot-clear pattern as consolidate_cron.py to prevent
double-processing if both run simultaneously.

Every REFLECTION_INTERVAL consolidations, runs self-reflection to update
the self-model with patterns observed in the consolidation log.
"""
import fcntl
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.crystallizer import Crystallizer
from core.embeddings import embed_for_memory
from core.feedback import FeedbackLoop
from core.lifecycle import LifecycleManager
from core.manifest import ManifestGenerator
from core.relevance import RelevanceEngine
from core.self_reflection import SelfReflector
from core.storage import MemoryStore
from core.threads import build_threads

# Run self-reflection every N consolidations.
REFLECTION_INTERVAL = 5


def _get_consolidation_count(store: MemoryStore) -> int:
    """Read the consolidation counter from meta/consolidation-count.json."""
    counter_path = store.base_dir / "meta" / "consolidation-count.json"
    if counter_path.exists():
        try:
            data = json.loads(counter_path.read_text())
            return data.get("count", 0)
        except (json.JSONDecodeError, OSError):
            pass
    return 0


def _increment_consolidation_count(store: MemoryStore) -> int:
    """Increment and persist the consolidation counter. Returns new count."""
    counter_path = store.base_dir / "meta" / "consolidation-count.json"
    count = _get_consolidation_count(store) + 1
    counter_path.write_text(json.dumps({"count": count}))
    return count


def main():
    try:
        session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
        project_context = os.getcwd()
        today = datetime.now().strftime("%Y-%m-%d")

        store = MemoryStore(project_context=project_context)
        lifecycle = LifecycleManager(store)
        consolidator = Consolidator(store, lifecycle)
        manifest = ManifestGenerator(store)
        feedback = FeedbackLoop(store, lifecycle)

        # Ensure instinctive layer is seeded (self-model + observation habit)
        reflector = SelfReflector(store)
        reflector.ensure_instinctive_layer()

        ephemeral_path = store.base_dir / "ephemeral" / f"session-{today}.md"
        if not ephemeral_path.exists():
            print("", flush=True)
            return

        # --- Lock: snapshot the buffer and clear it ---
        lock_path = ephemeral_path.parent / ".lock"
        header = f"# Session Observations — {today}\n\n"

        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                content = ephemeral_path.read_text(encoding="utf-8")
                lines = [l for l in content.splitlines() if l.strip() and not l.startswith("# Session")]
                if not lines:
                    print("", flush=True)
                    return
                ephemeral_path.write_text(header, encoding="utf-8")
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

        # --- Unlocked: process the snapshot ---
        snapshot_path = ephemeral_path.parent / f".processing-{ephemeral_path.name}"
        snapshot_path.write_text(content, encoding="utf-8")

        # Read conversation context from stdin (Claude Code may pipe it)
        conversation_text = ""
        if not sys.stdin.isatty():
            try:
                conversation_text = sys.stdin.read()
            except Exception:
                pass

        try:
            # Track usage BEFORE consolidation so importance scores reflect it
            injected_ids = store.get_session_injections(session_id)
            if injected_ids:
                # Use conversation text + ephemeral content as usage signal
                usage_text = conversation_text + "\n" + content
                feedback.track_usage(session_id, injected_ids, usage_text)

            result = consolidator.consolidate_session(str(snapshot_path), session_id)
            feedback.update_importance_scores(session_id)

            # Embed newly kept memories
            for memory_id in result.get("kept", []):
                try:
                    mem = store.get(memory_id)
                    embedding = embed_for_memory(
                        mem.get("title", ""),
                        mem.get("summary", ""),
                        mem.get("content", ""),
                    )
                    if embedding:
                        store.store_embedding(memory_id, embedding)
                except Exception as e:
                    print(f"Embedding error (non-fatal): {e}", file=sys.stderr)

            # Crystallization: synthesize promotion candidates into higher-level insights
            crystallized = []
            try:
                crystallizer = Crystallizer(store, lifecycle)
                crystallized = crystallizer.crystallize_candidates()
            except Exception as e:
                print(f"Crystallization error (non-fatal): {e}", file=sys.stderr)

            for crystal in crystallized:
                try:
                    cid = crystal.get("crystallized_id")
                    if cid:
                        mem = store.get(cid)
                        embedding = embed_for_memory(
                            mem.get("title", ""),
                            crystal.get("insight", ""),
                        )
                        if embedding:
                            store.store_embedding(cid, embedding)
                except Exception as e:
                    print(f"Crystal embedding error (non-fatal): {e}", file=sys.stderr)

            # Narrative threads: detect and synthesize episodic arcs
            threads_built = []
            try:
                threads_built = build_threads(store)
            except Exception as e:
                print(f"Thread building error (non-fatal): {e}", file=sys.stderr)

            # Instinctive promotion: crystallized memories that earned it
            instinctive_promoted = 0
            for mem in store.list_by_stage("crystallized"):
                can, reason = lifecycle.can_promote(mem["id"])
                if can:
                    try:
                        lifecycle.promote(mem["id"], f"Auto-promoted to instinctive: {reason}")
                        instinctive_promoted += 1
                    except ValueError:
                        pass

            # Run relevance maintenance — archive stale, rehydrate relevant
            relevance = RelevanceEngine(store)
            maint = relevance.run_maintenance(project_context)

            # Periodic self-reflection
            count = _increment_consolidation_count(store)
            reflected = False
            if count % REFLECTION_INTERVAL == 0:
                try:
                    reflection = reflector.reflect()
                    if reflection.get("observations") or reflection.get("deprecated"):
                        reflector.apply_reflection(reflection)
                        reflected = True
                except Exception as e:
                    print(f"Self-reflection error (non-fatal): {e}", file=sys.stderr)

            manifest.write_manifest()

            summary = f"Consolidation: {len(result['kept'])} kept, {len(result['pruned'])} pruned"
            if injected_ids:
                session_usage = getattr(feedback, '_session_usage', {}).get(session_id, {})
                used_count = sum(1 for v in session_usage.values() if v)
                summary += f", {used_count}/{len(injected_ids)} memories used"
            if crystallized:
                summary += f", {len(crystallized)} crystallized"
            if threads_built:
                summary += f", {len(threads_built)} threads"
            if instinctive_promoted:
                summary += f", {instinctive_promoted} → instinctive"
            if maint["archived"]:
                summary += f", {len(maint['archived'])} archived"
            if maint["rehydrated"]:
                summary += f", {len(maint['rehydrated'])} rehydrated"
            if reflected:
                summary += ", self-model updated"
            print(summary, file=sys.stderr)
        finally:
            snapshot_path.unlink(missing_ok=True)
            store.close()

        print("", flush=True)  # stdout must be empty for Claude Code

    except Exception as e:
        print(f"PreCompact error: {e}", file=sys.stderr)
        print("", flush=True)


if __name__ == "__main__":
    main()
