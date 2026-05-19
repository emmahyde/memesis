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

from hooks._safe import emit_context, emit_stderr, emit_stdout
from hooks._render import build_role_line, render_index

from core.database import get_project, init_db
from core.ingest import NativeMemoryIngestor
from core.models import Memory, MemoryEdge
from core.relevance import RelevanceEngine
from core.retrieval import RetrievalEngine
from core.rules import active_rules
from core.self_reflection import SelfReflector


# Cap on memories pulled into the panel — the renderer trims further to budget.
_PANEL_MEMORY_LIMIT = 80


def _build_panel() -> str:
    """Gather memories, rules, and session digests into the SessionStart panel.

    Raises on any failure — the caller falls back to the plain injection text.
    Project scope comes from get_project(), set by the earlier init_db() call.
    """
    project = get_project()

    query = Memory.select().where(
        Memory.archived_at.is_null(),
        Memory.stage.in_(["consolidated", "crystallized", "instinctive"]),
    )
    if project:
        # project is a soft filter, not a hard partition — global (NULL)
        # memories surface in every project.
        query = query.where(
            (Memory.project == project) | Memory.project.is_null()
        )
    mems = list(query.order_by(Memory.created_at.desc()).limit(_PANEL_MEMORY_LIMIT))

    ids = [m.id for m in mems]
    edges = []
    if ids:
        for e in MemoryEdge.select().where(
            MemoryEdge.source_id.in_(ids) | MemoryEdge.target_id.in_(ids)
        ):
            edges.append((e.source_id, e.target_id, e.edge_type))

    # Role line from the self-model instinctive memory (best-effort).
    role = None
    try:
        self_model = SelfReflector()._find_self_model()
        if self_model:
            role = (self_model.summary or "").strip() or None
    except Exception:
        role = None

    # WATCH — recent gotchas and corrections.
    watch = [
        m.title for m in mems
        if (m.memory_kind == "gotcha" or m.kind == "correction")
    ][:3]

    return render_index(
        mems,
        edges=edges,
        rules=active_rules(project),
        role=build_role_line(role),
        watch=watch,
    )


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

        # Inject memory context. inject_for_session() is still called for its
        # retrieval bookkeeping (injection counts, spaced-repetition schedule)
        # and as the fallback string; the grouped panel overrides the display.
        retriever = RetrievalEngine()
        injected = retriever.inject_for_session(session_id, project_context)
        try:
            injected = _build_panel()
        except Exception as e:
            emit_stderr(f"Panel render error (non-fatal): {e}")
        create_ephemeral_buffer(base_dir)

        # Surface the panel to both the user and the model. Plain stdout
        # reaches the model only; emit_context adds the user-visible copy.
        emit_context(injected, "SessionStart")
    except Exception:
        # Never crash the session
        emit_stdout("")
        sys.exit(0)


if __name__ == "__main__":
    main()
