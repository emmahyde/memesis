#!/usr/bin/env python3
"""SessionStart hook — injects memory context into Claude Code sessions.

On first run, seeds the instinctive layer (self-model + observation habit).
On every run, checks for archived memories worth rehydrating for this project
context, then injects the three-tier memory context.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import get_base_dir, init_db
from core.ingest import NativeMemoryIngestor
from core.relevance import RelevanceEngine
from core.retrieval import RetrievalEngine
from core.self_reflection import SelfReflector


def create_ephemeral_buffer(base_dir: Path) -> Path:
    """Create a fresh ephemeral session buffer."""
    timestamp = datetime.now().strftime("%Y-%m-%d")
    buffer_path = base_dir / "ephemeral" / f"session-{timestamp}.md"
    buffer_path.parent.mkdir(parents=True, exist_ok=True)
    if not buffer_path.exists():
        buffer_path.write_text(f"# Session Observations — {timestamp}\n\n")
    return buffer_path


def main():
    try:
        session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
        project_context = os.getcwd()

        base_dir = init_db(project_context=project_context)

        # Seed instinctive layer on first run (idempotent — skips if already seeded)
        reflector = SelfReflector()
        reflector.ensure_instinctive_layer()

        # Ingest native Claude Code memories (deduplicates automatically)
        ingestor = NativeMemoryIngestor()
        ingestor.ingest(project_context)

        # Rehydrate archived memories relevant to this project context
        relevance = RelevanceEngine()
        relevance.rehydrate_for_context(project_context)

        # Inject memory context
        retriever = RetrievalEngine()
        injected = retriever.inject_for_session(session_id, project_context)
        create_ephemeral_buffer(base_dir)

        print(injected)
    except Exception:
        # Never crash the session
        print("")
        sys.exit(0)


if __name__ == "__main__":
    main()
