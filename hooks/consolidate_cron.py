#!/usr/bin/env python3
"""
Hourly cron consolidation — runs headless outside any Claude Code session.

Scans all project memory directories for ephemeral buffers with observations,
runs LLM-based consolidation via Bedrock, and writes consolidated memories.

Usage:
    python3 /path/to/consolidate_cron.py

Install as cron:
    crontab -e
    7 * * * * /usr/local/bin/python3 /path/to/consolidate_cron.py >> /tmp/memory-consolidation.log 2>&1
"""
import fcntl
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.feedback import FeedbackLoop
from core.lifecycle import LifecycleManager
from core.manifest import ManifestGenerator
from core.storage import MemoryStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [consolidate-cron] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Ensure Bedrock credentials are available
os.environ.setdefault("AWS_PROFILE", "bedrock-users")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("CLAUDE_CODE_USE_BEDROCK", "true")


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


def consolidate_buffer(ephemeral_path: Path) -> dict | None:
    """Run consolidation on a single ephemeral buffer.

    Uses file locking to coordinate with the Stop hook's append_observation.py.
    The lock is held only during read+clear (fast), then released before the
    slow Bedrock call so the Stop hook isn't blocked.
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
            # Check if there's anything beyond the header
            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("# Session")]
            if not lines:
                return None
            # Clear the buffer so new observations go to a fresh file
            ephemeral_path.write_text(header, encoding="utf-8")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)

    # --- Unlocked: process the snapshot (slow Bedrock call) ---
    # Write snapshot to a temp file for the consolidator to read
    snapshot_path = ephemeral_path.parent / f".processing-{ephemeral_path.name}"
    snapshot_path.write_text(content, encoding="utf-8")

    try:
        store = MemoryStore(base_dir=str(memory_dir))
        lifecycle = LifecycleManager(store)
        consolidator = Consolidator(store, lifecycle)
        manifest = ManifestGenerator(store)
        feedback = FeedbackLoop(store, lifecycle)

        session_id = f"cron-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        result = consolidator.consolidate_session(str(snapshot_path), session_id)
        feedback.update_importance_scores(session_id)

        for mem in lifecycle.get_promotion_candidates():
            try:
                lifecycle.promote(mem["id"], "Auto-promoted: meets reinforcement threshold")
            except ValueError:
                pass

        manifest.write_manifest()
        return result
    finally:
        snapshot_path.unlink(missing_ok=True)


def main():
    logger.info("Starting hourly consolidation run")

    buffers = sorted(find_ephemeral_buffers())
    if not buffers:
        logger.info("No ephemeral buffers with observations found")
        return

    logger.info("Found %d buffer(s) with observations", len(buffers))

    for buf in buffers:
        project_hash = buf.parent.parent.parent.name
        logger.info("Consolidating %s (project: %s)", buf.name, project_hash)
        try:
            result = consolidate_buffer(buf)
            if result:
                logger.info(
                    "  Result: %d kept, %d pruned, %d promoted, %d conflicts",
                    len(result["kept"]),
                    len(result["pruned"]),
                    len(result["promoted"]),
                    len(result["conflicts"]),
                )
        except Exception:
            logger.exception("  Failed to consolidate %s", buf)

    logger.info("Consolidation run complete")


if __name__ == "__main__":
    main()
