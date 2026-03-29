#!/usr/bin/env python3
"""
Hourly cron — full memory lifecycle outside any Claude Code session.

Scans all project memory directories for ephemeral buffers with observations,
runs LLM-based consolidation via Bedrock, then runs the full lifecycle:
crystallization, thread building, relevance maintenance, and periodic
self-reflection.

Usage:
    python3 /path/to/consolidate_cron.py

Install as cron:
    crontab -e
    7 * * * * /usr/local/bin/python3 /path/to/consolidate_cron.py >> /tmp/memory-consolidation.log 2>&1
"""
import fcntl
import json
import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [consolidate-cron] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Ensure Bedrock credentials are available
os.environ.setdefault("AWS_PROFILE", "bedrock-users")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("CLAUDE_CODE_USE_BEDROCK", "true")

# Run self-reflection every N consolidations.
REFLECTION_INTERVAL = 5


def find_ephemeral_buffers() -> list[Path]:
    """Find all ephemeral session files with actual observations.

    Only scans directories that match Claude Code's naming convention
    (leading dash from path hashing). Directories without a leading dash
    are rogue stores created by bugs and are skipped.
    """
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return []

    buffers = []
    for memory_dir in projects_dir.glob("*/memory/ephemeral"):
        # Claude Code project dirs always start with '-' (from leading '/' in paths).
        # Skip rogue directories that lack this prefix.
        project_dir_name = memory_dir.parent.parent.name
        if not project_dir_name.startswith("-"):
            logger.warning("Skipping rogue project directory (no leading dash): %s", project_dir_name)
            continue

        for session_file in memory_dir.glob("session-*.md"):
            content = session_file.read_text(encoding="utf-8").strip()
            # Skip empty buffers (just the header line)
            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("# Session")]
            if lines:
                buffers.append(session_file)

    return buffers


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
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    count = _get_consolidation_count(store) + 1
    counter_path.write_text(json.dumps({"count": count}))
    return count


def process_buffer(ephemeral_path: Path) -> dict | None:
    """Run full lifecycle on a single ephemeral buffer.

    1. Lock, snapshot, clear the buffer
    2. Consolidate (LLM call via Bedrock)
    3. Crystallize promotion candidates
    4. Build narrative threads
    5. Promote crystallized → instinctive if eligible
    6. Relevance maintenance (archive stale, rehydrate relevant)
    7. Periodic self-reflection
    8. Write manifest
    """
    memory_dir = ephemeral_path.parent.parent  # up from ephemeral/ to memory/
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
        store = MemoryStore(base_dir=str(memory_dir))
        lifecycle = LifecycleManager(store)
        consolidator = Consolidator(store, lifecycle)
        manifest = ManifestGenerator(store)
        feedback = FeedbackLoop(store, lifecycle)
        reflector = SelfReflector(store)

        session_id = f"cron-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # Derive project context from memory_dir path
        project_context = str(memory_dir.parent)

        # --- Consolidation ---
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
                logger.warning("Embedding error (non-fatal): %s", e)

        summary_parts = [
            f"Consolidation: {len(result['kept'])} kept, {len(result['pruned'])} pruned"
        ]

        # --- Crystallization ---
        crystallized = []
        try:
            crystallizer = Crystallizer(store, lifecycle)
            crystallized = crystallizer.crystallize_candidates()
        except Exception as e:
            logger.warning("Crystallization error (non-fatal): %s", e)

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
                logger.warning("Crystal embedding error (non-fatal): %s", e)

        if crystallized:
            summary_parts.append(f"{len(crystallized)} crystallized")

        # --- Narrative threads ---
        threads_built = []
        try:
            threads_built = build_threads(store)
        except Exception as e:
            logger.warning("Thread building error (non-fatal): %s", e)

        if threads_built:
            summary_parts.append(f"{len(threads_built)} threads")

        # --- Instinctive promotion ---
        instinctive_promoted = 0
        for mem in store.list_by_stage("crystallized"):
            can, reason = lifecycle.can_promote(mem["id"])
            if can:
                try:
                    lifecycle.promote(mem["id"], f"Auto-promoted to instinctive: {reason}")
                    instinctive_promoted += 1
                except ValueError:
                    pass

        if instinctive_promoted:
            summary_parts.append(f"{instinctive_promoted} → instinctive")

        # --- Relevance maintenance ---
        relevance = RelevanceEngine(store)
        maint = relevance.run_maintenance(project_context)

        if maint["archived"]:
            summary_parts.append(f"{len(maint['archived'])} archived")
        if maint["rehydrated"]:
            summary_parts.append(f"{len(maint['rehydrated'])} rehydrated")

        # --- Periodic self-reflection ---
        count = _increment_consolidation_count(store)
        if count % REFLECTION_INTERVAL == 0:
            try:
                reflection = reflector.reflect()
                if reflection.get("observations") or reflection.get("deprecated"):
                    reflector.apply_reflection(reflection)
                    summary_parts.append("self-model updated")
            except Exception as e:
                logger.warning("Self-reflection error (non-fatal): %s", e)

        # --- Manifest ---
        manifest.write_manifest()
        store.close()

        logger.info("  %s", ", ".join(summary_parts))
        return result
    finally:
        snapshot_path.unlink(missing_ok=True)


def main():
    logger.info("Starting hourly lifecycle run")

    buffers = sorted(find_ephemeral_buffers())
    if not buffers:
        logger.info("No ephemeral buffers with observations found")
        return

    logger.info("Found %d buffer(s) with observations", len(buffers))

    for buf in buffers:
        project_hash = buf.parent.parent.parent.name
        logger.info("Processing %s (project: %s)", buf.name, project_hash)
        try:
            result = process_buffer(buf)
            if result is None:
                logger.info("  (empty after lock — skipped)")
        except Exception:
            logger.exception("  Failed to process %s", buf)

    logger.info("Lifecycle run complete")


if __name__ == "__main__":
    main()
