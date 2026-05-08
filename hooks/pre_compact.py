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

from hooks._safe import emit_stderr, emit_stdout

from core.affect import load_analyzer
from core.consolidator import Consolidator
from core.crystallizer import Crystallizer
from core.database import close_db, get_base_dir, get_vec_store, init_db
from core.embeddings import embed_for_memory
from core.feedback import FeedbackLoop
from core.lifecycle import LifecycleManager
from core.manifest import ManifestGenerator
from core.models import Memory, RetrievalLog
from core.relevance import RelevanceEngine
from core.self_reflection import SelfReflector
from core.threads import build_threads

# Run self-reflection every N consolidations.
REFLECTION_INTERVAL = 5


def _get_consolidation_count(base_dir: Path) -> int:
    """Read the consolidation counter from meta/consolidation-count.json."""
    counter_path = base_dir / "meta" / "consolidation-count.json"
    if counter_path.exists():
        try:
            data = json.loads(counter_path.read_text())
            return data.get("count", 0)
        except (json.JSONDecodeError, OSError):
            pass
    return 0


def _increment_consolidation_count(base_dir: Path) -> int:
    """Increment and persist the consolidation counter. Returns new count."""
    counter_path = base_dir / "meta" / "consolidation-count.json"
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    count = _get_consolidation_count(base_dir) + 1
    counter_path.write_text(json.dumps({"count": count}))
    return count


def main():
    try:
        session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
        project_context = os.getcwd()
        today = datetime.now().strftime("%Y-%m-%d")

        base_dir = init_db(project_context=project_context)

        # Load session affect state — available before reconsolidation and
        # thread-building so later waves can pass it to those subsystems.
        # Non-fatal: missing or corrupt affect file yields a neutral AffectState.
        session_affect = None
        try:
            analyzer = load_analyzer(base_dir, session_id)
            session_affect = analyzer.current_state()
        except Exception as e:
            emit_stderr(f"Affect load error (non-fatal): {e}")

        lifecycle = LifecycleManager()
        consolidator = Consolidator(lifecycle)
        manifest = ManifestGenerator()
        feedback = FeedbackLoop(lifecycle)

        # Ensure instinctive layer is seeded (self-model + observation habit)
        reflector = SelfReflector()
        reflector.ensure_instinctive_layer()

        ephemeral_path = base_dir / "ephemeral" / f"session-{today}.md"
        if not ephemeral_path.exists():
            emit_stdout("")
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
                    emit_stdout("")
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
            injected_ids = [
                r.memory_id for r in
                RetrievalLog.select(RetrievalLog.memory_id)
                .where(RetrievalLog.session_id == session_id)
                .distinct()
            ]
            if injected_ids:
                # Use conversation text + ephemeral content as usage signal
                usage_text = conversation_text + "\n" + content
                feedback.track_usage(session_id, injected_ids, usage_text)

                # Reconsolidate: check if session confirms/contradicts/refines memories
                try:
                    from core.reconsolidation import reconsolidate
                    # Pass session_affect as a plain dict so reconsolidation can
                    # embed affect signals into edge metadata without importing
                    # core.affect (avoids circular import risk in the hook layer).
                    affect_dict = None
                    if session_affect is not None:
                        import dataclasses
                        affect_dict = dataclasses.asdict(session_affect)
                    recon = reconsolidate(injected_ids, usage_text, session_id,
                                          session_affect=affect_dict)
                    if any(recon.values()):
                        emit_stderr(
                            f"Reconsolidation: {len(recon['confirmed'])} confirmed,"
                            f" {len(recon['contradicted'])} contradicted,"
                            f" {len(recon['refined'])} refined"
                        )
                except Exception as e:
                    emit_stderr(f"Reconsolidation error (non-fatal): {e}")

                # Hypothesis matching: check ALL pending hypotheses against session,
                # not just injected ones (ephemeral hypotheses are never injected).
                try:
                    from core.reconsolidation import reconsolidate_hypotheses
                    hyp_recon = reconsolidate_hypotheses(usage_text, session_id)
                    if any(hyp_recon.values()):
                        emit_stderr(
                            f"Hypothesis reconsolidation: {len(hyp_recon['confirmed'])} confirmed,"
                            f" {len(hyp_recon['contradicted'])} contradicted"
                        )
                except Exception as e:
                    emit_stderr(f"Hypothesis reconsolidation error (non-fatal): {e}")

            result = consolidator.consolidate_session(str(snapshot_path), session_id)
            feedback.update_importance_scores(session_id)

            vec_store = get_vec_store()

            # Embed newly kept memories
            for memory_id in result.get("kept", []):
                try:
                    mem = Memory.get_by_id(memory_id)
                    embedding = embed_for_memory(
                        mem.title or "",
                        mem.summary or "",
                        mem.content or "",
                    )
                    if embedding and vec_store:
                        vec_store.store_embedding(memory_id, embedding)
                except Exception as e:
                    emit_stderr(f"Embedding error (non-fatal): {e}")

            # Crystallization: synthesize promotion candidates into higher-level insights
            crystallized = []
            try:
                crystallizer = Crystallizer(lifecycle)
                crystallized = crystallizer.crystallize_candidates()
            except Exception as e:
                emit_stderr(f"Crystallization error (non-fatal): {e}")

            for crystal in crystallized:
                try:
                    cid = crystal.get("crystallized_id")
                    if cid:
                        mem = Memory.get_by_id(cid)
                        embedding = embed_for_memory(
                            mem.title or "",
                            crystal.get("insight", ""),
                        )
                        if embedding and vec_store:
                            vec_store.store_embedding(cid, embedding)
                except Exception as e:
                    emit_stderr(f"Crystal embedding error (non-fatal): {e}")

            # Narrative threads: detect and synthesize episodic arcs
            threads_built = []
            try:
                threads_built = build_threads()
            except Exception as e:
                emit_stderr(f"Thread building error (non-fatal): {e}")

            # Instinctive promotion: crystallized memories that earned it
            instinctive_promoted = 0
            for mem in Memory.by_stage("crystallized"):
                can, reason = lifecycle.can_promote(mem.id)
                if can:
                    try:
                        lifecycle.promote(mem.id, f"Auto-promoted to instinctive: {reason}")
                        instinctive_promoted += 1
                    except ValueError:
                        pass

            # Run relevance maintenance — archive stale, rehydrate relevant
            relevance = RelevanceEngine()
            maint = relevance.run_maintenance(project_context)

            # Periodic self-reflection
            count = _increment_consolidation_count(base_dir)
            reflected = False
            if count % REFLECTION_INTERVAL == 0:
                try:
                    reflection = reflector.reflect()
                    if reflection.get("observations") or reflection.get("deprecated"):
                        reflector.apply_reflection(reflection)
                        reflected = True
                except Exception as e:
                    emit_stderr(f"Self-reflection error (non-fatal): {e}")

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
                summary += f", {instinctive_promoted} -> instinctive"
            if maint["archived"]:
                summary += f", {len(maint['archived'])} archived"
            if maint["rehydrated"]:
                summary += f", {len(maint['rehydrated'])} rehydrated"
            if reflected:
                summary += ", self-model updated"
            emit_stderr(summary)
        finally:
            snapshot_path.unlink(missing_ok=True)
            close_db()

        emit_stdout("")  # stdout must be empty for Claude Code

    except Exception as e:
        emit_stderr(f"PreCompact error: {e}")
        emit_stdout("")


if __name__ == "__main__":
    main()
