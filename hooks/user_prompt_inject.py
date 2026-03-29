#!/usr/bin/env python3
"""UserPromptSubmit hook — just-in-time memory injection based on prompt content.

Fires on every user message. Extracts key terms from the prompt, searches
the memory index via FTS, and injects relevant memories that weren't already
loaded at SessionStart.

Must be fast: FTS only, no LLM calls, small token budget (~2000 tokens).
"""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.storage import MemoryStore

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


def get_already_injected(store: MemoryStore, session_id: str) -> set[str]:
    """
    Get memory IDs that were already injected in this session.

    Queries the retrieval_log to avoid duplicate injection.
    """
    with sqlite3.connect(store.db_path) as conn:
        cursor = conn.execute(
            "SELECT DISTINCT memory_id FROM retrieval_log WHERE session_id = ?",
            (session_id,),
        )
        return {row[0] for row in cursor.fetchall()}


def search_and_inject(
    store: MemoryStore,
    prompt: str,
    session_id: str,
    project_context: str = None,
) -> str:
    """
    Search for memories relevant to the prompt and format for injection.

    Args:
        store: MemoryStore instance.
        prompt: The user's prompt text.
        session_id: Current session ID.
        project_context: Current project directory.

    Returns:
        Formatted injection string, or empty string if nothing relevant found.
    """
    terms = extract_query_terms(prompt)
    if not terms:
        return ""

    query = " OR ".join(terms)

    try:
        results = store.search_fts(query, limit=10)
    except Exception:
        return ""

    if not results:
        return ""

    # Filter out already-injected and archived memories
    already_injected = get_already_injected(store, session_id)
    candidates = [
        m for m in results
        if m["id"] not in already_injected
        and not m.get("archived_at")
        and m.get("stage") != "ephemeral"
    ]

    if not candidates:
        return ""

    # Select top memories within token budget
    selected = []
    budget_remaining = TOKEN_BUDGET_CHARS

    for memory in candidates[:MAX_MEMORIES]:
        content = memory.get("content", "")
        summary = memory.get("summary", "")
        title = memory.get("title", "Memory")

        # Prefer summary for brevity; fall back to content
        display = summary if summary else content[:300]
        cost = len(display) + len(title) + 20  # overhead for formatting

        if cost <= budget_remaining:
            selected.append((title, display, memory))
            budget_remaining -= cost

    if not selected:
        return ""

    # Log injections
    for _, _, memory in selected:
        store.record_injection(memory["id"], session_id, project_context)

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

        store = MemoryStore(project_context=project_context)
        result = search_and_inject(store, prompt, session_id, project_context)

        print(result, flush=True)

    except Exception:
        # Never crash the user's prompt
        print("", flush=True)


if __name__ == "__main__":
    main()
