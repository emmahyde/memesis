#!/usr/bin/env python3
"""UserPromptSubmit hook — just-in-time memory injection based on prompt content.

Fires on every user message. Extracts key terms from the prompt, searches
the memory index via hybrid RRF (FTS + optional vector), and injects relevant
memories that weren't already loaded at SessionStart.

Must be fast: FTS < 10ms, vec KNN < 3ms, RRF merge < 1ms.  The embedding call
(~200-400ms) is attempted but never required — FTS-only hybrid is used if
Bedrock is unavailable.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import get_vec_store, init_db
from core.models import Memory, RetrievalLog
from core.retrieval import RetrievalEngine

# Max characters (~500 tokens) for just-in-time injection.
# Deliberately small — this supplements SessionStart, not replaces it.
TOKEN_BUDGET_CHARS = 2000

# Max memories to inject per prompt.
MAX_MEMORIES = 3

# Minimum word length to include in FTS query.
MIN_WORD_LENGTH = 4

# Common stop words to exclude from FTS queries.
STOP_WORDS = frozenset({
    "this", "that", "with", "from", "have", "been", "will", "your", "what",
    "when", "where", "which", "their", "there", "about", "would", "could",
    "should", "does", "into", "than", "them", "then", "some", "other",
    "also", "just", "only", "very", "most", "more", "make", "like",
    "each", "much", "many", "such", "even", "well", "back", "over",
    "after", "before", "first", "last", "being", "still", "here",
    "these", "those", "both", "same", "take", "want", "give", "help",
    "please", "thanks", "need", "know", "think", "look", "find",
    "file", "code", "line", "test", "work", "check", "sure",
})


def extract_query_terms(prompt: str) -> list[str]:
    """
    Extract significant words from a user prompt for FTS search.

    Filters out short words, stop words, and non-alpha tokens.
    Returns at most 10 terms.
    """
    # Strip markdown formatting, code fences, etc.
    clean = re.sub(r'```[\s\S]*?```', '', prompt)
    clean = re.sub(r'`[^`]+`', '', clean)
    clean = re.sub(r'[^a-zA-Z\s]', ' ', clean)

    words = clean.lower().split()
    terms = []
    seen = set()

    for word in words:
        if (
            len(word) >= MIN_WORD_LENGTH
            and word not in STOP_WORDS
            and word not in seen
        ):
            terms.append(word)
            seen.add(word)

        if len(terms) >= 10:
            break

    return terms


def get_already_injected(session_id: str) -> set[str]:
    """
    Get memory IDs that were already injected in this session.
    """
    rows = (
        RetrievalLog.select(RetrievalLog.memory_id)
        .where(RetrievalLog.session_id == session_id)
        .distinct()
    )
    return {r.memory_id for r in rows}


def search_and_inject(
    prompt: str,
    session_id: str,
    project_context: str = None,
) -> str:
    """
    Search for memories relevant to the prompt and format for injection.

    Uses two complementary retrieval paths:
    - Tier 2: get_crystallized_for_context with prompt-derived query — applies
      project_context boost, token budget, and crystallized-only filtering via
      hybrid RRF.
    - Tier 3 JIT: hybrid_search across ALL stages — supplements with
      non-crystallized memories not covered by Tier 2.

    The embedding is computed once and shared by both calls to stay within the
    500ms latency budget.  If embedding fails, both paths fall back to FTS-only.
    """
    terms = extract_query_terms(prompt)
    if not terms:
        return ""

    # Build FTS query string (shared by both Tier 2 and Tier 3 legs)
    fts_query = " OR ".join(Memory.sanitize_fts_term(t) for t in terms)

    # Attempt embedding from raw prompt text — computed ONCE, reused by both legs
    query_embedding = None
    try:
        from core.embeddings import embed_text
        query_embedding = embed_text(prompt[:500])
    except Exception:
        pass  # FTS-only hybrid fallback

    # --- Tier 2: crystallized-only path (project_context boost, token budget) ---
    tier2_memories: list = []
    tier2_ids: set = set()
    try:
        from core.flags import get_flag
        engine = RetrievalEngine()
        if get_flag("prompt_aware_tier2"):
            tier2_memories = engine.get_crystallized_for_context(
                query=fts_query,
                query_embedding=query_embedding,
                project_context=project_context,
                token_limit=TOKEN_BUDGET_CHARS,
            )
            tier2_ids = {m.id for m in tier2_memories}
    except Exception:
        engine = None

    # --- Tier 3 JIT: all-stage hybrid search (supplements Tier 2) ---
    tier3_candidates: list = []
    try:
        if engine is None:
            engine = RetrievalEngine()
        ranked = engine.hybrid_search(
            query=fts_query,
            query_embedding=query_embedding,
            k=10,
            vec_store=get_vec_store(),
        )
        if ranked:
            ranked_ids = [mid for mid, _ in ranked]
            memories_by_id = {
                m.id: m
                for m in Memory.select().where(Memory.id.in_(ranked_ids))
            }
            tier3_candidates = [
                memories_by_id[mid] for mid, _ in ranked
                if mid in memories_by_id and mid not in tier2_ids
            ]
    except Exception:
        pass

    # --- Merge: Tier 2 first, then Tier 3 JIT to fill remaining slots ---
    already_injected = get_already_injected(session_id)

    def _is_eligible(m) -> bool:
        return (
            m.id not in already_injected
            and not m.archived_at
            and m.stage != "ephemeral"
        )

    candidates = (
        [m for m in tier2_memories if _is_eligible(m)]
        + [m for m in tier3_candidates if _is_eligible(m)]
    )

    if not candidates:
        return ""

    # Select top memories within token budget
    selected = []
    budget_remaining = TOKEN_BUDGET_CHARS

    for memory in candidates[:MAX_MEMORIES]:
        content = memory.content or ""
        summary = memory.summary or ""
        title = memory.title or "Memory"

        # Prefer summary for brevity; fall back to content
        display = summary if summary else content[:300]
        cost = len(display) + len(title) + 20  # overhead for formatting

        if cost <= budget_remaining:
            selected.append((title, display, memory))
            budget_remaining -= cost

    if not selected:
        return ""

    # Log injections
    now = datetime.now().isoformat()
    for _, _, memory in selected:
        Memory.update(
            last_injected_at=now,
            injection_count=Memory.injection_count + 1,
        ).where(Memory.id == memory.id).execute()

        RetrievalLog.create(
            timestamp=now,
            session_id=session_id,
            memory_id=memory.id,
            retrieval_type='injected',
            project_context=project_context,
        )

    # Format output
    lines = []
    for title, display, _ in selected:
        lines.append(f"[Memory: {title}] {display}")

    return "\n".join(lines)


def main():
    try:
        # Read user prompt from stdin (Claude Code pipes it)
        prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
        if not prompt.strip():
            print("", flush=True)
            return

        session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
        project_context = os.getcwd()

        init_db(project_context=project_context)
        result = search_and_inject(prompt, session_id, project_context)

        print(result, flush=True)

    except Exception:
        # Never crash the user's prompt
        print("", flush=True)


if __name__ == "__main__":
    main()
