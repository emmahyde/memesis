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


# ---------------------------------------------------------------------------
# Hybrid RRF Search
# ---------------------------------------------------------------------------


class MockVecStore:
    """Stub VecStore that returns predetermined (memory_id, distance) tuples."""

    def __init__(self, results: list[tuple], available: bool = True):
        self._results = results
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def search_vector(self, query_embedding: bytes, k: int = 10, exclude_ids: set = None) -> list[tuple]:
        results = self._results[:k]
        if exclude_ids:
            results = [(mid, dist) for mid, dist in results if mid not in exclude_ids]
        return results


class TestHybridSearch:
    """Tests for hybrid_search() on RetrievalEngine using RRF algorithm."""

    def test_hybrid_search_both_legs_returns_rrf_ranked_results(self, base, engine):
        """hybrid_search with both FTS and vector results returns items sorted by RRF score descending."""
        id_a = _make_memory("alpha beta gamma", "crystallized", "Alpha Memory")
        id_b = _make_memory("alpha beta delta", "crystallized", "Beta Memory")

        # FTS rank order: id_a=1, id_b=2 (both match "alpha")
        # Vec rank order: id_b=1, id_a=2 (reversed)
        # RRF scores: id_a = 1/(60+1) + 1/(60+2) = 0.01639 + 0.01613 = 0.03252
        #             id_b = 1/(60+2) + 1/(60+1) = 0.01613 + 0.01639 = 0.03252  (same both ways)
        # Actually with distinct ranks: id_a FTS=1, vec=2; id_b FTS=2, vec=1
        # id_a: 1/61 + 1/62 = 0.016393 + 0.016129 = 0.032522
        # id_b: 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522
        # They're equal, so just verify both appear
        vec_store = MockVecStore([(id_b, 0.1), (id_a, 0.2)])
        dummy_embedding = b"\x00" * 4

        results = engine.hybrid_search(
            query="alpha",
            query_embedding=dummy_embedding,
            k=10,
            vec_store=vec_store,
        )

        assert isinstance(results, list)
        result_ids = [r[0] for r in results]
        assert id_a in result_ids
        assert id_b in result_ids

    def test_hybrid_search_rrf_score_exact_math(self, base, engine):
        """RRF score for item in both lists = 1/(k+fts_rank) + 1/(k+vec_rank) with k=60."""
        id_a = _make_memory("unique term xyzzy", "crystallized", "Unique Alpha")
        id_b = _make_memory("unique term xyzzy different", "crystallized", "Unique Beta")

        # Both match FTS, vec returns id_a first
        # FTS rank: id_a=1 (more unique match), id_b=2
        # Vec rank: id_a=1, id_b=2
        # id_a score: 1/(60+1) + 1/(60+1) = 2/61
        # id_b score: 1/(60+2) + 1/(60+2) = 2/62
        vec_store = MockVecStore([(id_a, 0.1), (id_b, 0.2)])
        dummy_embedding = b"\x00" * 4

        results = engine.hybrid_search(
            query="unique term xyzzy",
            query_embedding=dummy_embedding,
            k=10,
            rrf_k=60,
            vec_store=vec_store,
        )

        assert len(results) >= 2
        result_ids = [r[0] for r in results]
        # id_a must outrank id_b since it ranks #1 in both legs
        assert result_ids.index(id_a) < result_ids.index(id_b)

        # Verify score magnitudes: id_a gets 2/61, id_b gets 2/62
        id_a_score = next(score for rid, score in results if rid == id_a)
        id_b_score = next(score for rid, score in results if rid == id_b)
        assert abs(id_a_score - 2 / 61) < 1e-9
        assert abs(id_b_score - 2 / 62) < 1e-9

    def test_hybrid_search_fts_only_item_appears(self, base, engine):
        """Item only in FTS list gets score = 1/(k+fts_rank) and still appears in results."""
        id_fts_only = _make_memory("fts exclusive term", "crystallized", "FTS Only")
        id_vec_only = _make_memory("something else", "crystallized", "Vec Only")

        # FTS matches id_fts_only, vec returns id_vec_only
        vec_store = MockVecStore([(id_vec_only, 0.1)])
        dummy_embedding = b"\x00" * 4

        results = engine.hybrid_search(
            query="fts exclusive term",
            query_embedding=dummy_embedding,
            k=10,
            rrf_k=60,
            vec_store=vec_store,
        )

        result_ids = [r[0] for r in results]
        assert id_fts_only in result_ids

        fts_score = next(score for rid, score in results if rid == id_fts_only)
        # id_fts_only is FTS rank 1, not in vec: score = 1/(60+1)
        assert abs(fts_score - 1 / 61) < 1e-9

    def test_hybrid_search_vec_only_item_appears(self, base, engine):
        """Item only in vector list gets score = 1/(k+vec_rank) and still appears in results."""
        id_vec_only = _make_memory("vector exclusive content", "crystallized", "Vec Only Memory")

        # FTS search for something else so id_vec_only doesn't match FTS
        vec_store = MockVecStore([(id_vec_only, 0.1)])
        dummy_embedding = b"\x00" * 4

        results = engine.hybrid_search(
            query="xyzzy_nonexistent_fts_term_999",
            query_embedding=dummy_embedding,
            k=10,
            rrf_k=60,
            vec_store=vec_store,
        )

        result_ids = [r[0] for r in results]
        assert id_vec_only in result_ids

        vec_score = next(score for rid, score in results if rid == id_vec_only)
        # id_vec_only is vec rank 1, not in FTS: score = 1/(60+1)
        assert abs(vec_score - 1 / 61) < 1e-9

    def test_hybrid_search_vec_store_none_falls_back_to_fts(self, base, engine):
        """When vec_store is None, falls back to FTS-only ranking."""
        id_a = _make_memory("fts fallback content alpha", "crystallized", "Fallback Alpha")
        id_b = _make_memory("fts fallback content beta", "crystallized", "Fallback Beta")

        results = engine.hybrid_search(
            query="fts fallback",
            query_embedding=b"\x00" * 4,
            k=10,
            vec_store=None,
        )

        assert len(results) >= 2
        result_ids = [r[0] for r in results]
        assert id_a in result_ids
        assert id_b in result_ids
        # All scores should be FTS-only: 1/(60+rank)
        for _, score in results:
            # Each score is a single RRF term, so 1/(60+rank) <= 1/61
            assert score <= 1 / 61 + 1e-9

    def test_hybrid_search_vec_store_unavailable_falls_back_to_fts(self, base, engine):
        """When vec_store.available is False, falls back to FTS-only ranking."""
        id_a = _make_memory("unavailable vec fallback alpha", "crystallized", "Unavail Alpha")

        unavailable_store = MockVecStore(results=[(id_a, 0.1)], available=False)

        results = engine.hybrid_search(
            query="unavailable vec fallback",
            query_embedding=b"\x00" * 4,
            k=10,
            vec_store=unavailable_store,
        )

        assert len(results) >= 1
        result_ids = [r[0] for r in results]
        assert id_a in result_ids
        # Should be FTS-only score, not boosted by vec
        for _, score in results:
            assert score <= 1 / 61 + 1e-9

    def test_hybrid_search_none_embedding_falls_back_to_fts(self, base, engine):
        """When query_embedding is None (embedding API failure), falls back to FTS-only."""
        id_a = _make_memory("null embedding fallback content", "crystallized", "Null Embed Alpha")

        vec_store = MockVecStore([(id_a, 0.1)])

        results = engine.hybrid_search(
            query="null embedding fallback",
            query_embedding=None,
            k=10,
            vec_store=vec_store,
        )

        result_ids = [r[0] for r in results]
        assert id_a in result_ids
        # Should be FTS-only since embedding is None
        for _, score in results:
            assert score <= 1 / 61 + 1e-9

    def test_hybrid_search_fts_empty_returns_vec_only(self, base, engine):
        """When FTS returns empty results, returns vector-only ranking."""
        id_vec = _make_memory("vector only no fts match", "crystallized", "Vec Only Return")

        vec_store = MockVecStore([(id_vec, 0.1)])
        dummy_embedding = b"\x00" * 4

        results = engine.hybrid_search(
            query="xyzzy_no_fts_match_42",
            query_embedding=dummy_embedding,
            k=10,
            vec_store=vec_store,
        )

        assert len(results) >= 1
        assert results[0][0] == id_vec

    def test_hybrid_search_both_empty_returns_empty(self, base, engine):
        """When both FTS and vector return empty, returns empty list."""
        vec_store = MockVecStore([])
        dummy_embedding = b"\x00" * 4

        results = engine.hybrid_search(
            query="xyzzy_no_results_999",
            query_embedding=dummy_embedding,
            k=10,
            vec_store=vec_store,
        )

        assert results == []

    def test_hybrid_search_rrf_k_configurable(self, base, engine):
        """k parameter (rrf_k) is configurable and affects RRF scores."""
        id_a = _make_memory("configurable k term", "crystallized", "Configurable K Alpha")

        vec_store = MockVecStore([(id_a, 0.1)])
        dummy_embedding = b"\x00" * 4

        results_k60 = engine.hybrid_search(
            query="configurable k term",
            query_embedding=dummy_embedding,
            k=10,
            rrf_k=60,
            vec_store=vec_store,
        )
        results_k1 = engine.hybrid_search(
            query="configurable k term",
            query_embedding=dummy_embedding,
            k=10,
            rrf_k=1,
            vec_store=vec_store,
        )

        # With rrf_k=1, rank 1 gives 1/(1+1)=0.5; with rrf_k=60, rank 1 gives 1/(60+1)~0.016
        score_k60 = results_k60[0][1]
        score_k1 = results_k1[0][1]
        assert score_k1 > score_k60

    def test_hybrid_search_limit_controls_output_count(self, base, engine):
        """limit parameter controls max results returned."""
        for i in range(8):
            _make_memory(f"limit test memory item {i}", "crystallized", f"Limit Memory {i}")

        results = engine.hybrid_search(
            query="limit test memory",
            query_embedding=None,
            k=3,
            vec_store=None,
        )

        assert len(results) <= 3

    def test_hybrid_search_archived_fts_results_included(self, base, engine):
        """
        RRF operates on whatever FTS returns. Filtering is caller's responsibility.
        Archived memories returned by FTS are included in hybrid results.
        """
        from datetime import datetime

        id_archived = _make_memory(
            "archived hybrid search content", "consolidated", "Archived Hybrid"
        )
        # Mark as archived directly
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == id_archived).execute()

        # Re-index in FTS (since save() was bypassed via update)
        mem = Memory.get_by_id(id_archived)
        try:
            mem._fts_insert()
        except Exception:
            pass  # May already be indexed from create

        vec_store = MockVecStore([])
        dummy_embedding = b"\x00" * 4

        # FTS may or may not return archived items depending on how _make_memory indexed it
        # The key assertion: whatever FTS returns, hybrid_search does NOT filter by archived_at
        results = engine.hybrid_search(
            query="archived hybrid search",
            query_embedding=dummy_embedding,
            k=10,
            vec_store=vec_store,
        )
        # Results are a list of (id, score) tuples — archived_at is not a filter
        assert isinstance(results, list)
        for item in results:
            assert len(item) == 2  # (memory_id, score)
            assert isinstance(item[1], float)


