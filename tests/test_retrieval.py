"""
Tests for the three-tier retrieval engine.

Covers:
- Tier 1 (instinctive): all memories loaded, no filtering
- Tier 2 (crystallized): token budget, project-context boosting, sort order
- Tier 3 (active_search): FTS ranked results with progressive disclosure
- inject_for_session(): formatted output, injection logging
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Add parent directory to path so imports resolve as `from core.xxx import ...`
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.retrieval import RetrievalEngine
from core.database import init_db, close_db, get_base_dir, get_db_path
from core.models import Memory, NarrativeThread, ThreadMember, RetrievalLog, db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base(tmp_path):
    """Initialize DB in a throwaway temp directory."""
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def engine(base):
    """RetrievalEngine with default 8% token budget."""
    return RetrievalEngine()


def _make_memory(content, stage, title, summary=None, importance=0.5,
                 project_context=None, reinforcement_count=0, **kwargs):
    """Helper to create a memory with given parameters."""
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage=stage,
        title=title,
        summary=summary or f"Summary of {title}",
        content=content,
        tags=json.dumps(kwargs.get('tags', [])),
        importance=importance,
        reinforcement_count=reinforcement_count,
        created_at=now,
        updated_at=now,
    )

    if project_context is not None:
        Memory.update(project_context=project_context).where(Memory.id == mem.id).execute()

    return mem.id


def _get_retrieval_log():
    """Helper to get retrieval log entries."""
    return [
        {
            "memory_id": r.memory_id,
            "session_id": r.session_id,
            "retrieval_type": r.retrieval_type,
        }
        for r in RetrievalLog.select()
    ]


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_invalid_token_budget_zero(base):
    with pytest.raises(ValueError, match="token_budget_pct"):
        RetrievalEngine(token_budget_pct=0.0)


def test_invalid_token_budget_negative(base):
    with pytest.raises(ValueError, match="token_budget_pct"):
        RetrievalEngine(token_budget_pct=-0.1)


def test_token_limit_calculation(base):
    engine = RetrievalEngine(token_budget_pct=0.08)
    # 0.08 * 200_000 tokens * 4 chars/token = 64_000 chars
    assert engine.token_limit == 64_000


# ---------------------------------------------------------------------------
# Tier 1 -- Instinctive memories
# ---------------------------------------------------------------------------


def test_get_instinctive_memories_all_loaded(base, engine):
    """Tier 1 returns ALL instinctive memories with content."""
    ids = []
    for i in range(3):
        mid = _make_memory(
            content=f"Guideline content {i}",
            stage="instinctive",
            title=f"Guideline {i}",
        )
        ids.append(mid)

    results = engine.get_instinctive_memories()

    assert len(results) == 3
    result_ids = {m.id for m in results}
    assert result_ids == set(ids)


def test_get_instinctive_memories_content_populated(base, engine):
    """Each instinctive memory has its content loaded."""
    _make_memory(
        content="Always be helpful",
        stage="instinctive",
        title="Core Guideline",
    )

    results = engine.get_instinctive_memories()
    assert len(results) == 1
    assert "Always be helpful" in results[0].content


def test_get_instinctive_memories_empty(base, engine):
    """Returns empty list when no instinctive memories exist."""
    results = engine.get_instinctive_memories()
    assert results == []


def test_get_instinctive_memories_no_filtering(base, engine):
    """Instinctive memories are returned regardless of importance or project."""
    for i in range(5):
        _make_memory(
            content=f"Content {i}",
            stage="instinctive",
            title=f"Memory {i}",
            importance=0.1 * i,
        )

    results = engine.get_instinctive_memories()
    assert len(results) == 5


# ---------------------------------------------------------------------------
# Tier 2 -- Crystallized memories
# ---------------------------------------------------------------------------


def test_get_crystallized_returns_content(base, engine):
    """Crystallized memories have content loaded."""
    _make_memory(
        content="Python style: idiomatic",
        stage="crystallized",
        title="Python Style",
        importance=0.8,
    )

    results = engine.get_crystallized_for_context()
    assert len(results) == 1
    assert "Python style" in results[0].content


def test_get_crystallized_token_budget_respected(base):
    """Tier 2 never exceeds the configured token budget."""
    engine = RetrievalEngine(token_budget_pct=0.0001)
    # token_limit = int(0.0001 * 200_000) * 4 = 80 chars

    content_a = "A" * 50
    content_b = "B" * 50
    content_c = "C" * 50

    _make_memory(content_a, "crystallized", "Mem A", importance=0.9)
    _make_memory(content_b, "crystallized", "Mem B", importance=0.8)
    _make_memory(content_c, "crystallized", "Mem C", importance=0.7)

    results = engine.get_crystallized_for_context(token_limit=80)

    total_chars = sum(len(m.content or "") for m in results)
    assert total_chars <= 80


def test_get_crystallized_project_context_boosted(base, engine):
    """Project-matching memories appear before non-matching ones."""
    _make_memory(
        "Unrelated content", "crystallized",
        "Unrelated Memory", importance=0.9, project_context="/other/project",
    )
    _make_memory(
        "Project-specific content", "crystallized",
        "Matching Memory", importance=0.6, project_context="/my/project",
    )

    results = engine.get_crystallized_for_context(project_context="/my/project")

    assert len(results) == 2
    assert results[0].title == "Matching Memory"
    assert results[1].title == "Unrelated Memory"


def test_get_crystallized_sort_importance_desc(base, engine):
    """Without project context, memories sorted by importance DESC."""
    _make_memory("Low importance content", "crystallized", "Low", importance=0.3)
    _make_memory("High importance content", "crystallized", "High", importance=0.9)
    _make_memory("Mid importance content", "crystallized", "Mid", importance=0.6)

    results = engine.get_crystallized_for_context()

    importances = [m.importance for m in results]
    assert importances == sorted(importances, reverse=True)


def test_get_crystallized_empty(base, engine):
    """Returns empty list when no crystallized memories exist."""
    results = engine.get_crystallized_for_context()
    assert results == []


def test_get_crystallized_no_project_context_no_boost(base, engine):
    """Without project_context, all memories treated equally (importance order)."""
    _make_memory("Content A", "crystallized", "A",
                 importance=0.8, project_context="/proj/a")
    _make_memory("Content B", "crystallized", "B",
                 importance=0.9, project_context="/proj/b")

    results = engine.get_crystallized_for_context(project_context=None)

    assert results[0].title == "B"
    assert results[1].title == "A"


def test_get_crystallized_budget_skips_oversized(base):
    """Best-effort packing: large memory skipped, smaller one still included."""
    big_content = "X" * 400
    small_content = "Y" * 10

    engine = RetrievalEngine(token_budget_pct=0.08)
    _make_memory(big_content, "crystallized", "Big", importance=0.9)
    _make_memory(small_content, "crystallized", "Small", importance=0.5)

    results = engine.get_crystallized_for_context(token_limit=200)

    titles = [m.title for m in results]
    assert "Big" not in titles
    assert "Small" in titles


# ---------------------------------------------------------------------------
# Tier 3 -- Active search
# ---------------------------------------------------------------------------


def test_active_search_returns_matches(base, engine):
    """FTS search returns relevant memories."""
    _make_memory(
        "Prefer idiomatic Python constructs", "crystallized", "Python Style",
        summary="Python code style guidelines",
    )
    _make_memory(
        "Prefer idiomatic Ruby constructs", "consolidated", "Ruby Style",
        summary="Ruby code style guidelines",
    )

    results = engine.active_search("Python", session_id="test_session")

    assert len(results) >= 1
    titles = [r["title"] for r in results]
    assert "Python Style" in titles


def test_active_search_progressive_disclosure(base, engine):
    """Active search results include summary field prominently."""
    _make_memory(
        "Full detailed content here", "crystallized", "Detailed Memory",
        summary="Short summary",
    )

    results = engine.active_search("detailed", session_id="session_1")

    assert len(results) >= 1
    result = results[0]
    assert "summary" in result
    assert result["summary"] == "Short summary"
    assert "content" in result
    assert "Full detailed content" in result["content"]


def test_active_search_limit_respected(base, engine):
    """active_search respects the limit parameter."""
    for i in range(10):
        _make_memory(
            f"Searchable content item {i}", "crystallized", f"Memory {i}",
            summary=f"Summary {i}",
        )

    results = engine.active_search("Searchable content", session_id="s", limit=3)
    assert len(results) <= 3


def test_active_search_result_fields(base, engine):
    """Active search results include all required fields."""
    _make_memory(
        "Tagged content searchable", "crystallized", "Tagged Memory",
        summary="A tagged memory", importance=0.75,
    )

    results = engine.active_search("tagged", session_id="s1")

    assert len(results) >= 1
    r = results[0]
    for field in ("id", "title", "summary", "content", "importance", "stage", "tags", "rank"):
        assert field in r, f"Missing field: {field}"


def test_active_search_no_results(base, engine):
    """Active search returns empty list when no FTS matches."""
    _make_memory("Completely unrelated", "crystallized", "Unrelated")

    results = engine.active_search("xyzzy_nonexistent_term_42", session_id="s")
    assert results == []


# ---------------------------------------------------------------------------
# inject_for_session
# ---------------------------------------------------------------------------


def test_inject_for_session_returns_nonempty(base, engine):
    """inject_for_session returns non-empty string when memories exist."""
    _make_memory("Be helpful", "instinctive", "Core Guideline")

    result = engine.inject_for_session(session_id="sess_001")

    assert isinstance(result, str)
    assert len(result) > 0


def test_inject_for_session_format(base, engine):
    """Output contains required header/footer and section markers."""
    _make_memory("Always be concise", "instinctive", "Conciseness")

    result = engine.inject_for_session(session_id="sess_002")

    assert "---MEMORY CONTEXT---" in result
    assert "---END MEMORY CONTEXT---" in result
    assert "## Your Behavioral Guidelines (always active)" in result


def test_inject_for_session_includes_crystallized_section(base, engine):
    """inject_for_session includes crystallized section when present."""
    _make_memory("Python style details", "crystallized", "Python Style",
                 summary="Python idioms")

    result = engine.inject_for_session(session_id="sess_003")

    assert "## Context-Relevant Knowledge" in result
    assert "Python Style" in result


def test_inject_for_session_logs_all_injections(base, engine):
    """Every injected memory (Tier 1 + Tier 2) is logged via record_injection."""
    inst_id = _make_memory("Guideline", "instinctive", "Guideline")
    cryst_id = _make_memory("Knowledge", "crystallized", "Knowledge")

    engine.inject_for_session(session_id="sess_log_test")

    log = _get_retrieval_log()
    logged_memory_ids = {entry["memory_id"] for entry in log}
    assert inst_id in logged_memory_ids
    assert cryst_id in logged_memory_ids


def test_inject_for_session_injection_count_incremented(base, engine):
    """injection_count increments for each injected memory."""
    inst_id = _make_memory("Guideline", "instinctive", "Guideline")

    engine.inject_for_session(session_id="s1")
    engine.inject_for_session(session_id="s2")

    memory = Memory.get_by_id(inst_id)
    assert memory.injection_count == 2


def test_inject_for_session_empty_store_returns_empty_string(base, engine):
    """Returns empty string when store has no memories."""
    result = engine.inject_for_session(session_id="empty_session")
    assert result == ""


def test_inject_for_session_only_instinctive(base, engine):
    """Works correctly when only instinctive memories exist."""
    _make_memory("Instinctive content", "instinctive", "Inst")

    result = engine.inject_for_session(session_id="s")

    assert "---MEMORY CONTEXT---" in result
    assert "## Your Behavioral Guidelines (always active)" in result
    assert "## Context-Relevant Knowledge" not in result


def test_inject_for_session_only_crystallized(base, engine):
    """Works correctly when only crystallized memories exist."""
    _make_memory("Crystallized content", "crystallized", "Cryst")

    result = engine.inject_for_session(session_id="s")

    assert "---MEMORY CONTEXT---" in result
    assert "## Context-Relevant Knowledge" in result
    assert "## Your Behavioral Guidelines (always active)" not in result


def test_inject_for_session_project_context_boosting(base, engine):
    """Project context passed to Tier-2 retrieval is applied."""
    matching_id = _make_memory(
        "Project-specific knowledge", "crystallized",
        "Matching Project Memory", importance=0.5,
        project_context="/my/project",
    )
    other_id = _make_memory(
        "Generic knowledge", "crystallized",
        "Generic Memory", importance=0.9,
        project_context="/other/project",
    )

    result = engine.inject_for_session(session_id="s", project_context="/my/project")

    assert "Matching Project Memory" in result
    assert "Generic Memory" in result

    pos_matching = result.index("Matching Project Memory")
    pos_generic = result.index("Generic Memory")
    assert pos_matching < pos_generic


def test_inject_for_session_does_not_log_active_search(base, engine):
    """Active search results are NOT logged by inject_for_session."""
    cryst_id = _make_memory("Knowledge", "crystallized", "Knowledge")

    engine.inject_for_session(session_id="sess")

    log = _get_retrieval_log()
    assert len(log) == 1
    assert log[0]["memory_id"] == cryst_id


# ---------------------------------------------------------------------------
# D-08 / D-09 integration (lifecycle decisions)
# ---------------------------------------------------------------------------


def test_d09_demotion_candidate_after_ten_injections(base, engine):
    """
    D-09: memories injected 10+ times but never used are flagged for demotion.
    """
    from core.lifecycle import LifecycleManager

    cryst_id = _make_memory("Unused crystallized", "crystallized", "Unused")

    for i in range(10):
        engine.inject_for_session(session_id=f"sess_{i}")

    manager = LifecycleManager()
    candidates = manager.get_demotion_candidates()

    candidate_ids = [c["id"] for c in candidates]
    assert cryst_id in candidate_ids


def test_inject_for_session_multiple_tiers_both_logged(base, engine):
    """Both Tier-1 and Tier-2 memories are logged in the same session."""
    inst_id = _make_memory("I content", "instinctive", "Inst")
    cryst_id = _make_memory("C content", "crystallized", "Cryst")

    engine.inject_for_session(session_id="multi_session")

    log = _get_retrieval_log()
    assert len(log) == 2
    logged_ids = {e["memory_id"] for e in log}
    assert inst_id in logged_ids
    assert cryst_id in logged_ids
    for entry in log:
        assert entry["retrieval_type"] == "injected"


# ---------------------------------------------------------------------------
# D3 -- Thread budget (THREAD_BUDGET_CHARS = 8_000)
# ---------------------------------------------------------------------------


def _create_thread(title, summary, narrative, member_ids):
    """Helper to create a narrative thread."""
    now = datetime.now().isoformat()
    thread = NarrativeThread.create(
        title=title,
        summary=summary,
        narrative=narrative,
        created_at=now,
        updated_at=now,
    )
    for pos, mid in enumerate(member_ids):
        ThreadMember.create(thread_id=thread.id, memory_id=mid, position=pos)
    return thread.id


class TestThreadBudget:
    """Tests for the greedy THREAD_BUDGET_CHARS packing in _get_thread_narratives."""

    def test_short_threads_fit_within_budget(self, base, engine):
        """Three threads with ~200-char narratives each all fit within the 8 000-char budget."""
        narrative = "Short narrative sentence. " * 8  # ~208 chars

        mem_ids = []
        for i in range(3):
            mid = _make_memory(
                f"Crystallized content {i}", "crystallized", f"Crystal Short {i}",
            )
            mem_ids.append(mid)

        for i, mid in enumerate(mem_ids):
            _create_thread(f"Thread Short {i}", f"Summary {i}", narrative, [mid])

        result = engine.inject_for_session(session_id="budget_short_sess")

        for i in range(3):
            assert f"Thread Short {i}" in result

    def test_threads_over_budget_excluded(self, base, engine, monkeypatch):
        """Threads whose total narrative exceeds the budget are partially excluded."""
        import core.retrieval as retrieval_module

        monkeypatch.setattr(retrieval_module, "THREAD_BUDGET_CHARS", 1_500)

        mem_a = _make_memory("Content A", "crystallized", "Crystal A")
        mem_b = _make_memory("Content B", "crystallized", "Crystal B")
        mem_c = _make_memory("Content C", "crystallized", "Crystal C")

        _create_thread("Thread Over Budget A", "Medium thread A", "X" * 600, [mem_a])
        _create_thread("Thread Over Budget B", "Large thread B", "Y" * 800, [mem_b])
        _create_thread("Thread Over Budget C", "Small thread C", "Z" * 200, [mem_c])

        result = engine.inject_for_session(session_id="budget_over_sess")

        assert "Thread Over Budget C" in result
        assert "Thread Over Budget A" in result
        assert "Thread Over Budget B" not in result

    def test_single_thread_over_narrative_cap_is_truncated(self, base, engine):
        """A thread with a 1 200-char narrative is truncated to <= 1 000 chars ending with '.'."""
        sentence = "Sentence one about async patterns. "
        long_narrative = sentence * 35

        assert len(long_narrative) > 1_000

        mid = _make_memory(
            "Crystallized content for cap test", "crystallized", "Crystal Cap",
        )
        _create_thread("Thread Cap Test", "Thread with long narrative", long_narrative, [mid])

        result = engine.inject_for_session(session_id="cap_test_sess")

        assert "Thread Cap Test" in result

        lines = result.splitlines()
        thread_header_idx = next(
            i for i, line in enumerate(lines) if "Thread Cap Test" in line
        )
        narrative_lines = []
        for line in lines[thread_header_idx + 1:]:
            if line.startswith("##") or line.startswith("---"):
                break
            narrative_lines.append(line)
        injected_narrative = "\n".join(narrative_lines).strip()

        assert len(injected_narrative) <= 1_000
        assert injected_narrative.endswith(".")

    def test_shortest_first_maximises_arc_count(self, base, engine, monkeypatch):
        """Greedy shortest-first packing picks 3 short threads over 1 long one."""
        import core.retrieval as retrieval_module

        monkeypatch.setattr(retrieval_module, "THREAD_BUDGET_CHARS", 1_800)

        mem_long = _make_memory("Long content", "crystallized", "Crystal Long")
        _create_thread("Thread Long Arc", "Very long arc", "L" * 900, [mem_long])

        short_titles = []
        for i in range(3):
            mid = _make_memory(f"Short content {i}", "crystallized", f"Crystal Short Max {i}")
            title = f"Thread Short Arc {i}"
            short_titles.append(title)
            _create_thread(title, f"Short arc {i}", "S" * 400, [mid])

        result = engine.inject_for_session(session_id="greedy_sess")

        for title in short_titles:
            assert title in result, f"Expected short thread '{title}' in output"
        assert "Thread Long Arc" not in result

    def test_budget_zero_excludes_all(self, base, engine, monkeypatch):
        """When THREAD_BUDGET_CHARS is 0, no Narrative Threads section appears."""
        import core.retrieval as retrieval_module

        monkeypatch.setattr(retrieval_module, "THREAD_BUDGET_CHARS", 0)

        mid = _make_memory("Crystallized content zero budget", "crystallized", "Crystal Zero")
        _create_thread("Thread Zero Budget", "Should not appear", "Narrative that should be excluded.", [mid])

        result = engine.inject_for_session(session_id="zero_budget_sess")
        assert "Narrative Threads" not in result


# ---------------------------------------------------------------------------
# D4 -- last_surfaced_at tracking
# ---------------------------------------------------------------------------


class TestLastSurfacedAtTracking:
    """Tests for lazy last_surfaced_at updates in _get_thread_narratives."""

    def test_surfaced_threads_get_timestamp(self, base, engine):
        """After inject_for_session, a surfaced thread has last_surfaced_at set."""
        mid = _make_memory("Crystallized surfaced content", "crystallized", "Crystal Surfaced")
        tid = _create_thread("Thread Surfaced", "Will be surfaced", "A short narrative that will be surfaced.", [mid])

        thread = NarrativeThread.get_by_id(tid)
        assert thread.last_surfaced_at is None

        engine.inject_for_session(session_id="surf_sess")

        thread = NarrativeThread.get_by_id(tid)
        assert thread.last_surfaced_at is not None

    def test_non_surfaced_threads_unchanged(self, base, engine, monkeypatch):
        """A thread not surfaced (memory excluded from tier2) keeps last_surfaced_at=None."""
        mem_a = _make_memory("Small content fits", "crystallized", "Crystal In Budget", importance=0.9)
        mem_b = _make_memory("B" * 500, "crystallized", "Crystal Out Budget", importance=0.1)

        tid_a = _create_thread("Thread In Budget", "Should be surfaced", "Narrative for in-budget thread.", [mem_a])
        tid_b = _create_thread("Thread Out Budget", "Should not be surfaced", "Narrative for out-budget thread.", [mem_b])

        monkeypatch.setattr(engine, "token_limit", 200)
        engine.inject_for_session(session_id="non_surf_sess")

        assert NarrativeThread.get_by_id(tid_a).last_surfaced_at is not None
        assert NarrativeThread.get_by_id(tid_b).last_surfaced_at is None
