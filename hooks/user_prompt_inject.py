#!/usr/bin/env python3
"""UserPromptSubmit hook — just-in-time memory injection based on prompt content.

Fires on every user message. Extracts key terms from the prompt, searches
the memory index via hybrid RRF (FTS + optional vector), and injects relevant
memories that weren't already loaded at SessionStart.

Must be fast: FTS < 10ms, vec KNN < 3ms, RRF merge < 1ms.  The embedding call
(~200-400ms) is attempted but never required — FTS-only hybrid is used if
the embedding call fails.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hooks._safe import emit_context, emit_stderr, emit_stdout

from core.affect import coherence_probe, load_analyzer, save_analyzer, format_guidance
from core.database import get_base_dir, get_vec_store, init_db, project_slug
from core.models import Memory, RetrievalLog
from core.retrieval import RetrievalEngine

# Max characters (~500 tokens) for just-in-time injection.
# Deliberately small — this supplements SessionStart, not replaces it.
TOKEN_BUDGET_CHARS = 2000

# Max memories to inject per prompt.
MAX_MEMORIES = 3

# How many top-ranked matches are injected in full. Lower-ranked matches
# collapse into a compact pointer line. Corrections are always injected in
# full, regardless of this rank.
FULL_INJECT_RANK = 1

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


def _is_correction(memory) -> bool:
    """A correction memory — always injected in full, never abbreviated."""
    return (
        getattr(memory, "kind", None) == "correction"
        or getattr(memory, "polarity", None) == "corrective"
    )


def _first_tags(memory, limit: int = 3) -> str:
    """Comma-joined first few tags of a memory, for the pointer line."""
    raw = getattr(memory, "tags", None)
    if not raw:
        return ""
    try:
        tags = json.loads(raw) if isinstance(raw, str) else raw
        return ", ".join(str(t) for t in list(tags)[:limit])
    except (ValueError, TypeError):
        return ""


def _format_pointer_line(memories: list) -> str:
    """A single compact line pointing at weakly-matched memories."""
    refs = []
    for m in memories:
        short_id = (m.id or '')[:8]
        ref = f"[{short_id}]"
        tags = _first_tags(m)
        if tags:
            ref += f" ({tags})"
        refs.append(ref)
    return f"💡 {len(memories)} related — recall {' · '.join(refs)}"


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
    ranked: list = []
    try:
        if engine is None:
            engine = RetrievalEngine()
        ranked = engine.hybrid_search(
            query=fts_query,
            query_embedding=query_embedding,
            k=10,
            vec_store=get_vec_store(),
            project_context=project_context,
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
    # Project scoping now applied inside hybrid_search() (task #26). The
    # post-filter here is a defensive belt-and-suspenders for Tier 2 only
    # (Tier 2 originates from inject_for_session which scopes separately).
    current_project = project_slug(project_context)

    def _is_eligible(m) -> bool:
        proj_ok = (
            current_project is None
            or m.project is None
            or m.project == current_project
        )
        return (
            proj_ok
            and m.id not in already_injected
            and not m.archived_at
            and m.stage != "ephemeral"
        )

    candidates = (
        [m for m in tier2_memories if _is_eligible(m)]
        + [m for m in tier3_candidates if _is_eligible(m)]
    )

    if not candidates:
        return ""

    # Partition candidates into full-content injections and compact pointers.
    # `candidates` is already in merged relevance order. A correction is ALWAYS
    # injected in full, with stronger framing, and bypasses the per-prompt cap.
    # Other memories inject in full only for the top FULL_INJECT_RANK by
    # relevance; weaker matches collapse into a single pointer line.
    full_blocks: list = []      # (block_text, memory)
    pointer_mems: list = []
    budget_remaining = TOKEN_BUDGET_CHARS
    noncorrection_count = 0
    rank = 0

    for memory in candidates:
        is_correction = _is_correction(memory)
        if not is_correction and noncorrection_count >= MAX_MEMORIES:
            continue

        title = memory.title or "Memory"
        display = memory.summary or (memory.content or "")[:300]

        if is_correction:
            full_blocks.append((f"⛔ CORRECTION — {title}: {display}", memory))
            continue

        full = rank < FULL_INJECT_RANK
        rank += 1
        if full:
            block = f"[Memory: {title}] {display}"
            if len(block) > budget_remaining:
                continue
            budget_remaining -= len(block)
            full_blocks.append((block, memory))
            noncorrection_count += 1
        else:
            pointer_mems.append(memory)
            noncorrection_count += 1

    surfaced = [m for _, m in full_blocks] + pointer_mems
    if not surfaced:
        return ""

    # Log injections — full and pointer alike were surfaced to the agent.
    now = datetime.now().isoformat()
    for memory in surfaced:
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

    lines = [block for block, _ in full_blocks]
    if pointer_mems:
        lines.append(_format_pointer_line(pointer_mems))

    return "\n".join(lines)


def main():
    try:
        # Read user prompt from stdin (Claude Code pipes it)
        prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
        if not prompt.strip():
            emit_stdout("")
            return

        session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
        project_context = os.getcwd()

        init_db(project_context=project_context)

        # Memory search + injection
        result = search_and_inject(prompt, session_id, project_context)

        # Affect awareness — track interaction state across messages
        base_dir = get_base_dir()
        if base_dir:
            try:
                analyzer = load_analyzer(base_dir, session_id)
                # Strip system-reminder / command wrapper blocks before
                # affect detection. Reuses the same logic as transcript
                # cleaning so embedded reminders inside a real message do
                # not poison valence detection.
                from core.transcript import _clean_text
                state = analyzer.update(_clean_text(prompt))
                save_analyzer(analyzer, base_dir, session_id)

                # If degradation looks likely and we haven't probed recently,
                # run a coherence check (2 parallel LLM calls, ~1-2s)
                if state.likely_degraded:
                    try:
                        probe = coherence_probe(prompt)
                        if probe.likely_degraded:
                            guidance = (
                                "[Affect signal: coherence probe confirms degradation"
                                f" (variance={probe.variance:.2f})"
                                " — suggest compacting context or starting a fresh session"
                                " rather than retrying the same approach]"
                            )
                            result = f"{guidance}\n{result}" if result else guidance
                        # If probe says coherent, skip guidance — task is just hard
                    except Exception:
                        # Probe failed (API error, timeout) — fall back to cheap signal
                        guidance = format_guidance(state)
                        if guidance:
                            result = f"{guidance}\n{result}" if result else guidance
                else:
                    guidance = format_guidance(state)
                    if guidance:
                        result = f"{guidance}\n{result}" if result else guidance
            except Exception:
                pass  # affect tracking is best-effort

        # Surface JIT injections to both the user and the model.
        emit_context(result, "UserPromptSubmit")

    except Exception as exc:
        # Never crash the user's prompt
        emit_stderr(f"UserPromptInject error: {exc}")
        emit_stdout("")


if __name__ == "__main__":
    main()
