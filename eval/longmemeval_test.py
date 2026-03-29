"""
Tests for LongMemEval adapter.

Covers:
  - test_adapter_scores_correct_answer: retrieval returns answer → score 1.0
  - test_adapter_scores_wrong_answer: retrieval returns irrelevant → score 0.0
  - test_adapter_fixture_has_questions: fixture has 10 questions across 5 categories
  - test_adapter_aggregate_accuracy: 5 correct out of 10 → accuracy 0.5
"""

import pytest
from eval.longmemeval_adapter import (
    LongMemEvalAdapter,
    LongMemEvalResult,
    LONGMEMEVAL_FIXTURE,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

SAMPLE_QUESTION = {
    "id": "test-001",
    "question": "What database does the user prefer for new projects?",
    "answer": "PostgreSQL",
    "memory_ids": ["mem-001"],
    "category": "single-session-user",
}


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------


def test_adapter_scores_correct_answer():
    """When retrieval_fn returns a string containing the expected answer, score is 1.0."""
    retrieval_fn = lambda query: [f"The user strongly prefers {SAMPLE_QUESTION['answer']} for all new projects."]
    adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
    result = adapter.run_question(SAMPLE_QUESTION)

    assert isinstance(result, LongMemEvalResult)
    assert result.question_id == SAMPLE_QUESTION["id"]
    assert result.question == SAMPLE_QUESTION["question"]
    assert result.expected_answer == SAMPLE_QUESTION["answer"]
    assert result.is_correct is True
    assert result.score == 1.0


def test_adapter_scores_wrong_answer():
    """When retrieval_fn returns irrelevant strings, score is 0.0."""
    retrieval_fn = lambda query: ["This is completely irrelevant text about something else."]
    adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
    result = adapter.run_question(SAMPLE_QUESTION)

    assert isinstance(result, LongMemEvalResult)
    assert result.is_correct is False
    assert result.score == 0.0


def test_adapter_scores_correct_case_insensitive():
    """Answer match is case-insensitive."""
    retrieval_fn = lambda query: ["the user prefers postgresql for everything."]
    adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
    result = adapter.run_question(SAMPLE_QUESTION)

    assert result.is_correct is True
    assert result.score == 1.0


def test_adapter_scores_empty_retrieval():
    """When retrieval_fn returns empty list, score is 0.0."""
    retrieval_fn = lambda query: []
    adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
    result = adapter.run_question(SAMPLE_QUESTION)

    assert result.is_correct is False
    assert result.score == 0.0


def test_adapter_records_retrieved_context():
    """Result stores the retrieved context strings."""
    context = ["memory string one", "memory string two"]
    retrieval_fn = lambda query: context
    adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
    result = adapter.run_question(SAMPLE_QUESTION)

    assert result.retrieved_context == context


# ---------------------------------------------------------------------------
# Fixture tests
# ---------------------------------------------------------------------------


def test_adapter_fixture_has_questions():
    """LONGMEMEVAL_FIXTURE has exactly 10 questions covering all 5 categories."""
    assert len(LONGMEMEVAL_FIXTURE) == 10

    required_categories = {
        "single-session-user",
        "single-session-assistant",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    }
    actual_categories = {q["category"] for q in LONGMEMEVAL_FIXTURE}
    assert actual_categories == required_categories


def test_fixture_questions_have_required_keys():
    """Each fixture question has id, question, answer, memory_ids, category."""
    required_keys = {"id", "question", "answer", "memory_ids", "category"}
    for q in LONGMEMEVAL_FIXTURE:
        assert required_keys.issubset(q.keys()), f"Missing keys in question {q.get('id', '?')}"


def test_fixture_each_category_has_two_questions():
    """Each of the 5 categories has exactly 2 questions in the fixture."""
    from collections import Counter
    counts = Counter(q["category"] for q in LONGMEMEVAL_FIXTURE)
    for category, count in counts.items():
        assert count == 2, f"Category {category!r} has {count} questions, expected 2"


# ---------------------------------------------------------------------------
# run_fixture tests
# ---------------------------------------------------------------------------


def test_run_fixture_returns_all_results():
    """run_fixture() returns one result per fixture question."""
    retrieval_fn = lambda query: []
    adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
    results = adapter.run_fixture()

    assert len(results) == len(LONGMEMEVAL_FIXTURE)
    assert all(isinstance(r, LongMemEvalResult) for r in results)


# ---------------------------------------------------------------------------
# Aggregate tests
# ---------------------------------------------------------------------------


def test_adapter_aggregate_accuracy():
    """5 correct out of 10 results → accuracy == 0.5."""
    results = []
    for i, q in enumerate(LONGMEMEVAL_FIXTURE):
        # Make first 5 correct, last 5 wrong
        is_correct = i < 5
        results.append(
            LongMemEvalResult(
                question_id=q["id"],
                question=q["question"],
                expected_answer=q["answer"],
                retrieved_context=["some context"],
                predicted_answer=q["answer"] if is_correct else "wrong",
                is_correct=is_correct,
                score=1.0 if is_correct else 0.0,
            )
        )

    adapter = LongMemEvalAdapter(retrieval_fn=lambda q: [])
    agg = adapter.aggregate(results)

    assert agg["accuracy"] == pytest.approx(0.5)
    assert agg["total"] == 10


def test_adapter_aggregate_by_category():
    """aggregate() returns per-category accuracy breakdown."""
    retrieval_fn = lambda query: []
    adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
    results = adapter.run_fixture()

    agg = adapter.aggregate(results)
    assert "by_category" in agg

    required_categories = {
        "single-session-user",
        "single-session-assistant",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    }
    assert set(agg["by_category"].keys()) == required_categories


def test_adapter_aggregate_perfect_score():
    """All correct → accuracy == 1.0."""
    results = [
        LongMemEvalResult(
            question_id=q["id"],
            question=q["question"],
            expected_answer=q["answer"],
            retrieved_context=[q["answer"]],
            predicted_answer=q["answer"],
            is_correct=True,
            score=1.0,
        )
        for q in LONGMEMEVAL_FIXTURE
    ]
    adapter = LongMemEvalAdapter(retrieval_fn=lambda q: [])
    agg = adapter.aggregate(results)
    assert agg["accuracy"] == pytest.approx(1.0)


def test_adapter_aggregate_zero_score():
    """All wrong → accuracy == 0.0."""
    results = [
        LongMemEvalResult(
            question_id=q["id"],
            question=q["question"],
            expected_answer=q["answer"],
            retrieved_context=[],
            predicted_answer="",
            is_correct=False,
            score=0.0,
        )
        for q in LONGMEMEVAL_FIXTURE
    ]
    adapter = LongMemEvalAdapter(retrieval_fn=lambda q: [])
    agg = adapter.aggregate(results)
    assert agg["accuracy"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# No-network / no-core-import guard
# ---------------------------------------------------------------------------


def test_adapter_importable_without_core():
    """Adapter must be importable without core.* modules — validated by the import at top of file."""
    # If we got here, the import succeeded. That's the test.
    assert LongMemEvalAdapter is not None
    assert LongMemEvalResult is not None
    assert LONGMEMEVAL_FIXTURE is not None