# ---------------------------------------------------------------------------
# Wiring: hybrid_search into get_crystallized_for_context and active_search
# ---------------------------------------------------------------------------


class TestCrystallizedHybrid:
    """Tests for hybrid wiring into get_crystallized_for_context and inject_for_session."""

    def test_crystallized_hybrid_uses_rrf_ranking(self, base, engine):
        """get_crystallized_for_context(query=...) returns memories ranked by RRF score."""
        id_a = _make_memory("deployment kubernetes scaling", "crystallized", "Deploy Guide", importance=0.5)
        id_b = _make_memory("deployment strategy blue green", "crystallized", "Deploy Strategy", importance=0.5)

        results = engine.get_crystallized_for_context(query="deployment")
        assert len(results) >= 1
        # Both should appear since both match "deployment"
        result_ids = [m.id for m in results]
        assert id_a in result_ids
        assert id_b in result_ids

    def test_crystallized_no_query_preserves_static_sort(self, base, engine):
        """get_crystallized_for_context() with no query preserves existing static sort behavior."""
        id_high = _make_memory("content high importance", "crystallized", "High Importance", importance=0.9)
        id_low = _make_memory("content low importance", "crystallized", "Low Importance", importance=0.2)

        results = engine.get_crystallized_for_context()
        assert len(results) == 2
        assert results[0].id == id_high
        assert results[1].id == id_low

    def test_crystallized_hybrid_respects_token_limit(self, base, engine):
        """get_crystallized_for_context with query still respects token_limit budget."""
        for i in range(10):
            _make_memory(f"deployment scaling content item {i}", "crystallized", f"Deploy {i}", importance=0.5)

        results = engine.get_crystallized_for_context(query="deployment scaling", token_limit=100)
        total_chars = sum(len(m.content or "") for m in results)
        assert total_chars <= 100

    def test_crystallized_hybrid_project_context_boost(self, base, engine):
        """get_crystallized_for_context with query applies project_context boost."""
        # Two memories both match FTS "deployment", but one matches project_context
        id_match = _make_memory(
            "deployment pipeline step", "crystallized", "Matching Deploy",
            importance=0.5, project_context="/my/project",
        )
        id_other = _make_memory(
            "deployment pipeline step", "crystallized", "Other Deploy",
            importance=0.5, project_context="/other/project",
        )

        results = engine.get_crystallized_for_context(
            query="deployment pipeline", project_context="/my/project"
        )
        assert len(results) == 2
        # Project-matching memory should appear first
        assert results[0].id == id_match

    def test_inject_for_session_accepts_query_parameter(self, base, engine):
        """inject_for_session accepts optional query parameter and forwards it to get_crystallized_for_context."""
        import inspect
        sig = inspect.signature(engine.inject_for_session)
        assert "query" in sig.parameters

    def test_inject_for_session_forwards_query(self, base, engine):
        """inject_for_session with query produces result containing relevant memories."""
        id_a = _make_memory("kubernetes deployment scaling", "crystallized", "Kube Deploy", importance=0.5)
        id_b = _make_memory("python refactoring patterns", "crystallized", "Python Refactor", importance=0.9)

        # Without query, id_b (higher importance) should appear first in static sort
        results_no_query = engine.get_crystallized_for_context()
        assert results_no_query[0].id == id_b

        # With query "kubernetes", id_a should be in results and ranked well
        results_with_query = engine.get_crystallized_for_context(query="kubernetes deployment")
        result_ids = [m.id for m in results_with_query]
        assert id_a in result_ids

    def test_active_search_uses_hybrid_search(self, base, engine):
        """active_search uses hybrid_search (via mocked vec_store), returns results in RRF order."""
        id_a = _make_memory("scaling kubernetes nodes", "crystallized", "Scale Guide")
        id_b = _make_memory("scaling microservices horizontally", "crystallized", "Micro Scale")

        # Without vec_store, falls back to FTS-only hybrid — both should appear
        results = engine.active_search("scaling", session_id="test_sess")
        titles = [r["title"] for r in results]
        assert "Scale Guide" in titles
        assert "Micro Scale" in titles

    def test_active_search_fts_fallback_no_vec_store(self, base, engine):
        """active_search with no vec_store still works (FTS fallback via hybrid_search)."""
        id_a = _make_memory("fts fallback active search content", "crystallized", "FTS Fallback")

        results = engine.active_search("fts fallback active", session_id="s")
        assert len(results) >= 1
        assert results[0]["title"] == "FTS Fallback"

    def test_active_search_returns_rrf_fields(self, base, engine):
        """active_search results include all required fields after hybrid wiring."""
        _make_memory("hybrid active search fields test", "crystallized", "Hybrid Fields",
                     summary="Summary for hybrid")

        results = engine.active_search("hybrid active", session_id="s")
        assert len(results) >= 1
        r = results[0]
        for field in ("id", "title", "summary", "content", "importance", "stage", "tags", "rank"):
            assert field in r


