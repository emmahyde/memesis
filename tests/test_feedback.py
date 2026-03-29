"""
Tests for FeedbackLoop: usage tracking, importance scoring, promotion/demotion signals.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.feedback import FeedbackLoop
from core.lifecycle import LifecycleManager
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return MemoryStore(base_dir=str(tmp_path / 'memory'))


@pytest.fixture
def lifecycle(store):
    return LifecycleManager(store)


@pytest.fixture
def feedback(store, lifecycle):
    return FeedbackLoop(store, lifecycle)


def _make_memory(store, *, path='mem.md', content='body', **metadata) -> str:
    """Helper: create a memory with sensible defaults."""
    metadata.setdefault('stage', 'consolidated')
    metadata.setdefault('title', 'Default Title')
    metadata.setdefault('summary', 'default summary text')
    return store.create(path=path, content=content, metadata=metadata)



# ---------------------------------------------------------------------------
# track_usage
# ---------------------------------------------------------------------------


def test_track_usage_marks_used_when_two_keywords_match(store, feedback):
    memory_id = _make_memory(
        store,
        path='python_tips.md',
        title='Python Testing',
        summary='pytest fixtures help organize tests',
    )
    store.record_injection(memory_id, 'sess1')

    result = feedback.track_usage(
        'sess1',
        [memory_id],
        'We used pytest fixtures extensively in this python project.',
    )

    assert result[memory_id] is True


def test_track_usage_marks_not_used_when_fewer_than_two_keywords(store, feedback):
    memory_id = _make_memory(
        store,
        path='obscure.md',
        title='Zymurgical Processes',
        summary='fermentation wort yeast grain malt',
    )
    store.record_injection(memory_id, 'sess1')

    result = feedback.track_usage(
        'sess1',
        [memory_id],
        'We deployed the application to Kubernetes today.',
    )

    assert result[memory_id] is False


def test_track_usage_calls_record_usage_for_used_memory(store, feedback):
    memory_id = _make_memory(
        store,
        path='ruby_style.md',
        title='Ruby Style Guide',
        summary='idiomatic ruby methods patterns',
    )
    store.record_injection(memory_id, 'sess1')

    feedback.track_usage('sess1', [memory_id], 'Follow ruby style patterns for idiomatic code.')

    memory = store.get(memory_id)
    assert memory['usage_count'] == 1


def test_track_usage_does_not_increment_usage_when_not_used(store, feedback):
    memory_id = _make_memory(
        store,
        path='irrelevant.md',
        title='Baroque Architecture',
        summary='ornate columns classical facade',
    )
    store.record_injection(memory_id, 'sess1')

    feedback.track_usage('sess1', [memory_id], 'Today we wrote unit tests.')

    memory = store.get(memory_id)
    assert memory['usage_count'] == 0


def test_track_usage_handles_missing_memory(feedback):
    """track_usage should not raise for unknown IDs — returns False."""
    result = feedback.track_usage('sess1', ['nonexistent-id'], 'some response text')
    assert result['nonexistent-id'] is False


def test_track_usage_case_insensitive(store, feedback):
    memory_id = _make_memory(
        store,
        path='django.md',
        title='Django Framework',
        summary='middleware views models forms',
    )
    store.record_injection(memory_id, 'sess1')

    result = feedback.track_usage(
        'sess1',
        [memory_id],
        'DJANGO MIDDLEWARE and VIEWS were configured.',
    )

    assert result[memory_id] is True


def test_track_usage_returns_map_for_multiple_memories(store, feedback):
    id1 = _make_memory(
        store, path='mem1.md',
        title='Kubernetes Deployment', summary='pods replicas services namespace',
    )
    id2 = _make_memory(
        store, path='mem2.md',
        title='Zymurgical Processes', summary='fermentation wort yeast grain',
    )
    for mid in (id1, id2):
        store.record_injection(mid, 'sess1')

    response = 'Kubernetes deployment uses pods and replicas inside a namespace.'
    result = feedback.track_usage('sess1', [id1, id2], response)

    assert result[id1] is True
    assert result[id2] is False


def test_track_usage_logs_event_for_used_memory(store, feedback):
    memory_id = _make_memory(
        store,
        path='testing.md',
        title='Testing Patterns',
        summary='pytest fixtures parametrize coverage',
    )
    store.record_injection(memory_id, 'sess1')

    feedback.track_usage('sess1', [memory_id], 'pytest fixtures and parametrize patterns help coverage.')

    log_path = store.base_dir / 'meta' / 'retrieval-log.jsonl'
    assert log_path.exists()

    lines = log_path.read_text().strip().splitlines()
    events = [json.loads(line) for line in lines]
    used_events = [e for e in events if e['event'] == 'memory_used']

    assert len(used_events) == 1
    assert used_events[0]['memory_id'] == memory_id
    assert used_events[0]['session_id'] == 'sess1'
    assert 'timestamp' in used_events[0]
    assert 'confidence' in used_events[0]


# ---------------------------------------------------------------------------
# update_importance_scores
# ---------------------------------------------------------------------------


def test_importance_increases_for_used_memory(store, feedback):
    memory_id = _make_memory(
        store,
        path='used_mem.md',
        title='Python Testing',
        summary='pytest fixtures helper organize',
        importance=0.5,
    )
    store.record_injection(memory_id, 'sess1')

    feedback.track_usage('sess1', [memory_id], 'pytest fixtures and helpers organize tests nicely.')
    feedback.update_importance_scores('sess1')

    memory = store.get(memory_id)
    assert abs(memory['importance'] - 0.55) < 1e-9


def test_importance_increase_capped_at_1(store, feedback):
    memory_id = _make_memory(
        store,
        path='near_max.md',
        title='Python Testing',
        summary='pytest fixtures helper organize',
        importance=0.98,
    )
    store.record_injection(memory_id, 'sess1')

    feedback.track_usage('sess1', [memory_id], 'pytest fixtures and helpers organize tests nicely.')
    feedback.update_importance_scores('sess1')

    memory = store.get(memory_id)
    assert memory['importance'] == 1.0


def test_importance_decreases_after_three_consecutive_unused(store, feedback):
    memory_id = _make_memory(
        store,
        path='unused_mem.md',
        title='Unused Memory Topic',
        summary='nothing relevant ever appears here',
        importance=0.5,
    )

    # Record 3 injections with no usage
    for i in range(3):
        store.record_injection(memory_id, f'sess_{i}')

    # Run update without any track_usage calls so session_map is empty
    feedback.update_importance_scores('new_session')

    memory = store.get(memory_id)
    assert abs(memory['importance'] - 0.4) < 1e-9


def test_importance_decrease_floored_at_0_1(store, feedback):
    memory_id = _make_memory(
        store,
        path='low_importance.md',
        title='Unused Memory Topic',
        summary='nothing relevant ever appears here',
        importance=0.15,
    )

    for i in range(3):
        store.record_injection(memory_id, f'sess_{i}')

    feedback.update_importance_scores('new_session')

    memory = store.get(memory_id)
    assert memory['importance'] == 0.1


def test_importance_does_not_decrease_with_fewer_than_three_injections(store, feedback):
    memory_id = _make_memory(
        store,
        path='two_inject.md',
        title='Unused Memory Topic',
        summary='nothing relevant ever appears here',
        importance=0.5,
    )

    # Only 2 injections — not enough for the penalty
    for i in range(2):
        store.record_injection(memory_id, f'sess_{i}')

    feedback.update_importance_scores('new_session')

    memory = store.get(memory_id)
    assert memory['importance'] == 0.5


def test_importance_does_not_decrease_when_third_was_used(store, feedback):
    """3 injections but one was used → not consecutive unused → no decrease."""
    memory_id = _make_memory(
        store,
        path='mixed_use.md',
        title='Unused Memory Topic',
        summary='nothing relevant ever appears here',
        importance=0.5,
    )

    # First injection used, next two not used
    store.record_injection(memory_id, 'sess_0')
    store.record_usage(memory_id, 'sess_0')
    store.record_injection(memory_id, 'sess_1')
    store.record_injection(memory_id, 'sess_2')

    feedback.update_importance_scores('new_session')

    # The latest 3 entries: sess_2 (unused), sess_1 (unused), sess_0 (used)
    # → not all unused → no penalty
    memory = store.get(memory_id)
    assert memory['importance'] == 0.5


def test_update_importance_logs_event(store, feedback):
    memory_id = _make_memory(
        store,
        path='score_log.md',
        title='Python Testing',
        summary='pytest fixtures helper organize',
        importance=0.5,
    )
    store.record_injection(memory_id, 'sess1')

    feedback.track_usage('sess1', [memory_id], 'pytest fixtures and helpers organize tests.')
    feedback.update_importance_scores('sess1')

    log_path = store.base_dir / 'meta' / 'retrieval-log.jsonl'
    lines = log_path.read_text().strip().splitlines()
    events = [json.loads(line) for line in lines]
    update_events = [e for e in events if e['event'] == 'importance_updated']

    assert len(update_events) == 1
    assert update_events[0]['memory_id'] == memory_id
    assert update_events[0]['old'] == 0.5
    assert abs(update_events[0]['new'] - 0.55) < 1e-9


def test_ephemeral_memories_excluded_from_importance_update(store, feedback):
    """Ephemeral memories should not have their importance updated."""
    memory_id = _make_memory(
        store,
        path='ephem.md',
        title='Python Testing',
        summary='pytest fixtures helper organize',
        stage='ephemeral',
        importance=0.5,
    )
    store.record_injection(memory_id, 'sess1')

    feedback.track_usage('sess1', [memory_id], 'pytest fixtures and helpers organize tests.')
    feedback.update_importance_scores('sess1')

    # Should remain unchanged since ephemeral is excluded from the query
    memory = store.get(memory_id)
    assert memory['importance'] == 0.5


# ---------------------------------------------------------------------------
# get_promotion_signals
# ---------------------------------------------------------------------------


def test_get_promotion_signals_returns_eligible_ids(store, feedback):
    eligible_id = _make_memory(
        store, path='promo_eligible.md',
        title='Promoted Memory', summary='worth keeping around',
        stage='consolidated', reinforcement_count=3,
    )
    ineligible_id = _make_memory(
        store, path='promo_not_yet.md',
        title='Not Ready Yet', summary='needs more work',
        stage='consolidated', reinforcement_count=1,
    )

    signals = feedback.get_promotion_signals()

    assert eligible_id in signals
    assert ineligible_id not in signals


def test_get_promotion_signals_returns_empty_when_none_eligible(store, feedback):
    _make_memory(
        store, path='low_reinf.md',
        title='Some Memory', summary='text here',
        stage='consolidated', reinforcement_count=0,
    )

    signals = feedback.get_promotion_signals()
    assert signals == []


# ---------------------------------------------------------------------------
# get_demotion_signals
# ---------------------------------------------------------------------------


def test_get_demotion_signals_returns_d09_candidates(store, feedback):
    memory_id = _make_memory(
        store, path='overinjected.md',
        title='Overinjected Memory', summary='never actually used',
        stage='crystallized',
    )

    for i in range(12):
        store.record_injection(memory_id, f'sess_{i}')

    signals = feedback.get_demotion_signals()
    assert memory_id in signals


def test_get_demotion_signals_excludes_used_memories(store, feedback):
    memory_id = _make_memory(
        store, path='used_memory.md',
        title='Actually Used Memory', summary='gets referenced often',
        stage='crystallized',
    )

    for i in range(12):
        store.record_injection(memory_id, f'sess_{i}')
    store.record_usage(memory_id, 'sess_0')

    signals = feedback.get_demotion_signals()
    assert memory_id not in signals


def test_get_demotion_signals_empty_when_all_used(store, feedback):
    signals = feedback.get_demotion_signals()
    assert signals == []


# ---------------------------------------------------------------------------
# get_cross_project_candidates
# ---------------------------------------------------------------------------


def test_get_cross_project_candidates_returns_memories_in_3_distinct_projects(store, feedback):
    """D-08: memory injected in 3+ distinct projects should be a candidate."""
    memory_id = store.create(
        path='cross_project.md',
        content='Useful everywhere',
        metadata={
            'stage': 'crystallized',
            'title': 'Cross Project Memory',
            'summary': 'used across many projects',
        },
    )

    # Simulate injections in 3 distinct project_contexts
    with sqlite3.connect(store.db_path) as conn:
        from datetime import datetime
        now = datetime.now().isoformat()
        for project in ['/proj/a', '/proj/b', '/proj/c']:
            conn.execute(
                """INSERT INTO retrieval_log
                   (timestamp, session_id, memory_id, retrieval_type, project_context)
                   VALUES (?, ?, ?, 'injected', ?)""",
                (now, 'sess-1', memory_id, project),
            )
        conn.commit()

    candidates = feedback.get_cross_project_candidates()
    assert memory_id in candidates


def test_get_cross_project_candidates_excludes_single_project_injections(store, feedback):
    """D-08: memory injected 3x from the SAME project should not be a candidate."""
    memory_id = store.create(
        path='single_project.md',
        content='Only one project',
        metadata={
            'stage': 'crystallized',
            'title': 'Single Project Memory',
            'summary': 'only ever used in one project',
        },
    )

    # 3 injections but all from the same project_context
    with sqlite3.connect(store.db_path) as conn:
        from datetime import datetime
        now = datetime.now().isoformat()
        for _ in range(3):
            conn.execute(
                """INSERT INTO retrieval_log
                   (timestamp, session_id, memory_id, retrieval_type, project_context)
                   VALUES (?, ?, ?, 'injected', ?)""",
                (now, 'sess-1', memory_id, '/proj/a'),
            )
        conn.commit()

    candidates = feedback.get_cross_project_candidates()
    assert memory_id not in candidates


def test_get_cross_project_candidates_excludes_null_project_context(store, feedback):
    """D-08: injections with NULL project_context don't count toward distinct projects."""
    memory_id = store.create(
        path='no_project.md',
        content='No project context',
        metadata={
            'stage': 'crystallized',
            'title': 'No Project Memory',
            'summary': 'never associated with project',
        },
    )

    # Injections with no project_context shouldn't count
    with sqlite3.connect(store.db_path) as conn:
        from datetime import datetime
        now = datetime.now().isoformat()
        for _ in range(3):
            conn.execute(
                """INSERT INTO retrieval_log
                   (timestamp, session_id, memory_id, retrieval_type, project_context)
                   VALUES (?, ?, ?, 'injected', NULL)""",
                (now, 'sess-1', memory_id),
            )
        conn.commit()

    candidates = feedback.get_cross_project_candidates()
    assert memory_id not in candidates


