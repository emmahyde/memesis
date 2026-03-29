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