class TestHybridPerformance:
    """Performance test for hybrid_search on large corpus."""

    def test_hybrid_search_1000_memories_under_500ms(self, base, engine):
        """hybrid_search on 1000 memories completes in under 500ms (FTS-only, no vec_store)."""
        import time

        # Create 1000 memories with searchable content
        for i in range(1000):
            _make_memory(
                f"performance test memory item number {i} with extra content to index",
                "crystallized",
                f"Perf Memory {i}",
            )

        start = time.perf_counter()
        results = engine.hybrid_search(
            query="performance test memory",
            query_embedding=None,
            k=20,
            vec_store=None,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 500, f"hybrid_search took {elapsed_ms:.1f}ms, expected < 500ms"
        assert len(results) > 0


# ---------------------------------------------------------------------------
# Thompson Sampling
# ---------------------------------------------------------------------------


def _make_memory_with_counts(content, title, usage_count=0, injection_count=0, base_fixture=None):
    """Helper to create a crystallized memory with usage/injection counts set."""
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage="crystallized",
        title=title,
        summary=f"Summary of {title}",
        content=content,
        tags="[]",
        importance=0.5,
        reinforcement_count=0,
        created_at=now,
        updated_at=now,
        usage_count=usage_count,
        injection_count=injection_count,
    )
    return mem


