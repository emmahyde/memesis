#!/usr/bin/env python3
"""
Hourly cron — full memory lifecycle outside any Claude Code session.

Scans ~/.claude/memory/ephemeral/<project-slug>/ for ephemeral buffers with
observations, runs LLM-based consolidation against the single global store,
then runs the full lifecycle: crystallization, thread building, relevance
maintenance, and periodic self-reflection.

LLM transport: the agent-SDK / OAuth path used by interactive Claude Code
sessions, with `claude -p` subprocess as fallback.

Usage:
    python3 /path/to/consolidate_cron.py

Install as cron:
    crontab -e
    7 * * * * /usr/local/bin/python3 /path/to/consolidate_cron.py >> /tmp/memory-consolidation.log 2>&1
"""
import fcntl
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.crystallizer import Crystallizer
from core.database import close_db, get_vec_store, init_db
from core.embeddings import embed_for_memory
from core.feedback import FeedbackLoop
from core.lifecycle import LifecycleManager
from core.manifest import ManifestGenerator
from core.models import Memory, db
from core.relevance import RelevanceEngine
from core.self_reflection import SelfReflector
from core.threads import build_threads

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [consolidate-cron] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Run self-reflection every N consolidations.
REFLECTION_INTERVAL = 5
STALENESS_INTERVAL = 20  # Run staleness detection every 20 cron consolidations (~daily at hourly cron)


