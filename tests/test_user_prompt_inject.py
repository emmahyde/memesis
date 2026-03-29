"""
Tests for the UserPromptSubmit hook — just-in-time memory injection.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.storage import MemoryStore
from hooks.user_prompt_inject import extract_query_terms, search_and_inject, get_already_injected


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    return MemoryStore(base_dir=str(tmp_path / "memory"))


# ---------------------------------------------------------------------------
# Query term extraction
# ---------------------------------------------------------------------------

class TestExtractQueryTerms:
    def test_extracts_significant_words(self):
        terms = extract_query_terms("How do I fix the payment pipeline deadlock?")
        assert "payment" in terms
        assert "pipeline" in terms
        assert "deadlock" in terms

    def test_filters_short_words(self):
        terms = extract_query_terms("I am so not ok")
        assert "not" not in terms
        assert len(terms) == 0  # all too short or stop words

    def test_filters_stop_words(self):
        terms = extract_query_terms("please help me with this code")
        assert "please" not in terms
        assert "help" not in terms
        assert "this" not in terms
        assert "code" not in terms

    def test_strips_code_fences(self):
        prompt = "Look at this:\n```python\ndef foo():\n    pass\n```\nIs it correct?"
        terms = extract_query_terms(prompt)
        assert "pass" not in terms  # inside code fence

    def test_strips_inline_code(self):
        terms = extract_query_terms("The `payment_processor` function needs fixing")
        # payment_processor is stripped because backticks are removed,
        # then non-alpha chars are removed
        assert "function" in terms
        assert "needs" in terms
        assert "fixing" in terms

    def test_deduplicates(self):
        terms = extract_query_terms("payment payment payment pipeline")
        assert terms.count("payment") == 1

    def test_caps_at_ten_terms(self):
        long_prompt = " ".join(f"word{i}" for i in range(100))
        terms = extract_query_terms(long_prompt)
        assert len(terms) <= 10

    def test_empty_prompt(self):
        assert extract_query_terms("") == []

    def test_only_punctuation(self):
        assert extract_query_terms("!!! ??? ...") == []


# ---------------------------------------------------------------------------
# Search and inject
# ---------------------------------------------------------------------------

class TestSearchAndInject:
    def test_returns_empty_for_no_matches(self, tmp_store):
        result = search_and_inject(tmp_store, "something unrelated", "sess-001")
        assert result == ""

    def test_returns_empty_for_empty_prompt(self, tmp_store):
        result = search_and_inject(tmp_store, "", "sess-001")
        assert result == ""

    def test_finds_matching_memory(self, tmp_store):
        tmp_store.create(
            path="payment_pipeline.md",
            content="Always check lock ordering in the payment pipeline.",
            metadata={
                "stage": "consolidated",
                "title": "Payment Pipeline Locking",
                "summary": "Check lock ordering to prevent deadlocks.",
                "tags": ["payment", "concurrency"],
            },
        )

        result = search_and_inject(
            tmp_store,
            "I need to fix the payment pipeline deadlock issue",
            "sess-001",
        )
        assert "Payment Pipeline Locking" in result

    def test_excludes_already_injected(self, tmp_store):
        mid = tmp_store.create(
            path="already_injected.md",
            content="Already loaded at session start.",
            metadata={
                "stage": "crystallized",
                "title": "Already Injected Memory",
                "summary": "This was injected at SessionStart.",
                "tags": ["python"],
            },
        )
        # Simulate SessionStart injection
        tmp_store.record_injection(mid, "sess-001")

        result = search_and_inject(
            tmp_store,
            "Tell me about the already injected memory for python",
            "sess-001",
        )
        assert "Already Injected" not in result

    def test_excludes_archived_memories(self, tmp_store):
        mid = tmp_store.create(
            path="archived_mem.md",
            content="This was archived.",
            metadata={
                "stage": "consolidated",
                "title": "Archived Payment Info",
                "summary": "Old payment pipeline details.",
                "tags": ["payment"],
            },
        )
        tmp_store.archive(mid)

        result = search_and_inject(
            tmp_store,
            "payment pipeline issues",
            "sess-001",
        )
        assert "Archived Payment" not in result

    def test_excludes_ephemeral(self, tmp_store):
        tmp_store.create(
            path="scratch.md",
            content="Some scratch observation about deployment.",
            metadata={
                "stage": "ephemeral",
                "title": "Deployment Scratch",
                "summary": "Ephemeral note.",
                "tags": ["deployment"],
            },
        )

        result = search_and_inject(
            tmp_store,
            "deployment pipeline configuration",
            "sess-001",
        )
        assert "Deployment Scratch" not in result

    def test_respects_token_budget(self, tmp_store):
        # Create a memory with very long content
        tmp_store.create(
            path="long_memory.md",
            content="X" * 5000,
            metadata={
                "stage": "consolidated",
                "title": "Very Long Memory About Kubernetes",
                "summary": "A" * 3000,  # exceeds TOKEN_BUDGET_CHARS
                "tags": ["kubernetes"],
            },
        )

        result = search_and_inject(
            tmp_store,
            "kubernetes deployment strategy",
            "sess-001",
        )
        # Should either be empty (budget exceeded) or truncated
        assert len(result) <= 2500  # TOKEN_BUDGET_CHARS + overhead


# ---------------------------------------------------------------------------
# Already-injected tracking
# ---------------------------------------------------------------------------

class TestAlreadyInjected:
    def test_empty_session(self, tmp_store):
        result = get_already_injected(tmp_store, "new-session")
        assert result == set()

    def test_returns_injected_ids(self, tmp_store):
        mid = tmp_store.create(
            path="test.md",
            content="Test",
            metadata={"stage": "crystallized", "title": "Test"},
        )
        tmp_store.record_injection(mid, "sess-001")

        result = get_already_injected(tmp_store, "sess-001")
        assert mid in result

    def test_session_isolation(self, tmp_store):
        mid = tmp_store.create(
            path="test.md",
            content="Test",
            metadata={"stage": "crystallized", "title": "Test"},
        )
        tmp_store.record_injection(mid, "sess-001")

        result = get_already_injected(tmp_store, "sess-002")
        assert mid not in result