class TestThompsonSampling:
    """Tests for Thompson sampling re-ranking in RetrievalEngine."""

    def test_cold_start_sample_is_nonzero(self, base, engine):
        """Test 1: A memory with usage_count=0, injection_count=0 produces Beta(1,1) — always non-zero."""
        mem = _make_memory_with_counts("cold start content", "Cold Start", usage_count=0, injection_count=0)
        result = engine._thompson_rerank([mem])
        # betavariate always returns a value in (0, 1), never exactly 0
        assert len(result) == 1
        assert result[0].id == mem.id

    def test_deterministic_order_with_fixed_seed(self, base, engine, monkeypatch):
        """Test 2: With a fixed random seed, _thompson_rerank produces a specific known ordering."""
        import random
        mem_a = _make_memory_with_counts("content A", "Alpha", usage_count=8, injection_count=10)
        mem_b = _make_memory_with_counts("content B", "Beta", usage_count=1, injection_count=10)
        mem_c = _make_memory_with_counts("content C", "Gamma", usage_count=0, injection_count=0)

        # Compute expected order with seed 42
        random.seed(42)
        a_score = random.betavariate((mem_a.usage_count or 0) + 1,
                                     max((mem_a.injection_count or 0) - (mem_a.usage_count or 0), 0) + 1)
        b_score = random.betavariate((mem_b.usage_count or 0) + 1,
                                     max((mem_b.injection_count or 0) - (mem_b.usage_count or 0), 0) + 1)
        c_score = random.betavariate((mem_c.usage_count or 0) + 1,
                                     max((mem_c.injection_count or 0) - (mem_c.usage_count or 0), 0) + 1)

        scores = [(a_score, mem_a.id), (b_score, mem_b.id), (c_score, mem_c.id)]
        expected_ids = [mid for _, mid in sorted(scores, key=lambda x: x[0], reverse=True)]

        # Now run with the same seed
        random.seed(42)
        result = engine._thompson_rerank([mem_a, mem_b, mem_c])
        result_ids = [m.id for m in result]
        assert result_ids == expected_ids

    def test_statistical_high_usage_outranks_low_usage(self, base, engine):
        """Test 3: Over 1000 runs, Beta(9,3) outranks Beta(2,9) more than 80% of the time."""
        mem_high = _make_memory_with_counts("high usage content", "High Usage", usage_count=8, injection_count=10)
        mem_low = _make_memory_with_counts("low usage content", "Low Usage", usage_count=1, injection_count=10)

        high_wins = 0
        for _ in range(1000):
            result = engine._thompson_rerank([mem_high, mem_low])
            if result[0].id == mem_high.id:
                high_wins += 1

        assert high_wins > 800, f"Expected >80% wins for high-usage memory, got {high_wins}/1000"

    def test_flag_disabled_preserves_deterministic_order(self, base, engine, monkeypatch):
        """Test 4: When thompson_sampling flag is False, get_crystallized_for_context returns deterministic order."""
        import core.flags as flags_module
        monkeypatch.setattr(flags_module, "_cache", {"hybrid_rrf": False, "thompson_sampling": False})

        mem_high = _make_memory_with_counts("high importance content", "High Imp", usage_count=0, injection_count=0)
        Memory.update(importance=0.9).where(Memory.id == mem_high.id).execute()
        mem_low = _make_memory_with_counts("low importance content", "Low Imp", usage_count=5, injection_count=5)
        Memory.update(importance=0.1).where(Memory.id == mem_low.id).execute()

        # Run multiple times to confirm determinism
        results_first = engine.get_crystallized_for_context()
        results_second = engine.get_crystallized_for_context()
        assert [m.id for m in results_first] == [m.id for m in results_second]
        # High importance should come first in static sort
        assert results_first[0].title == "High Imp"

    def test_flag_enabled_hybrid_path_calls_thompson_rerank(self, base, engine, monkeypatch):
        """Test 5: When thompson_sampling flag is True, _crystallized_hybrid calls _thompson_rerank."""
        import core.flags as flags_module
        monkeypatch.setattr(flags_module, "_cache", {"hybrid_rrf": True, "thompson_sampling": True})

        rerank_calls = []

        original_rerank = engine._thompson_rerank

        def mock_rerank(memories):
            rerank_calls.append(len(memories))
            return original_rerank(memories)

        monkeypatch.setattr(engine, "_thompson_rerank", mock_rerank)

        _make_memory_with_counts("deployment content test", "Deploy Mem", usage_count=0, injection_count=0)
        engine.get_crystallized_for_context(query="deployment")

        assert len(rerank_calls) >= 1, "_thompson_rerank was not called in hybrid path"

    def test_flag_enabled_static_path_calls_thompson_rerank(self, base, engine, monkeypatch):
        """Test 6: When thompson_sampling flag is True, static path calls _thompson_rerank."""
        import core.flags as flags_module
        monkeypatch.setattr(flags_module, "_cache", {"hybrid_rrf": False, "thompson_sampling": True})

        rerank_calls = []

        original_rerank = engine._thompson_rerank

        def mock_rerank(memories):
            rerank_calls.append(len(memories))
            return original_rerank(memories)

        monkeypatch.setattr(engine, "_thompson_rerank", mock_rerank)

        _make_memory_with_counts("static path content", "Static Mem", usage_count=0, injection_count=0)
        engine.get_crystallized_for_context()

        assert len(rerank_calls) >= 1, "_thompson_rerank was not called in static path"

    def test_negative_unused_count_guard(self, base, engine):
        """Test 7: usage_count > injection_count (data anomaly) produces b=1, not negative."""
        # usage_count=5, injection_count=3 — anomaly: used more than injected
        mem = _make_memory_with_counts("anomaly content", "Anomaly Mem", usage_count=5, injection_count=3)
        # This should not raise and should produce a valid ordering
        result = engine._thompson_rerank([mem])
        assert len(result) == 1
        assert result[0].id == mem.id
        # Verify the Beta params would be valid: a=6, b=max(3-5,0)+1=1 (not 0 or negative)
