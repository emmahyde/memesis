"""
Tests for the UserPromptSubmit hook — just-in-time memory injection.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.models import Memory, RetrievalLog
from hooks.user_prompt_inject import extract_query_terms, search_and_inject, get_already_injected


@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _make_memory(stage='consolidated', title='Test', content='Content', tags=None, **kw):
    now = datetime.now().isoformat()
    return Memory.create(
        stage=stage, title=title, summary=kw.get('summary', f'Summary of {title}'),
        content=content, tags=json.dumps(tags or []),
        importance=kw.get('importance', 0.5), created_at=now, updated_at=now,
    )


def _record_injection(memory_id, session_id):
    now = datetime.now().isoformat()
    Memory.update(last_injected_at=now, injection_count=Memory.injection_count + 1).where(Memory.id == memory_id).execute()
    RetrievalLog.create(timestamp=now, session_id=session_id, memory_id=memory_id, retrieval_type='injected')


def _archive(memory_id):
    Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == memory_id).execute()


class TestExtractQueryTerms:
    def test_extracts_significant_words(self):
        terms = extract_query_terms("How do I fix the payment pipeline deadlock?")
        assert "payment" in terms
        assert "pipeline" in terms
        assert "deadlock" in terms

    def test_filters_short_words(self):
        terms = extract_query_terms("I am so not ok")
        assert len(terms) == 0

    def test_filters_stop_words(self):
        terms = extract_query_terms("please help me with this code")
        assert "please" not in terms
        assert "code" not in terms

    def test_strips_code_fences(self):
        prompt = "Look at this:\n```python\ndef foo():\n    pass\n```\nIs it correct?"
        terms = extract_query_terms(prompt)
        assert "pass" not in terms

    def test_strips_inline_code(self):
        terms = extract_query_terms("The `payment_processor` function needs fixing")
        assert "function" in terms
        assert "fixing" in terms

    def test_deduplicates(self):
        terms = extract_query_terms("payment payment payment pipeline")
        assert terms.count("payment") == 1

    def test_caps_at_ten_terms(self):
        long_prompt = " ".join(f"word{i}" for i in range(100))
        assert len(extract_query_terms(long_prompt)) <= 10

    def test_empty_prompt(self):
        assert extract_query_terms("") == []

    def test_only_punctuation(self):
        assert extract_query_terms("!!! ??? ...") == []


class TestSearchAndInject:
    def test_returns_empty_for_no_matches(self, base):
        assert search_and_inject("something unrelated", "sess-001") == ""

    def test_returns_empty_for_empty_prompt(self, base):
        assert search_and_inject("", "sess-001") == ""

    def test_finds_matching_memory(self, base):
        _make_memory(stage='consolidated', title='Payment Pipeline Locking',
                     content='Always check lock ordering in the payment pipeline.',
                     summary='Check lock ordering to prevent deadlocks.', tags=["payment", "concurrency"])
        result = search_and_inject("I need to fix the payment pipeline deadlock issue", "sess-001")
        assert "Payment Pipeline Locking" in result

    def test_excludes_already_injected(self, base):
        mem = _make_memory(stage='crystallized', title='Already Injected Memory',
                           content='Already loaded.', summary='Injected at SessionStart.', tags=["python"])
        _record_injection(mem.id, "sess-001")
        result = search_and_inject("Tell me about the already injected memory for python", "sess-001")
        assert "Already Injected" not in result

    def test_excludes_archived_memories(self, base):
        mem = _make_memory(stage='consolidated', title='Archived Payment Info',
                           content='Archived.', summary='Old payment details.', tags=["payment"])
        _archive(mem.id)
        result = search_and_inject("payment pipeline issues", "sess-001")
        assert "Archived Payment" not in result

    def test_excludes_ephemeral(self, base):
        _make_memory(stage='ephemeral', title='Deployment Scratch',
                     content='Scratch observation about deployment.', summary='Ephemeral note.', tags=["deployment"])
        result = search_and_inject("deployment pipeline configuration", "sess-001")
        assert "Deployment Scratch" not in result

    def test_respects_token_budget(self, base):
        _make_memory(stage='consolidated', title='Very Long Memory About Kubernetes',
                     content='X' * 5000, summary='A' * 3000, tags=["kubernetes"])
        result = search_and_inject("kubernetes deployment strategy", "sess-001")
        assert len(result) <= 2500


class TestAlreadyInjected:
    def test_empty_session(self, base):
        assert get_already_injected("new-session") == set()

    def test_returns_injected_ids(self, base):
        mem = _make_memory(stage='crystallized')
        _record_injection(mem.id, "sess-001")
        assert mem.id in get_already_injected("sess-001")

    def test_session_isolation(self, base):
        mem = _make_memory(stage='crystallized')
        _record_injection(mem.id, "sess-001")
        assert mem.id not in get_already_injected("sess-002")


class TestSearchAndInjectHybrid:
    """Tests for hybrid wiring in search_and_inject (via RetrievalEngine)."""

    def test_search_and_inject_uses_hybrid_search(self, base, monkeypatch):
        """search_and_inject calls hybrid_search via RetrievalEngine instead of raw Memory.search_fts."""
        from core.retrieval import RetrievalEngine

        _make_memory(stage='consolidated', title='Hybrid Payment Memory',
                     content='payment pipeline processing hybrid search',
                     summary='Hybrid payment processing details.')

        hybrid_called = []

        original_hybrid = RetrievalEngine.hybrid_search
        def spy_hybrid(self, query, query_embedding=None, k=20, rrf_k=60, vec_store=None):
            hybrid_called.append(query)
            return original_hybrid(self, query, query_embedding, k, rrf_k, vec_store)

        monkeypatch.setattr(RetrievalEngine, 'hybrid_search', spy_hybrid)

        result = search_and_inject("payment pipeline processing", "sess-hybrid-001")

        # hybrid_search should have been called
        assert len(hybrid_called) > 0, "hybrid_search was not called"
        assert "Hybrid Payment Memory" in result

    def test_search_and_inject_works_when_embedding_unavailable(self, base, monkeypatch):
        """search_and_inject still works when embedding is unavailable (FTS-only fallback)."""
        import core.embeddings as embeddings_module
        monkeypatch.setattr(embeddings_module, 'embed_text', lambda text, **kw: None)

        _make_memory(stage='consolidated', title='Fallback Memory',
                     content='fallback embedding unavailable content',
                     summary='Fallback when embedding fails.')

        result = search_and_inject("fallback embedding unavailable", "sess-fallback-001")
        assert "Fallback Memory" in result

    def test_search_and_inject_excludes_already_injected(self, base):
        """search_and_inject still respects already-injected exclusion."""
        mem = _make_memory(stage='consolidated', title='Already Injected Hybrid',
                           content='hybrid already injected content test',
                           summary='Already loaded via hybrid.')
        _record_injection(mem.id, "sess-excl-001")

        result = search_and_inject("hybrid already injected content test", "sess-excl-001")
        assert "Already Injected Hybrid" not in result

    def test_search_and_inject_respects_max_memories(self, base):
        """search_and_inject still respects MAX_MEMORIES limit."""
        for i in range(10):
            _make_memory(stage='consolidated', title=f'Hybrid Max Memory {i}',
                         content=f'hybrid maximum memories limit test content item {i}',
                         summary=f'Hybrid memory {i}.')

        result = search_and_inject("hybrid maximum memories limit test", "sess-max-001")

        # Count the injected memory markers
        memory_count = result.count("[Memory:")
        from hooks.user_prompt_inject import MAX_MEMORIES
        assert memory_count <= MAX_MEMORIES

    def test_search_and_inject_respects_token_budget(self, base):
        """search_and_inject still respects TOKEN_BUDGET_CHARS limits."""
        _make_memory(stage='consolidated', title='Very Long Hybrid Memory',
                     content='X' * 5000,
                     summary='A' * 3000)

        result = search_and_inject("kubernetes deployment strategy", "sess-budget-001")
        from hooks.user_prompt_inject import TOKEN_BUDGET_CHARS
        assert len(result) <= TOKEN_BUDGET_CHARS + 200  # small overhead allowance for formatting


class TestHybridPerformanceUserPrompt:
    """Performance test for hybrid search in the user prompt inject path."""

    def test_hybrid_search_1000_memories_under_500ms(self, base):
        """hybrid_search on 1000 memories completes in under 500ms (no embedding API call)."""
        import time
        from core.retrieval import RetrievalEngine

        # Create 1000 memories with searchable content
        for i in range(1000):
            _make_memory(
                stage='crystallized',
                title=f'Perf Test Memory {i}',
                content=f'performance test memory item number {i} with extra content for indexing',
            )

        engine = RetrievalEngine()
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


class TestTier2PromptInjection:
    """Tests proving Tier 2 crystallized retrieval is called with prompt text."""

    def test_search_and_inject_calls_get_crystallized_for_context(self, base, monkeypatch):
        """search_and_inject calls get_crystallized_for_context with query and query_embedding."""
        from core.retrieval import RetrievalEngine

        _make_memory(stage='crystallized', title='Crystallized Vector Memory',
                     content='sqlite-vec vector embedding storage integration test',
                     summary='Notes on sqlite-vec integration.')

        tier2_calls = []
        original = RetrievalEngine.get_crystallized_for_context

        def spy_tier2(self, project_context=None, token_limit=None, query=None, query_embedding=None):
            tier2_calls.append({'query': query, 'query_embedding': query_embedding})
            return original(self, project_context=project_context, token_limit=token_limit,
                            query=query, query_embedding=query_embedding)

        monkeypatch.setattr(RetrievalEngine, 'get_crystallized_for_context', spy_tier2)

        search_and_inject("Tell me about sqlite-vec embedding storage", "sess-tier2-001")

        assert len(tier2_calls) > 0, "get_crystallized_for_context was not called"
        assert tier2_calls[0]['query'] is not None, "query arg was None (must be prompt-derived)"
        assert "sqlite" in tier2_calls[0]['query'].lower() or "vec" in tier2_calls[0]['query'].lower()

    def test_crystallized_memories_surface_via_tier2_path(self, base, monkeypatch):
        """Crystallized memories matching the prompt surface through the Tier 2 path."""
        from core.retrieval import RetrievalEngine

        # Memory with crystallized stage — should be returned via Tier 2
        _make_memory(stage='crystallized', title='SQLite-Vec Embedding Guide',
                     content='sqlite-vec enables vector similarity search in sqlite databases.',
                     summary='How to use sqlite-vec for vector storage.')

        # Patch embed_text to return None so test is deterministic (FTS-only)
        import core.embeddings as embeddings_module
        monkeypatch.setattr(embeddings_module, 'embed_text', lambda text, **kw: None)

        result = search_and_inject("How does sqlite-vec embedding work", "sess-tier2-002")
        assert "SQLite-Vec Embedding Guide" in result, (
            "Expected Tier 2 crystallized memory to surface in injection result"
        )

    def test_already_injected_crystallized_memories_not_reinjected(self, base, monkeypatch):
        """Memories already injected at SessionStart are not re-injected via Tier 2."""
        import core.embeddings as embeddings_module
        monkeypatch.setattr(embeddings_module, 'embed_text', lambda text, **kw: None)

        mem = _make_memory(stage='crystallized', title='Already Loaded Crystallized',
                           content='crystallized content already loaded at session start.',
                           summary='Pre-loaded at session start.')
        _record_injection(mem.id, "sess-tier2-003")

        result = search_and_inject("crystallized content already loaded at session start", "sess-tier2-003")
        assert "Already Loaded Crystallized" not in result, (
            "Already-injected crystallized memory should be deduplicated"
        )

    def test_tier2_receives_fts_query_when_embedding_fails(self, base, monkeypatch):
        """When embedding API fails, Tier 2 still receives the FTS query (query_embedding=None fallback)."""
        from core.retrieval import RetrievalEngine

        _make_memory(stage='crystallized', title='Fallback FTS Memory',
                     content='fallback fts search query crystallized content.',
                     summary='Should be found via FTS.')

        tier2_calls = []
        original = RetrievalEngine.get_crystallized_for_context

        def spy_tier2(self, project_context=None, token_limit=None, query=None, query_embedding=None):
            tier2_calls.append({'query': query, 'query_embedding': query_embedding})
            return original(self, project_context=project_context, token_limit=token_limit,
                            query=query, query_embedding=query_embedding)

        monkeypatch.setattr(RetrievalEngine, 'get_crystallized_for_context', spy_tier2)

        # Simulate embedding failure
        import core.embeddings as embeddings_module
        monkeypatch.setattr(embeddings_module, 'embed_text', lambda text, **kw: (_ for _ in ()).throw(RuntimeError("API unavailable")))

        search_and_inject("fallback fts search query crystallized content", "sess-tier2-004")

        assert len(tier2_calls) > 0, "get_crystallized_for_context was not called even on embedding failure"
        assert tier2_calls[0]['query'] is not None, "FTS query should still be passed even when embedding fails"
        assert tier2_calls[0]['query_embedding'] is None, "query_embedding should be None when embedding fails"

    def test_tier2_latency_under_500ms_with_1000_memories(self, base, monkeypatch):
        """500ms latency budget is met with 1000 memories including Tier 2 crystallized call."""
        import time
        import core.embeddings as embeddings_module
        monkeypatch.setattr(embeddings_module, 'embed_text', lambda text, **kw: None)

        for i in range(1000):
            _make_memory(
                stage='crystallized',
                title=f'Perf Crystallized Memory {i}',
                content=f'performance tier2 crystallized memory item number {i} with searchable content',
            )

        start = time.perf_counter()
        search_and_inject("performance tier2 crystallized memory search", "sess-tier2-perf")
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 500, (
            f"search_and_inject with Tier 2 call took {elapsed_ms:.1f}ms, expected < 500ms"
        )
