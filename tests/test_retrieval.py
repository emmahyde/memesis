"""
Tests for the three-tier retrieval engine.

Covers:
- Tier 1 (instinctive): all memories loaded, no filtering
- Tier 2 (crystallized): token budget, project-context boosting, sort order
- Tier 3 (active_search): FTS ranked results with progressive disclosure
- inject_for_session(): formatted output, injection logging
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Add parent directory to path so imports resolve as `from core.xxx import ...`
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.retrieval import RetrievalEngine
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """Isolated MemoryStore backed by a temp directory."""
    return MemoryStore(base_dir=str(tmp_path / "memory"))


@pytest.fixture
def engine(store):
    """RetrievalEngine with default 8% token budget."""
    return RetrievalEngine(store)


def _make_memory(store, path, content, stage, title, summary=None, importance=0.5,
                 project_context=None, reinforcement_count=0):
    """Helper to create a memory with given parameters."""
    metadata = {
        "stage": stage,
        "title": title,
        "summary": summary or f"Summary of {title}",
        "importance": importance,
        "reinforcement_count": reinforcement_count,
    }
    if project_context is not None:
        # MemoryStore.create stores project_context from the store instance,
        # not from metadata.  We inject it directly after creation.
        pass

    memory_id = store.create(path=path, content=content, metadata=metadata)

    if project_context is not None:
        # Patch project_context directly in DB since MemoryStore.create uses
        # the store-level project_context, not per-memory metadata.
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE memories SET project_context = ? WHERE id = ?",
                (project_context, memory_id),
            )
            conn.commit()

    return memory_id


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_invalid_token_budget_zero(store):
    with pytest.raises(ValueError, match="token_budget_pct"):
        RetrievalEngine(store, token_budget_pct=0.0)


def test_invalid_token_budget_negative(store):
    with pytest.raises(ValueError, match="token_budget_pct"):
        RetrievalEngine(store, token_budget_pct=-0.1)


def test_token_limit_calculation(store):
    engine = RetrievalEngine(store, token_budget_pct=0.08)
    # 0.08 * 200_000 tokens * 4 chars/token = 64_000 chars
    assert engine.token_limit == 64_000


# ---------------------------------------------------------------------------
# Tier 1 — Instinctive memories
# ---------------------------------------------------------------------------


def test_get_instinctive_memories_all_loaded(store, engine):
    """Tier 1 returns ALL instinctive memories with content."""
    ids = []
    for i in range(3):
        mid = _make_memory(
            store,
            path=f"instinctive_{i}.md",
            content=f"Guideline content {i}",
            stage="instinctive",
            title=f"Guideline {i}",
        )
        ids.append(mid)

    results = engine.get_instinctive_memories()

    assert len(results) == 3
    result_ids = {m["id"] for m in results}
    assert result_ids == set(ids)


def test_get_instinctive_memories_content_populated(store, engine):
    """Each instinctive memory has its file content loaded."""
    _make_memory(
        store,
        path="guideline.md",
        content="Always be helpful",
        stage="instinctive",
        title="Core Guideline",
    )

    results = engine.get_instinctive_memories()
    assert len(results) == 1
    assert "Always be helpful" in results[0]["content"]


def test_get_instinctive_memories_empty(store, engine):
    """Returns empty list when no instinctive memories exist."""
    results = engine.get_instinctive_memories()
    assert results == []


def test_get_instinctive_memories_no_filtering(store, engine):
    """Instinctive memories are returned regardless of importance or project."""
    for i in range(5):
        _make_memory(
            store,
            path=f"inst_{i}.md",
            content=f"Content {i}",
            stage="instinctive",
            title=f"Memory {i}",
            importance=0.1 * i,  # includes very low importance
        )

    results = engine.get_instinctive_memories()
    assert len(results) == 5


# ---------------------------------------------------------------------------
# Tier 2 — Crystallized memories
# ---------------------------------------------------------------------------


def test_get_crystallized_returns_content(store, engine):
    """Crystallized memories have content loaded."""
    _make_memory(
        store,
        path="crystal.md",
        content="Python style: idiomatic",
        stage="crystallized",
        title="Python Style",
        importance=0.8,
    )

    results = engine.get_crystallized_for_context()
    assert len(results) == 1
    assert "Python style" in results[0]["content"]


def test_get_crystallized_token_budget_respected(store):
    """Tier 2 never exceeds the configured token budget."""
    # Each memory has ~100 chars of content
    # Set a tiny budget: 50 tokens → 200 chars
    engine = RetrievalEngine(store, token_budget_pct=0.000001)
    # token_limit = int(0.000001 * 200_000) * 4 = int(0.2) * 4 = 0 chars
    # Use a more realistic small budget via direct assignment after creation
    engine = RetrievalEngine(store, token_budget_pct=0.0001)
    # token_limit = int(0.0001 * 200_000) * 4 = int(20) * 4 = 80 chars

    content_a = "A" * 50   # 50 chars — fits
    content_b = "B" * 50   # 50 chars — might not fit after A
    content_c = "C" * 50   # 50 chars

    _make_memory(store, "mem_a.md", content_a, "crystallized", "Mem A", importance=0.9)
    _make_memory(store, "mem_b.md", content_b, "crystallized", "Mem B", importance=0.8)
    _make_memory(store, "mem_c.md", content_c, "crystallized", "Mem C", importance=0.7)

    results = engine.get_crystallized_for_context(token_limit=80)

    # Total content chars across results must be <= 80
    total_chars = sum(len(m.get("content", "") or "") for m in results)
    assert total_chars <= 80


def test_get_crystallized_project_context_boosted(store, engine):
    """Project-matching memories appear before non-matching ones."""
    _make_memory(
        store, "unrelated.md", "Unrelated content", "crystallized",
        "Unrelated Memory", importance=0.9, project_context="/other/project",
    )
    _make_memory(
        store, "matching.md", "Project-specific content", "crystallized",
        "Matching Memory", importance=0.6, project_context="/my/project",
    )

    results = engine.get_crystallized_for_context(project_context="/my/project")

    assert len(results) == 2
    # Matching memory must come first despite lower importance
    assert results[0]["title"] == "Matching Memory"
    assert results[1]["title"] == "Unrelated Memory"


def test_get_crystallized_sort_importance_desc(store, engine):
    """Without project context, memories sorted by importance DESC."""
    _make_memory(store, "low.md", "Low importance content", "crystallized",
                 "Low", importance=0.3)
    _make_memory(store, "high.md", "High importance content", "crystallized",
                 "High", importance=0.9)
    _make_memory(store, "mid.md", "Mid importance content", "crystallized",
                 "Mid", importance=0.6)

    results = engine.get_crystallized_for_context()

    importances = [m["importance"] for m in results]
    assert importances == sorted(importances, reverse=True)


def test_get_crystallized_empty(store, engine):
    """Returns empty list when no crystallized memories exist."""
    results = engine.get_crystallized_for_context()
    assert results == []


def test_get_crystallized_no_project_context_no_boost(store, engine):
    """Without project_context, all memories treated equally (importance order)."""
    _make_memory(store, "a.md", "Content A", "crystallized", "A",
                 importance=0.8, project_context="/proj/a")
    _make_memory(store, "b.md", "Content B", "crystallized", "B",
                 importance=0.9, project_context="/proj/b")

    results = engine.get_crystallized_for_context(project_context=None)

    # Sorted purely by importance DESC — B (0.9) before A (0.8)
    assert results[0]["title"] == "B"
    assert results[1]["title"] == "A"


def test_get_crystallized_budget_skips_oversized(store):
    """Best-effort packing: large memory skipped, smaller one still included."""
    # MemoryStore wraps content in YAML frontmatter (~64 chars overhead).
    # big_content: 400 chars raw → ~464 chars on disk (won't fit in 200 budget)
    # small_content: 10 chars raw → ~74 chars on disk (fits in 200 budget)
    big_content = "X" * 400
    small_content = "Y" * 10

    engine = RetrievalEngine(store, token_budget_pct=0.08)
    _make_memory(store, "big.md", big_content, "crystallized", "Big", importance=0.9)
    _make_memory(store, "small.md", small_content, "crystallized", "Small", importance=0.5)

    # Budget of 200 chars: big (~464 chars) doesn't fit, small (~74 chars) does.
    results = engine.get_crystallized_for_context(token_limit=200)

    titles = [m["title"] for m in results]
    assert "Big" not in titles
    assert "Small" in titles


# ---------------------------------------------------------------------------
# Tier 3 — Active search
# ---------------------------------------------------------------------------


def test_active_search_returns_matches(store, engine):
    """FTS search returns relevant memories."""
    _make_memory(
        store, "python_style.md", "Prefer idiomatic Python constructs",
        "crystallized", "Python Style",
        summary="Python code style guidelines",
    )
    _make_memory(
        store, "ruby_style.md", "Prefer idiomatic Ruby constructs",
        "consolidated", "Ruby Style",
        summary="Ruby code style guidelines",
    )

    results = engine.active_search("Python", session_id="test_session")

    assert len(results) >= 1
    titles = [r["title"] for r in results]
    assert "Python Style" in titles


def test_active_search_progressive_disclosure(store, engine):
    """Active search results include summary field prominently."""
    _make_memory(
        store, "mem.md", "Full detailed content here",
        "crystallized", "Detailed Memory",
        summary="Short summary",
    )

    results = engine.active_search("detailed", session_id="session_1")

    assert len(results) >= 1
    result = results[0]
    # Progressive disclosure: both summary and full content present
    assert "summary" in result
    assert result["summary"] == "Short summary"
    assert "content" in result
    assert "Full detailed content" in result["content"]


def test_active_search_limit_respected(store, engine):
    """active_search respects the limit parameter."""
    for i in range(10):
        _make_memory(
            store, f"mem_{i}.md", f"Searchable content item {i}",
            "crystallized", f"Memory {i}",
            summary=f"Summary {i}",
        )

    results = engine.active_search("Searchable content", session_id="s", limit=3)

    assert len(results) <= 3


def test_active_search_result_fields(store, engine):
    """Active search results include all required fields."""
    _make_memory(
        store, "tagged.md", "Tagged content searchable",
        "crystallized", "Tagged Memory",
        summary="A tagged memory",
        importance=0.75,
    )

    results = engine.active_search("tagged", session_id="s1")

    assert len(results) >= 1
    r = results[0]
    for field in ("id", "title", "summary", "content", "importance", "stage", "tags", "rank"):
        assert field in r, f"Missing field: {field}"


def test_active_search_no_results(store, engine):
    """Active search returns empty list when no FTS matches."""
    _make_memory(store, "unrelated.md", "Completely unrelated", "crystallized",
                 "Unrelated")

    results = engine.active_search("xyzzy_nonexistent_term_42", session_id="s")

    assert results == []


# ---------------------------------------------------------------------------
# inject_for_session
# ---------------------------------------------------------------------------


def test_inject_for_session_returns_nonempty(store, engine):
    """inject_for_session returns non-empty string when memories exist."""
    _make_memory(store, "guideline.md", "Be helpful", "instinctive",
                 "Core Guideline")

    result = engine.inject_for_session(session_id="sess_001")

    assert isinstance(result, str)
    assert len(result) > 0


def test_inject_for_session_format(store, engine):
    """Output contains required header/footer and section markers."""
    _make_memory(store, "inst.md", "Always be concise", "instinctive",
                 "Conciseness")

    result = engine.inject_for_session(session_id="sess_002")

    assert "---MEMORY CONTEXT---" in result
    assert "---END MEMORY CONTEXT---" in result
    assert "## Your Behavioral Guidelines (always active)" in result


def test_inject_for_session_includes_crystallized_section(store, engine):
    """inject_for_session includes crystallized section when present."""
    _make_memory(store, "cryst.md", "Python style details", "crystallized",
                 "Python Style", summary="Python idioms")

    result = engine.inject_for_session(session_id="sess_003")

    assert "## Context-Relevant Knowledge" in result
    assert "Python Style" in result


def test_inject_for_session_logs_all_injections(store, engine):
    """Every injected memory (Tier 1 + Tier 2) is logged via record_injection."""
    inst_id = _make_memory(store, "inst.md", "Guideline", "instinctive", "Guideline")
    cryst_id = _make_memory(store, "cryst.md", "Knowledge", "crystallized", "Knowledge")

    engine.inject_for_session(session_id="sess_log_test")

    log = store.get_retrieval_log()
    logged_memory_ids = {entry["memory_id"] for entry in log}
    assert inst_id in logged_memory_ids
    assert cryst_id in logged_memory_ids


def test_inject_for_session_injection_count_incremented(store, engine):
    """injection_count increments for each injected memory."""
    inst_id = _make_memory(store, "inst.md", "Guideline", "instinctive", "Guideline")

    engine.inject_for_session(session_id="s1")
    engine.inject_for_session(session_id="s2")

    memory = store.get(inst_id)
    assert memory["injection_count"] == 2


def test_inject_for_session_empty_store_returns_empty_string(store, engine):
    """Returns empty string when store has no memories."""
    result = engine.inject_for_session(session_id="empty_session")
    assert result == ""


def test_inject_for_session_only_instinctive(store, engine):
    """Works correctly when only instinctive memories exist."""
    _make_memory(store, "inst.md", "Instinctive content", "instinctive", "Inst")

    result = engine.inject_for_session(session_id="s")

    assert "---MEMORY CONTEXT---" in result
    assert "## Your Behavioral Guidelines (always active)" in result
    # No crystallized section
    assert "## Context-Relevant Knowledge" not in result


def test_inject_for_session_only_crystallized(store, engine):
    """Works correctly when only crystallized memories exist."""
    _make_memory(store, "cryst.md", "Crystallized content", "crystallized", "Cryst")

    result = engine.inject_for_session(session_id="s")

    assert "---MEMORY CONTEXT---" in result
    assert "## Context-Relevant Knowledge" in result
    # No instinctive section
    assert "## Your Behavioral Guidelines (always active)" not in result


def test_inject_for_session_project_context_boosting(store, engine):
    """Project context passed to Tier-2 retrieval is applied."""
    matching_id = _make_memory(
        store, "match.md", "Project-specific knowledge", "crystallized",
        "Matching Project Memory", importance=0.5,
        project_context="/my/project",
    )
    other_id = _make_memory(
        store, "other.md", "Generic knowledge", "crystallized",
        "Generic Memory", importance=0.9,
        project_context="/other/project",
    )

    result = engine.inject_for_session(session_id="s", project_context="/my/project")

    # Both should appear
    assert "Matching Project Memory" in result
    assert "Generic Memory" in result

    # Matching memory should appear first in the output
    pos_matching = result.index("Matching Project Memory")
    pos_generic = result.index("Generic Memory")
    assert pos_matching < pos_generic


def test_inject_for_session_does_not_log_active_search(store, engine):
    """Active search results are NOT logged by inject_for_session."""
    cryst_id = _make_memory(store, "cryst.md", "Knowledge", "crystallized", "Knowledge")

    # Only inject, don't call active_search
    engine.inject_for_session(session_id="sess")

    log = store.get_retrieval_log()
    assert len(log) == 1  # only the crystallized injection
    assert log[0]["memory_id"] == cryst_id


# ---------------------------------------------------------------------------
# D-08 / D-09 integration (lifecycle decisions)
# ---------------------------------------------------------------------------


def test_d09_demotion_candidate_after_ten_injections(store, engine):
    """
    D-09: memories injected 10+ times but never used are flagged for demotion.
    inject_for_session records injections; LifecycleManager reads them.
    """
    from core.lifecycle import LifecycleManager

    cryst_id = _make_memory(
        store, "unused.md", "Unused crystallized", "crystallized", "Unused"
    )

    # Inject 10 times across different sessions
    for i in range(10):
        engine.inject_for_session(session_id=f"sess_{i}")

    manager = LifecycleManager(store)
    candidates = manager.get_demotion_candidates()

    candidate_ids = [c["id"] for c in candidates]
    assert cryst_id in candidate_ids


def test_inject_for_session_multiple_tiers_both_logged(store, engine):
    """Both Tier-1 and Tier-2 memories are logged in the same session."""
    inst_id = _make_memory(store, "inst.md", "I content", "instinctive", "Inst")
    cryst_id = _make_memory(store, "cryst.md", "C content", "crystallized", "Cryst")

    engine.inject_for_session(session_id="multi_session")

    log = store.get_retrieval_log()
    assert len(log) == 2
    logged_ids = {e["memory_id"] for e in log}
    assert inst_id in logged_ids
    assert cryst_id in logged_ids
    # Both logged as 'injected' type
    for entry in log:
        assert entry["retrieval_type"] == "injected"
