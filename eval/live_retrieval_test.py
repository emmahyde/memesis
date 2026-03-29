"""
Eval: Live Retrieval Quality

Tests retrieval against real observations extracted from transcripts
via the scan → reduce pipeline. Unlike synthetic fixtures, these memories
come from actual sessions — so retrieval quality reflects real-world signal.

Requires eval/eval-observations.db (produced by reduce.py --db eval/eval-observations.db).

No LLM calls — tests FTS and injection mechanics against real content.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import Memory
from core.retrieval import RetrievalEngine
from eval.conftest import EVAL_OBSERVATIONS_DB, seed_from_observations


# ---------------------------------------------------------------------------
# Fixture sanity
# ---------------------------------------------------------------------------


def test_live_store_has_memories(live_store):
    """The live store should have a non-trivial number of memories."""
    count = Memory.select().count()
    assert count >= 10, f"Expected at least 10 memories from observations, got {count}"


def test_live_store_has_multiple_stages(live_store):
    """Observations with high frequency should be crystallized."""
    consolidated = Memory.by_stage("consolidated").count()
    crystallized = Memory.by_stage("crystallized").count()
    assert consolidated > 0, "Expected some consolidated memories"
    # crystallized only if any observation has count >= 10
    # don't assert — just report
    print(f"\n  consolidated: {consolidated}, crystallized: {crystallized}")


# ---------------------------------------------------------------------------
# Injection quality
# ---------------------------------------------------------------------------


def test_injection_produces_context(live_store):
    """inject_for_session should produce non-empty context from real memories."""
    engine = RetrievalEngine()
    context = engine.inject_for_session(session_id="live_eval_test")
    assert len(context) > 100, f"Injected context too short ({len(context)} chars)"
    assert "---MEMORY CONTEXT---" in context


def test_injection_contains_high_frequency_observations(live_store):
    """Memories with highest reinforcement count should appear in injected context."""
    engine = RetrievalEngine()
    context = engine.inject_for_session(session_id="live_eval_freq")

    # Get top 5 by reinforcement count
    top = (
        Memory.select()
        .where(Memory.stage.in_(["crystallized", "instinctive"]))
        .order_by(Memory.reinforcement_count.desc())
        .limit(5)
    )
    top_titles = [m.title for m in top]

    if not top_titles:
        pytest.skip("No crystallized/instinctive memories in live store")

    found = sum(1 for t in top_titles if t in context)
    score = found / len(top_titles)
    assert score >= 0.4, (
        f"Only {found}/{len(top_titles)} top observations found in context. "
        f"Missing: {[t for t in top_titles if t not in context]}"
    )


# ---------------------------------------------------------------------------
# FTS quality
# ---------------------------------------------------------------------------


def test_fts_finds_known_observation(live_store):
    """FTS should find at least one memory for a broad query."""
    # Use a term likely to exist in any observation store
    for query in ["workflow", "preference", "pattern", "test", "code"]:
        results = Memory.search_fts(Memory.sanitize_fts_term(query), limit=5)
        if results:
            return  # pass — at least one query hit
    pytest.fail("No FTS results for any common query term")


def test_fts_returns_relevant_results(live_store):
    """FTS results should contain the search term in title, summary, or content."""
    query = "workflow"
    results = Memory.search_fts(Memory.sanitize_fts_term(query), limit=5)
    if not results:
        pytest.skip("No FTS results for 'workflow'")

    for mem in results:
        haystack = f"{mem.title} {mem.summary} {mem.content}".lower()
        assert query in haystack, (
            f"FTS result '{mem.title}' doesn't contain '{query}'"
        )


# ---------------------------------------------------------------------------
# Coverage metrics
# ---------------------------------------------------------------------------


def test_observation_type_coverage(live_store):
    """The observation store should have diverse observation types."""
    types = set()
    for mem in Memory.select():
        tags = mem.tag_list
        for t in tags:
            if t.startswith("type:"):
                types.add(t.split(":", 1)[1])

    assert len(types) >= 3, (
        f"Expected at least 3 observation types, got {len(types)}: {types}"
    )


def test_memory_count_report(live_store):
    """Report memory counts by stage (informational, always passes)."""
    stages = {}
    for mem in Memory.select():
        stages[mem.stage] = stages.get(mem.stage, 0) + 1

    total = sum(stages.values())
    print(f"\n  Live store: {total} memories")
    for stage, count in sorted(stages.items()):
        print(f"    {stage}: {count}")