def _fts_integrity_or_rebuild() -> None:
    """Run FTS5 integrity-check; rebuild the index if it fails."""
    try:
        db.execute_sql("INSERT INTO memories_fts(memories_fts) VALUES('integrity-check')")
        logger.debug("memories_fts integrity-check: OK")
    except Exception as e:
        logger.warning("memories_fts integrity-check failed: %s — rebuilding", e)
        db.execute_sql("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        logger.warning("memories_fts rebuild complete")


def _assert_fts_sync() -> None:
    """Detect FTS row-count drift vs the base table; rebuild if diverged."""
    m = db.execute_sql("SELECT COUNT(*) FROM memories").fetchone()[0]
    f = db.execute_sql("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
    if m != f:
        logger.warning("FTS row drift: memories=%d fts=%d — rebuilding", m, f)
        db.execute_sql("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        logger.warning("memories_fts rebuild complete after drift detection")


def find_ephemeral_buffers() -> list[Path]:
    """Find all ephemeral session files with actual observations.

    Single global store: buffers stage under
    `~/.claude/memory/ephemeral/<project-slug>/session-*.md`. The slug
    sub-directory carries project identity (see process_buffer).
    """
    ephemeral_root = Path.home() / ".claude" / "memory" / "ephemeral"
    if not ephemeral_root.exists():
        return []

    buffers = []
    for slug_dir in ephemeral_root.iterdir():
        if not slug_dir.is_dir():
            continue
        if not slug_dir.name.startswith("-"):
            logger.warning("Skipping rogue ephemeral directory (no leading dash): %s", slug_dir.name)
            continue

        for session_file in slug_dir.glob("session-*.md"):
            content = session_file.read_text(encoding="utf-8").strip()
            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("# Session")]
            if lines:
                buffers.append(session_file)

    return buffers


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


def process_buffer(ephemeral_path: Path) -> dict | None:
    """Run full lifecycle on a single ephemeral buffer.

    Layout: ~/.claude/memory/ephemeral/<project-slug>/session-*.md
    The slug sub-directory names the project; it is stamped into the
    `project` column via init_db(project=...).
    """
    project_slug = ephemeral_path.parent.name
    lock_path = ephemeral_path.parent / ".lock"
    date_str = ephemeral_path.stem.replace("session-", "")
    header = f"# Session Observations — {date_str}\n\n"

    # --- Lock: snapshot the buffer and clear it ---
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            content = ephemeral_path.read_text(encoding="utf-8")
            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("# Session")]
            if not lines:
                return None
            ephemeral_path.write_text(header, encoding="utf-8")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)

    # --- Unlocked: process the snapshot ---
    snapshot_path = ephemeral_path.parent / f".processing-{ephemeral_path.name}"
    snapshot_path.write_text(content, encoding="utf-8")

    try:
        base_dir = init_db(project=project_slug)

        # --- FTS5 health check before any writes ---
        try:
            _fts_integrity_or_rebuild()
            _assert_fts_sync()
        except Exception as e:
            logger.warning("FTS5 health check error (non-fatal): %s", e)

        lifecycle = LifecycleManager()
        consolidator = Consolidator(lifecycle)
        manifest = ManifestGenerator()
        feedback = FeedbackLoop(lifecycle)
        reflector = SelfReflector()

        session_id = f"cron-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # Project identity is the ephemeral sub-directory slug.
        project_context = project_slug

        # --- Consolidation ---
        obs_in_buffer = sum(
            1 for line in content.splitlines() if line.startswith("## [")
        )
        result = consolidator.consolidate_session(str(snapshot_path), session_id)
        logger.info(
            "Baseline stats: obs_in=%d kept=%d pruned=%d orphaned=%d promoted=%d",
            obs_in_buffer,
            len(result.get("kept", [])),
            len(result.get("pruned", [])),
            len(result.get("orphaned", [])),
            len(result.get("promoted", [])),
        )
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
                logger.warning("Embedding error (non-fatal): %s", e)

        summary_parts = [
            f"Consolidation: {len(result['kept'])} kept, {len(result['pruned'])} pruned"
        ]

        # --- Crystallization ---
        crystallized = []
        try:
            crystallizer = Crystallizer(lifecycle)
            crystallized = crystallizer.crystallize_candidates()
        except Exception as e:
            logger.warning("Crystallization error (non-fatal): %s", e)

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
                logger.warning("Crystal embedding error (non-fatal): %s", e)

        if crystallized:
            summary_parts.append(f"{len(crystallized)} crystallized")

        # --- Narrative threads ---
        threads_built = []
        try:
            threads_built = build_threads()
        except Exception as e:
            logger.warning("Thread building error (non-fatal): %s", e)

        if threads_built:
            summary_parts.append(f"{len(threads_built)} threads")

        # --- Instinctive promotion ---
        instinctive_promoted = 0
        for mem in Memory.by_stage("crystallized"):
            can, reason = lifecycle.can_promote(mem.id)
            if can:
                try:
                    lifecycle.promote(mem.id, f"Auto-promoted to instinctive: {reason}")
                    instinctive_promoted += 1
                except ValueError:
                    pass

        if instinctive_promoted:
            summary_parts.append(f"{instinctive_promoted} -> instinctive")

        # --- Hypothesis reconsolidation ---
        # Check all pending hypotheses against this session's buffer content.
        # Hypothesis memories are ephemeral so they're never injected; this is
        # the only path for them to accumulate or lose evidence in cron mode.
        try:
            from core.reconsolidation import reconsolidate_hypotheses
            hyp_result = reconsolidate_hypotheses(content, session_id)
            if any(hyp_result.values()):
                confirmed_n = len(hyp_result["confirmed"])
                contradicted_n = len(hyp_result["contradicted"])
                logger.info(
                    "Hypothesis reconsolidation: %d confirmed, %d contradicted",
                    confirmed_n,
                    contradicted_n,
                )
                if confirmed_n:
                    summary_parts.append(f"{confirmed_n} hyp confirmed")
                if contradicted_n:
                    summary_parts.append(f"{contradicted_n} hyp contradicted")
        except Exception as e:
            logger.warning("Hypothesis reconsolidation error (non-fatal): %s", e)

        # --- Stored-vs-stored contradiction resolution ---
        try:
            from core.promoter import resolve_contradictions_pass
            cr_result = resolve_contradictions_pass(session_id)
            if any(cr_result.values()):
                parts = [f"{v} {k}" for k, v in cr_result.items() if v]
                summary_parts.append(f"contradictions: {', '.join(parts)}")
        except Exception as e:
            logger.warning("Contradiction resolution error (non-fatal): %s", e)

        # --- Verifier sweep (auto-archive stale memories) ---
        try:
            from core.verifier import run_verifier_sweep
            vr_result = run_verifier_sweep(project_context)
            if vr_result["archived"]:
                summary_parts.append(f"verifier: {vr_result['archived']} archived")
        except Exception as e:
            logger.warning("Verifier sweep error (non-fatal): %s", e)

        # --- Bundled-row decomposer ---
        try:
            from core.decomposer import run_decomposer_sweep
            dc_result = run_decomposer_sweep()
            if dc_result["split"]:
                summary_parts.append(f"decomposer: {dc_result['split']} split")
        except Exception as e:
            logger.warning("Decomposer sweep error (non-fatal): %s", e)

        # --- Rule sync: auto-activate invariant memories as semantic rules ---
        try:
            from core.rules import sync_rules_from_memories
            rs_result = sync_rules_from_memories()
            if rs_result["created"] or rs_result["updated"] or rs_result["deactivated"]:
                summary_parts.append(
                    f"rule-sync: +{rs_result['created']} ~{rs_result['updated']} -{rs_result['deactivated']}"
                )
        except Exception as e:
            logger.warning("Rule sync error (non-fatal): %s", e)

        # --- Rule proposal (candidate guardrails from memories) ---
        try:
            from core.rule_proposal import run_rule_proposal_sweep
            rp_result = run_rule_proposal_sweep()
            if rp_result["proposed"]:
                summary_parts.append(f"rules: {rp_result['proposed']} proposed")
        except Exception as e:
            logger.warning("Rule proposal error (non-fatal): %s", e)

        # --- Relevance maintenance ---
        relevance = RelevanceEngine()
        maint = relevance.run_maintenance(project_context)

        if maint["archived"]:
            summary_parts.append(f"{len(maint['archived'])} archived")
        if maint["rehydrated"]:
            summary_parts.append(f"{len(maint['rehydrated'])} rehydrated")

        # --- Periodic self-reflection ---
        count = _increment_consolidation_count(base_dir)
        if count % REFLECTION_INTERVAL == 0:
            try:
                reflection = reflector.reflect()
                if reflection.get("observations") or reflection.get("deprecated"):
                    reflector.apply_reflection(reflection)
                    summary_parts.append("self-model updated")
            except Exception as e:
                logger.warning("Self-reflection error (non-fatal): %s", e)

        # --- Periodic staleness detection ---
        if count % STALENESS_INTERVAL == 0:
            try:
                from core.llm import call_llm
                from core.models import Observation
                from datetime import timezone, timedelta

                # Pull recent observations as session context
                recent_sessions: set[str] = set()
                for row in Observation.select(Observation.session_id).where(
                    Observation.session_id.is_null(False)
                ).distinct():
                    if row.session_id:
                        recent_sessions.add(row.session_id)
                recent_sessions_list = sorted(recent_sessions, reverse=True)[:5]

                obs_parts = []
                if recent_sessions_list:
                    obs_rows = list(
                        Observation.select()
                        .where(Observation.session_id.in_(recent_sessions_list))
                        .order_by(Observation.created_at.desc())
                        .limit(20)
                    )
                    obs_parts = [
                        (o.filtered_content or o.content or "").strip()[:200]
                        for o in obs_rows
                        if (o.filtered_content or o.content or "").strip()
                    ]
                observations_text = "\n".join(f"- {p}" for p in obs_parts[:15]) or "(no recent observations)"

                # Find stale candidates
                cutoff_days = 30
                cutoff_ts = (
                    datetime.now(timezone.utc) - timedelta(days=cutoff_days)
                ).isoformat()
                candidates = list(
                    Memory.select()
                    .where(
                        Memory.stage.in_(["consolidated", "crystallized"]),
                        Memory.importance <= 0.8,
                        Memory.archived_at.is_null(True),
                    )
                    .order_by(Memory.importance.asc())
                    .limit(5)
                )

                stale_found = 0
                for mem in candidates:
                    last_activity = mem.last_used_at or mem.created_at or ""
                    if last_activity and last_activity >= cutoff_ts:
                        continue  # recently active, skip
                    prompt = (
                        f"Memory to evaluate:\nTitle: {mem.title or '(untitled)'}\n"
                        f"Stage: {mem.stage}\nContent: {(mem.content or '')[:400]}\n\n"
                        f"Recent observations:\n{observations_text}\n\n"
                        "Is this memory VALID, OUTDATED, or UNCERTAIN given the recent observations? "
                        "Reply with JSON: {\"verdict\": \"VALID|OUTDATED|UNCERTAIN\", \"reasoning\": \"one sentence\"}"
                    )
                    try:
                        import json as _json
                        raw = call_llm(
                            prompt,
                            model="claude-haiku-4-5-20251001",
                            max_tokens=100,
                        )
                        result_dict = _json.loads(raw.strip().strip("```json").strip("```"))
                        if result_dict.get("verdict") == "OUTDATED":
                            new_imp = max(0.0, round((mem.importance or 0.5) - 0.15, 3))
                            Memory.update(importance=new_imp).where(Memory.id == mem.id).execute()
                            stale_found += 1
                            logger.info(
                                "Staleness: %s importance %.2f→%.2f (%s)",
                                (mem.title or mem.id)[:40],
                                mem.importance or 0.5,
                                new_imp,
                                result_dict.get("reasoning", "")[:60],
                            )
                    except Exception:
                        pass  # per-memory errors are non-fatal

                if stale_found:
                    summary_parts.append(f"{stale_found} stale decayed")
            except Exception as e:
                logger.warning("Staleness detection error (non-fatal): %s", e)

        # --- Manifest ---
        manifest.write_manifest()
        close_db()

        logger.info("  %s", ", ".join(summary_parts))
        snapshot_path.unlink(missing_ok=True)
        return result
    except Exception:
        # Restore the snapshot content back to the ephemeral buffer so the
        # next cron run gets a chance to reprocess. Without this, a single
        # LLM failure permanently drops every observation in the snapshot
        # (the buffer was cleared under lock before consolidation ran).
        try:
            with open(lock_path, "w") as lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    current = ephemeral_path.read_text(encoding="utf-8") \
                        if ephemeral_path.exists() else header
                    # Append snapshot body (minus header) so concurrent writes survive.
                    body = "\n".join(
                        line for line in content.splitlines()
                        if line.strip() and not line.startswith("# Session")
                    )
                    ephemeral_path.write_text(
                        current.rstrip() + "\n" + body + "\n",
                        encoding="utf-8",
                    )
                    logger.warning(
                        "Restored %d byte(s) to ephemeral buffer after failure",
                        len(body),
                    )
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception as restore_exc:
            logger.exception(
                "CRITICAL: snapshot restore failed; observations lost: %s",
                restore_exc,
            )
        snapshot_path.unlink(missing_ok=True)
        raise