# ---------------------------------------------------------------------------
# log_event
# ---------------------------------------------------------------------------


def test_log_event_creates_jsonl_file(store, feedback):
    log_path = store.base_dir / 'meta' / 'retrieval-log.jsonl'
    assert not log_path.exists()

    feedback.log_event('test_event', {'key': 'value'})

    assert log_path.exists()


def test_log_event_appends_valid_json_lines(store, feedback):
    feedback.log_event('event_a', {'x': 1})
    feedback.log_event('event_b', {'y': 2})

    log_path = store.base_dir / 'meta' / 'retrieval-log.jsonl'
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first['event'] == 'event_a'
    assert first['x'] == 1
    assert 'timestamp' in first

    second = json.loads(lines[1])
    assert second['event'] == 'event_b'
    assert second['y'] == 2


def test_log_event_timestamp_format(store, feedback):
    feedback.log_event('check_ts', {})

    log_path = store.base_dir / 'meta' / 'retrieval-log.jsonl'
    record = json.loads(log_path.read_text().strip())
    # Should parse as ISO datetime without error
    from datetime import datetime
    datetime.fromisoformat(record['timestamp'])


# ---------------------------------------------------------------------------
# NLTK usage scoring (D-07)
# ---------------------------------------------------------------------------


class TestNLTKUsageScoring:
    """NLTK stopword filtering and stemming in track_usage (D-07)."""

    def test_stemmed_variant_triggers_usage(self, store, feedback):
        """Inflected form in response should match via Porter stem."""
        memory_id = _make_memory(
            store,
            path='auth_middleware.md',
            title='Authentication Middleware',
            summary='validates tokens before routing requests',
        )
        store.record_injection(memory_id, 'sess1')

        # Response uses "authenticating" — stem matches "authentication"
        result = feedback.track_usage(
            'sess1',
            [memory_id],
            'We are authenticating requests using the middleware layer.',
        )

        assert result[memory_id] is True

    def test_stopword_in_title_does_not_inflate_score(self, store, feedback):
        """Stopwords like 'the' in the title must not trigger usage on unrelated content."""
        memory_id = _make_memory(
            store,
            path='payment_system.md',
            title='The Payment System',
            summary='handles billing and invoicing workflows',
        )
        store.record_injection(memory_id, 'sess1')

        # Response has no payment/billing/invoicing content — only common words
        result = feedback.track_usage(
            'sess1',
            [memory_id],
            'We deployed the new Kubernetes cluster today.',
        )

        assert result[memory_id] is False

    def test_nltk_fallback_when_data_unavailable(self, store, feedback, monkeypatch):
        """LookupError from nltk.data.find must not raise — fallback path stays valid."""
        import core.feedback as feedback_module

        # Force re-initialization by resetting the cached globals
        monkeypatch.setattr(feedback_module, '_STOPWORDS', None)
        monkeypatch.setattr(feedback_module, '_STEMMER', None)

        # Make nltk.data.find raise LookupError so _ensure_nltk_data triggers
        # the download path, which itself is mocked to fail gracefully
        import nltk as _nltk
        monkeypatch.setattr(_nltk.data, 'find', lambda *a, **kw: (_ for _ in ()).throw(LookupError('not found')))
        # Also block the download so no network call is made
        monkeypatch.setattr(_nltk, 'download', lambda *a, **kw: None)

        memory_id = _make_memory(
            store,
            path='fallback.md',
            title='Python Testing',
            summary='pytest fixtures and helpers',
        )
        store.record_injection(memory_id, 'sess1')

        # Must not raise; must return a valid dict with a boolean value
        result = feedback.track_usage(
            'sess1',
            [memory_id],
            'We used pytest fixtures throughout the testing session.',
        )

        assert isinstance(result, dict)
        assert memory_id in result
        assert isinstance(result[memory_id], bool)


