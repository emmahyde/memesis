"""
Tests for FeedbackLoop: usage tracking, importance scoring, promotion/demotion signals.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, get_base_dir
from core.feedback import FeedbackLoop
from core.lifecycle import LifecycleManager
from core.models import Memory, RetrievalLog, db


@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def lifecycle(base):
    return LifecycleManager()


@pytest.fixture
def feedback(base, lifecycle):
    return FeedbackLoop(lifecycle)


def _make_memory(*, content="body", **metadata) -> str:
    metadata.setdefault("stage", "consolidated")
    metadata.setdefault("title", "Default Title")
    metadata.setdefault("summary", "default summary text")
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage=metadata["stage"],
        title=metadata["title"],
        summary=metadata["summary"],
        content=content,
        tags=json.dumps(metadata.get("tags", [])),
        importance=metadata.get("importance", 0.5),
        reinforcement_count=metadata.get("reinforcement_count", 0),
        created_at=now,
        updated_at=now,
    )
    return mem.id


def _record_injection(memory_id, session_id):
    now = datetime.now().isoformat()
    Memory.update(last_injected_at=now, injection_count=Memory.injection_count + 1).where(Memory.id == memory_id).execute()
    RetrievalLog.create(timestamp=now, session_id=session_id, memory_id=memory_id, retrieval_type="injected")


def _record_usage(memory_id, session_id):
    now = datetime.now().isoformat()
    Memory.update(last_used_at=now, usage_count=Memory.usage_count + 1).where(Memory.id == memory_id).execute()
    from peewee import fn
    subq = RetrievalLog.select(fn.MAX(RetrievalLog.timestamp)).where(
        RetrievalLog.memory_id == memory_id, RetrievalLog.session_id == session_id
    )
    RetrievalLog.update(was_used=1).where(
        RetrievalLog.memory_id == memory_id, RetrievalLog.session_id == session_id, RetrievalLog.timestamp == subq
    ).execute()


# --- track_usage ---

def test_track_usage_marks_used_when_two_keywords_match(base, feedback):
    memory_id = _make_memory(title="Python Testing", summary="pytest fixtures help organize tests")
    _record_injection(memory_id, "sess1")
    result = feedback.track_usage("sess1", [memory_id], "We used pytest fixtures extensively in this python project.")
    assert result[memory_id] is True


def test_track_usage_marks_not_used_when_fewer_than_two_keywords(base, feedback):
    memory_id = _make_memory(title="Zymurgical Processes", summary="fermentation wort yeast grain malt")
    _record_injection(memory_id, "sess1")
    result = feedback.track_usage("sess1", [memory_id], "We deployed the application to Kubernetes today.")
    assert result[memory_id] is False


def test_track_usage_calls_record_usage_for_used_memory(base, feedback):
    memory_id = _make_memory(title="Ruby Style Guide", summary="idiomatic ruby methods patterns")
    _record_injection(memory_id, "sess1")
    feedback.track_usage("sess1", [memory_id], "Follow ruby style patterns for idiomatic code.")
    mem = Memory.get_by_id(memory_id)
    assert mem.usage_count == 1


def test_track_usage_does_not_increment_usage_when_not_used(base, feedback):
    memory_id = _make_memory(title="Baroque Architecture", summary="ornate columns classical facade")
    _record_injection(memory_id, "sess1")
    feedback.track_usage("sess1", [memory_id], "Today we wrote unit tests.")
    mem = Memory.get_by_id(memory_id)
    assert mem.usage_count == 0


def test_track_usage_handles_missing_memory(feedback):
    result = feedback.track_usage("sess1", ["nonexistent-id"], "some response text")
    assert result["nonexistent-id"] is False


def test_track_usage_case_insensitive(base, feedback):
    memory_id = _make_memory(title="Django Framework", summary="middleware views models forms")
    _record_injection(memory_id, "sess1")
    result = feedback.track_usage("sess1", [memory_id], "DJANGO MIDDLEWARE and VIEWS were configured.")
    assert result[memory_id] is True


def test_track_usage_returns_map_for_multiple_memories(base, feedback):
    id1 = _make_memory(title="Kubernetes Deployment", summary="pods replicas services namespace")
    id2 = _make_memory(title="Zymurgical Processes", summary="fermentation wort yeast grain")
    for mid in (id1, id2):
        _record_injection(mid, "sess1")
    result = feedback.track_usage("sess1", [id1, id2], "Kubernetes deployment uses pods and replicas inside a namespace.")
    assert result[id1] is True
    assert result[id2] is False


def test_track_usage_logs_event_for_used_memory(base, feedback):
    memory_id = _make_memory(title="Testing Patterns", summary="pytest fixtures parametrize coverage")
    _record_injection(memory_id, "sess1")
    feedback.track_usage("sess1", [memory_id], "pytest fixtures and parametrize patterns help coverage.")
    log_path = get_base_dir() / "meta" / "retrieval-log.jsonl"
    assert log_path.exists()
    events = [json.loads(line) for line in log_path.read_text().strip().splitlines()]
    used_events = [e for e in events if e["event"] == "memory_used"]
    assert len(used_events) == 1
    assert used_events[0]["memory_id"] == memory_id


# --- update_importance_scores ---

def test_importance_increases_for_used_memory(base, feedback):
    memory_id = _make_memory(title="Python Testing", summary="pytest fixtures helper organize", importance=0.5)
    _record_injection(memory_id, "sess1")
    feedback.track_usage("sess1", [memory_id], "pytest fixtures and helpers organize tests nicely.")
    feedback.update_importance_scores("sess1")
    mem = Memory.get_by_id(memory_id)
    assert abs(mem.importance - 0.55) < 1e-9


def test_importance_increase_capped_at_1(base, feedback):
    memory_id = _make_memory(title="Python Testing", summary="pytest fixtures helper organize", importance=0.98)
    _record_injection(memory_id, "sess1")
    feedback.track_usage("sess1", [memory_id], "pytest fixtures and helpers organize tests nicely.")
    feedback.update_importance_scores("sess1")
    assert Memory.get_by_id(memory_id).importance == 1.0


def test_importance_decreases_after_three_consecutive_unused(base, feedback):
    memory_id = _make_memory(title="Unused Memory Topic", summary="nothing relevant ever appears here", importance=0.5)
    for i in range(3):
        _record_injection(memory_id, f"sess_{i}")
    feedback.update_importance_scores("new_session")
    assert abs(Memory.get_by_id(memory_id).importance - 0.4) < 1e-9


def test_importance_decrease_floored_at_0_1(base, feedback):
    memory_id = _make_memory(title="Unused Memory Topic", summary="nothing relevant ever appears here", importance=0.15)
    for i in range(3):
        _record_injection(memory_id, f"sess_{i}")
    feedback.update_importance_scores("new_session")
    assert Memory.get_by_id(memory_id).importance == 0.1


def test_importance_does_not_decrease_with_fewer_than_three_injections(base, feedback):
    memory_id = _make_memory(title="Unused Memory Topic", summary="nothing relevant ever appears here", importance=0.5)
    for i in range(2):
        _record_injection(memory_id, f"sess_{i}")
    feedback.update_importance_scores("new_session")
    assert Memory.get_by_id(memory_id).importance == 0.5


def test_importance_does_not_decrease_when_third_was_used(base, feedback):
    memory_id = _make_memory(title="Unused Memory Topic", summary="nothing relevant ever appears here", importance=0.5)
    _record_injection(memory_id, "sess_0")
    _record_usage(memory_id, "sess_0")
    _record_injection(memory_id, "sess_1")
    _record_injection(memory_id, "sess_2")
    feedback.update_importance_scores("new_session")
    assert Memory.get_by_id(memory_id).importance == 0.5


def test_update_importance_logs_event(base, feedback):
    memory_id = _make_memory(title="Python Testing", summary="pytest fixtures helper organize", importance=0.5)
    _record_injection(memory_id, "sess1")
    feedback.track_usage("sess1", [memory_id], "pytest fixtures and helpers organize tests.")
    feedback.update_importance_scores("sess1")
    log_path = get_base_dir() / "meta" / "retrieval-log.jsonl"
    events = [json.loads(line) for line in log_path.read_text().strip().splitlines()]
    update_events = [e for e in events if e["event"] == "importance_updated"]
    assert len(update_events) == 1
    assert update_events[0]["old"] == 0.5
    assert abs(update_events[0]["new"] - 0.55) < 1e-9


def test_ephemeral_memories_excluded_from_importance_update(base, feedback):
    memory_id = _make_memory(title="Python Testing", summary="pytest fixtures helper organize", stage="ephemeral", importance=0.5)
    _record_injection(memory_id, "sess1")
    feedback.track_usage("sess1", [memory_id], "pytest fixtures and helpers organize tests.")
    feedback.update_importance_scores("sess1")
    assert Memory.get_by_id(memory_id).importance == 0.5


# --- get_promotion_signals ---

def test_get_promotion_signals_returns_eligible_ids(base, feedback):
    eligible_id = _make_memory(title="Promoted Memory", summary="worth keeping around", reinforcement_count=3)
    ineligible_id = _make_memory(title="Not Ready Yet", summary="needs more work", reinforcement_count=1)
    signals = feedback.get_promotion_signals()
    assert eligible_id in signals
    assert ineligible_id not in signals


def test_get_promotion_signals_returns_empty_when_none_eligible(base, feedback):
    _make_memory(title="Some Memory", summary="text here", reinforcement_count=0)
    assert feedback.get_promotion_signals() == []


# --- get_demotion_signals ---

def test_get_demotion_signals_returns_d09_candidates(base, feedback):
    memory_id = _make_memory(title="Overinjected Memory", summary="never actually used", stage="crystallized")
    for i in range(12):
        _record_injection(memory_id, f"sess_{i}")
    assert memory_id in feedback.get_demotion_signals()


def test_get_demotion_signals_excludes_used_memories(base, feedback):
    memory_id = _make_memory(title="Actually Used Memory", summary="gets referenced often", stage="crystallized")
    for i in range(12):
        _record_injection(memory_id, f"sess_{i}")
    _record_usage(memory_id, "sess_0")
    assert memory_id not in feedback.get_demotion_signals()


def test_get_demotion_signals_empty_when_all_used(base, feedback):
    assert feedback.get_demotion_signals() == []


# --- get_cross_project_candidates ---

def test_get_cross_project_candidates_returns_memories_in_3_distinct_projects(base, feedback):
    memory_id = _make_memory(stage="crystallized", title="Cross Project Memory", summary="used across many projects", content="Useful everywhere")
    now = datetime.now().isoformat()
    for project in ["/proj/a", "/proj/b", "/proj/c"]:
        RetrievalLog.create(timestamp=now, session_id="sess-1", memory_id=memory_id, retrieval_type="injected", project_context=project)
    assert memory_id in feedback.get_cross_project_candidates()


def test_get_cross_project_candidates_excludes_single_project_injections(base, feedback):
    memory_id = _make_memory(stage="crystallized", title="Single Project Memory", summary="only ever used in one project", content="Only one project")
    now = datetime.now().isoformat()
    for _ in range(3):
        RetrievalLog.create(timestamp=now, session_id="sess-1", memory_id=memory_id, retrieval_type="injected", project_context="/proj/a")
    assert memory_id not in feedback.get_cross_project_candidates()


def test_get_cross_project_candidates_excludes_null_project_context(base, feedback):
    memory_id = _make_memory(stage="crystallized", title="No Project Memory", summary="never associated", content="No project")
    now = datetime.now().isoformat()
    for _ in range(3):
        RetrievalLog.create(timestamp=now, session_id="sess-1", memory_id=memory_id, retrieval_type="injected", project_context=None)
    assert memory_id not in feedback.get_cross_project_candidates()


# --- log_event ---

def test_log_event_creates_jsonl_file(base, feedback):
    log_path = get_base_dir() / "meta" / "retrieval-log.jsonl"
    assert not log_path.exists()
    feedback.log_event("test_event", {"key": "value"})
    assert log_path.exists()


def test_log_event_appends_valid_json_lines(base, feedback):
    feedback.log_event("event_a", {"x": 1})
    feedback.log_event("event_b", {"y": 2})
    log_path = get_base_dir() / "meta" / "retrieval-log.jsonl"
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "event_a"
    assert first["x"] == 1


def test_log_event_timestamp_format(base, feedback):
    feedback.log_event("check_ts", {})
    log_path = get_base_dir() / "meta" / "retrieval-log.jsonl"
    record = json.loads(log_path.read_text().strip())
    datetime.fromisoformat(record["timestamp"])


# --- NLTK usage scoring ---

class TestNLTKUsageScoring:
    def test_stemmed_variant_triggers_usage(self, base, feedback):
        memory_id = _make_memory(title="Authentication Middleware", summary="validates tokens before routing requests")
        _record_injection(memory_id, "sess1")
        result = feedback.track_usage("sess1", [memory_id], "We are authenticating requests using the middleware layer.")
        assert result[memory_id] is True

    def test_stopword_in_title_does_not_inflate_score(self, base, feedback):
        memory_id = _make_memory(title="The Payment System", summary="handles billing and invoicing workflows")
        _record_injection(memory_id, "sess1")
        result = feedback.track_usage("sess1", [memory_id], "We deployed the new Kubernetes cluster today.")
        assert result[memory_id] is False

    def test_nltk_fallback_when_data_unavailable(self, base, feedback, monkeypatch):
        import core.feedback as feedback_module
        monkeypatch.setattr(feedback_module, "_STOPWORDS", None)
        monkeypatch.setattr(feedback_module, "_STEMMER", None)
        import nltk as _nltk
        monkeypatch.setattr(_nltk.data, "find", lambda *a, **kw: (_ for _ in ()).throw(LookupError("not found")))
        monkeypatch.setattr(_nltk, "download", lambda *a, **kw: None)
        memory_id = _make_memory(title="Python Testing", summary="pytest fixtures and helpers")
        _record_injection(memory_id, "sess1")
        result = feedback.track_usage("sess1", [memory_id], "We used pytest fixtures throughout the testing session.")
        assert isinstance(result, dict)
        assert memory_id in result


# --- Content-aware usage scoring ---

class TestContentAwareUsage:
    def test_content_keywords_trigger_usage(self, base, feedback):
        memory_id = _make_memory(
            title="Short Title", summary="brief note",
            content="The idempotency_key mechanism prevents duplicate payment processing in the webhook handler by checking Redis before executing.",
        )
        _record_injection(memory_id, "sess1")
        result = feedback.track_usage("sess1", [memory_id], "I added idempotency_key validation in the webhook handler to prevent duplicate payment processing via Redis.")
        assert result[memory_id] is True

    def test_title_match_is_strongest_signal(self, base, feedback):
        memory_id = _make_memory(title="Kubernetes Deployment Strategy", summary="rolling updates", content="generic content")
        _record_injection(memory_id, "sess1")
        result = feedback.track_usage("sess1", [memory_id], "The Kubernetes deployment was configured with the right strategy.")
        assert result[memory_id] is True

    def test_short_common_content_words_dont_false_positive(self, base, feedback):
        memory_id = _make_memory(title="Obscure Zymurgical Process", summary="fermentation technique details", content="This code uses the file and gets data from the list.")
        _record_injection(memory_id, "sess1")
        result = feedback.track_usage("sess1", [memory_id], "This code uses the file and gets data from the list.")
        assert result[memory_id] is False

    def test_longer_content_words_carry_more_weight(self, base, feedback):
        memory_id = _make_memory(title="Unrelated Title Here", summary="nothing useful",
            content="The authentication middleware validates the authorization header before processing any request.")
        _record_injection(memory_id, "sess1")
        result = feedback.track_usage("sess1", [memory_id], "I configured the authentication middleware to validate the authorization header properly.")
        assert result[memory_id] is True

    def test_usage_score_in_log_event(self, base, feedback):
        memory_id = _make_memory(title="Python Testing Patterns", summary="pytest fixtures parametrize coverage", content="Use conftest.py for shared fixtures.")
        _record_injection(memory_id, "sess1")
        feedback.track_usage("sess1", [memory_id], "The Python testing patterns use pytest fixtures and parametrize for coverage.")
        log_path = get_base_dir() / "meta" / "retrieval-log.jsonl"
        events = [json.loads(line) for line in log_path.read_text().strip().splitlines()]
        used_events = [e for e in events if e["event"] == "memory_used"]
        assert len(used_events) == 1
        assert 0 < used_events[0]["confidence"] <= 1.0