def main():
    logger.info("Starting hourly lifecycle run")

    # --- Step 0: Extract observations from recent session transcripts ---
    # Runs BEFORE finding ephemeral buffers so freshly-extracted observations
    # are included in this cron's consolidation pass.
    try:
        from core.transcript_ingest import tick as _transcript_tick
        tick_results = _transcript_tick(max_sessions=10)
        if tick_results["observations_total"] > 0 or tick_results["processed"] > 0:
            logger.info(
                "Transcript extraction: %d session(s) processed, %d observation(s) appended, %d skipped",
                tick_results["processed"],
                tick_results["observations_total"],
                tick_results["skipped"],
            )
    except Exception as e:
        logger.warning("Transcript extraction error (non-fatal): %s", e)

    buffers = sorted(find_ephemeral_buffers())
    if not buffers:
        logger.info("No ephemeral buffers with observations found")
        return

    logger.info("Found %d buffer(s) with observations", len(buffers))

    failed_buffers = 0
    for buf in buffers:
        project_hash = buf.parent.parent.parent.name
        logger.info("Processing %s (project: %s)", buf.name, project_hash)
        try:
            result = process_buffer(buf)
            if result is None:
                logger.info("  (empty after lock — skipped)")
        except Exception as exc:
            failed_buffers += 1
            logger.exception("  Failed to process %s", buf)
            # Persist a structured error record so db_check / observability
            # can surface failed cron runs that would otherwise vanish into
            # stderr. Without this, 5 consecutive failures in production
            # (cron-20260508-*) had no on-disk trace.
            try:
                import json as _json
                from datetime import datetime as _dt
                meta_dir = buf.parent.parent / "meta"
                meta_dir.mkdir(parents=True, exist_ok=True)
                err_path = meta_dir / "consolidation-errors.jsonl"
                record = {
                    "ts": _dt.now().isoformat(),
                    "buffer": str(buf),
                    "project": project_hash,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
                with err_path.open("a", encoding="utf-8") as f:
                    f.write(_json.dumps(record) + "\n")
            except Exception:
                pass  # error-logging is best-effort

    logger.info("Lifecycle run complete")
    if failed_buffers:
        logger.error(
            "Lifecycle run completed with %d buffer failure(s)", failed_buffers
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