# ---------------------------------------------------------------------------
# Content-aware usage scoring
# ---------------------------------------------------------------------------


class TestContentAwareUsage:
    """Usage detection should consider memory content, not just title/summary."""

    def test_content_keywords_trigger_usage(self, store, feedback):
        """Memory content has domain-specific terms — should detect usage."""
        memory_id = _make_memory(
            store,
            path='content_usage.md',
            title='Short Title',
            summary='brief note',
            content='The idempotency_key mechanism prevents duplicate payment processing '
                    'in the webhook handler by checking Redis before executing.',
        )
        store.record_injection(memory_id, 'sess1')

        result = feedback.track_usage(
            'sess1',
            [memory_id],
            'I added idempotency_key validation in the webhook handler '
            'to prevent duplicate payment processing via Redis.',
        )
        assert result[memory_id] is True

    def test_title_match_is_strongest_signal(self, store, feedback):
        """Two title keyword matches should still trigger (backward compat)."""
        memory_id = _make_memory(
            store,
            path='title_match.md',
            title='Kubernetes Deployment Strategy',
            summary='rolling updates',
            content='generic content with no special words',
        )
        store.record_injection(memory_id, 'sess1')

        result = feedback.track_usage(
            'sess1',
            [memory_id],
            'The Kubernetes deployment was configured with the right strategy.',
        )
        assert result[memory_id] is True

    def test_short_common_content_words_dont_false_positive(self, store, feedback):
        """Short generic content words shouldn't cause false positives."""
        memory_id = _make_memory(
            store,
            path='generic.md',
            title='Obscure Zymurgical Process',
            summary='fermentation technique details',
            content='This code uses the file and gets data from the list.',
        )
        store.record_injection(memory_id, 'sess1')

        result = feedback.track_usage(
            'sess1',
            [memory_id],
            'This code uses the file and gets data from the list.',
        )
        # Content words are all short/generic — shouldn't reach threshold
        assert result[memory_id] is False

    def test_longer_content_words_carry_more_weight(self, store, feedback):
        """Specific domain terms (8+ chars) in content should be high weight."""
        memory_id = _make_memory(
            store,
            path='specific.md',
            title='Unrelated Title Here',
            summary='nothing useful',
            content='The authentication middleware validates the authorization '
                    'header before processing any request.',
        )
        store.record_injection(memory_id, 'sess1')

        result = feedback.track_usage(
            'sess1',
            [memory_id],
            'I configured the authentication middleware to validate '
            'the authorization header properly.',
        )
        assert result[memory_id] is True

    def test_usage_score_in_log_event(self, store, feedback):
        """Confidence score in log events should reflect the scoring model."""
        memory_id = _make_memory(
            store,
            path='scored.md',
            title='Python Testing Patterns',
            summary='pytest fixtures parametrize coverage',
            content='Use conftest.py for shared fixtures.',
        )
        store.record_injection(memory_id, 'sess1')

        feedback.track_usage(
            'sess1',
            [memory_id],
            'The Python testing patterns use pytest fixtures and parametrize for coverage.',
        )

        log_path = store.base_dir / 'meta' / 'retrieval-log.jsonl'
        lines = log_path.read_text().strip().splitlines()
        events = [json.loads(line) for line in lines]
        used_events = [e for e in events if e['event'] == 'memory_used']

        assert len(used_events) == 1
        assert 0 < used_events[0]['confidence'] <= 1.0
