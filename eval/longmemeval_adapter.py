"""
LongMemEval adapter for the memesis eval harness.

LongMemEval (ICLR 2025): https://github.com/xiaowu0162/LongMemEval
500 curated questions testing memory as indexing → retrieval → reading pipeline.

This module provides:
  - LONGMEMEVAL_FIXTURE: 10-question subset covering all 5 categories (offline)
  - LongMemEvalAdapter: wraps a retrieval callable, scores questions
  - LongMemEvalResult: per-question scored result

To run against the full 500-question dataset, set LONGMEMEVAL_DATASET_PATH
to a local clone of the LongMemEval repo's data/ directory. The adapter
will load questions from there if the env var is set, otherwise uses the
built-in fixture.

NOTE: Live wiring to core.retrieval.RetrievalEngine.active_search() is
deferred to Phase 7 (Hybrid RRF). Until then, pass a mock or stub retrieval_fn.

Wire to core.retrieval.RetrievalEngine.active_search() once Phase 7
(Hybrid RRF) is complete.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# LongMemEval 5-category taxonomy (from original paper)
# ---------------------------------------------------------------------------
# single-session-user    — what the *user* stated/expressed in one session
# single-session-assistant — what the *assistant* recommended/said in one session
# multi-session          — information that spans multiple separate sessions
# temporal-reasoning     — questions requiring reasoning about *when* something happened
# knowledge-update       — questions where an earlier fact was superseded by a newer one
# ---------------------------------------------------------------------------


@dataclass
class LongMemEvalResult:
    """Per-question scored result from the LongMemEval adapter."""

    question_id: str
    question: str
    expected_answer: str
    retrieved_context: list[str]
    predicted_answer: str
    is_correct: bool
    score: float  # 1.0 (correct) or 0.0 (wrong)


# ---------------------------------------------------------------------------
# 10-question fixture — 2 questions per LongMemEval category
# ---------------------------------------------------------------------------

LONGMEMEVAL_FIXTURE: list[dict] = [
    # --- single-session-user (2) ---
    {
        "id": "ssu-001",
        "question": "What database does the user prefer for new projects?",
        "answer": "PostgreSQL",
        "memory_ids": ["mem-db-pref"],
        "category": "single-session-user",
    },
    {
        "id": "ssu-002",
        "question": "What programming language does the user primarily work in?",
        "answer": "Python",
        "memory_ids": ["mem-lang-pref"],
        "category": "single-session-user",
    },
    # --- single-session-assistant (2) ---
    {
        "id": "ssa-001",
        "question": "What did the assistant recommend for error handling?",
        "answer": "try/except with logging",
        "memory_ids": ["mem-error-handling"],
        "category": "single-session-assistant",
    },
    {
        "id": "ssa-002",
        "question": "What testing framework did the assistant suggest for the project?",
        "answer": "pytest",
        "memory_ids": ["mem-test-framework"],
        "category": "single-session-assistant",
    },
    # --- multi-session (2) ---
    {
        "id": "ms-001",
        "question": "What recurring theme appears across multiple sessions?",
        "answer": "technical debt reduction",
        "memory_ids": ["mem-session-1", "mem-session-3", "mem-session-5"],
        "category": "multi-session",
    },
    {
        "id": "ms-002",
        "question": "Which performance concern has the user mentioned in multiple conversations?",
        "answer": "slow query times",
        "memory_ids": ["mem-perf-1", "mem-perf-2"],
        "category": "multi-session",
    },
    # --- temporal-reasoning (2) ---
    {
        "id": "tr-001",
        "question": "When did the user last discuss the authentication system?",
        "answer": "two weeks ago",
        "memory_ids": ["mem-auth-discussion"],
        "category": "temporal-reasoning",
    },
    {
        "id": "tr-002",
        "question": "How long ago did the team decide to migrate to microservices?",
        "answer": "six months ago",
        "memory_ids": ["mem-microservices-decision"],
        "category": "temporal-reasoning",
    },
    # --- knowledge-update (2) ---
    {
        "id": "ku-001",
        "question": "What is the current API endpoint after the recent change?",
        "answer": "https://api.example.com/v2/users",
        "memory_ids": ["mem-api-update"],
        "category": "knowledge-update",
    },
    {
        "id": "ku-002",
        "question": "What is the updated deployment target after the infrastructure migration?",
        "answer": "Kubernetes",
        "memory_ids": ["mem-infra-migration"],
        "category": "knowledge-update",
    },
]


# ---------------------------------------------------------------------------
# LongMemEvalAdapter
# ---------------------------------------------------------------------------


class LongMemEvalAdapter:
    """
    Wraps a retrieval callable and scores LongMemEval questions.

    Args:
        retrieval_fn: Callable that accepts a query string and returns a list
                      of memory content strings. Signature:
                          retrieval_fn(query: str) -> list[str]

    Example (stub for offline testing)::

        adapter = LongMemEvalAdapter(retrieval_fn=lambda q: [])
        results = adapter.run_fixture()
        print(adapter.aggregate(results))

    Example (wired to Phase 7 retrieval engine)::

        # TODO(Phase 7): Wire to core.retrieval.RetrievalEngine.active_search()
        # engine = RetrievalEngine(store)
        # adapter = LongMemEvalAdapter(retrieval_fn=engine.active_search)
        pass
    """

    def __init__(self, retrieval_fn: Callable[[str], list[str]]) -> None:
        self._retrieval_fn = retrieval_fn

    def run_question(self, q: dict) -> LongMemEvalResult:
        """
        Score a single question dict against the retrieval callable.

        Args:
            q: Dict with keys: id, question, answer, memory_ids, category.

        Returns:
            LongMemEvalResult with score 1.0 if the expected answer appears
            (case-insensitive substring) in any retrieved string, else 0.0.
        """
        retrieved = self._retrieval_fn(q["question"])
        expected = q["answer"].lower()

        is_correct = any(expected in ctx.lower() for ctx in retrieved)
        score = 1.0 if is_correct else 0.0
        predicted = q["answer"] if is_correct else ""

        return LongMemEvalResult(
            question_id=q["id"],
            question=q["question"],
            expected_answer=q["answer"],
            retrieved_context=retrieved,
            predicted_answer=predicted,
            is_correct=is_correct,
            score=score,
        )

    def run_fixture(self) -> list[LongMemEvalResult]:
        """
        Run all LONGMEMEVAL_FIXTURE questions through the retrieval callable.

        If the environment variable LONGMEMEVAL_DATASET_PATH is set, questions
        are loaded from the full dataset at that path instead of the built-in
        fixture. (Full dataset loading is not implemented until Phase 7.)

        Returns:
            List of LongMemEvalResult, one per question.
        """
        dataset_path = os.environ.get("LONGMEMEVAL_DATASET_PATH")
        if dataset_path:
            # Full dataset loading deferred to Phase 7 (Hybrid RRF).
            # When implemented, load from dataset_path and run all 500 questions.
            raise NotImplementedError(
                "Full dataset loading from LONGMEMEVAL_DATASET_PATH is deferred "
                "to Phase 7 (Hybrid RRF). Remove this env var to use the built-in "
                "10-question fixture."
            )
        return [self.run_question(q) for q in LONGMEMEVAL_FIXTURE]

    def aggregate(self, results: list[LongMemEvalResult]) -> dict:
        """
        Compute aggregate accuracy from a list of scored results.

        Args:
            results: List of LongMemEvalResult.

        Returns:
            Dict with keys:
              - "accuracy": float — fraction of correct answers overall
              - "by_category": dict mapping category → accuracy float
              - "total": int — total number of questions scored
        """
        if not results:
            return {"accuracy": 0.0, "by_category": {}, "total": 0}

        total = len(results)
        correct = sum(1 for r in results if r.is_correct)
        accuracy = correct / total

        # Per-category accuracy
        category_correct: dict[str, int] = defaultdict(int)
        category_total: dict[str, int] = defaultdict(int)

        for r in results:
            # Look up category from fixture by question_id
            category = _get_category(r.question_id, results)
            category_total[category] += 1
            if r.is_correct:
                category_correct[category] += 1

        by_category = {
            cat: category_correct[cat] / category_total[cat]
            for cat in category_total
        }

        return {
            "accuracy": accuracy,
            "by_category": by_category,
            "total": total,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FIXTURE_CATEGORY_MAP: dict[str, str] = {
    q["id"]: q["category"] for q in LONGMEMEVAL_FIXTURE
}


def _get_category(question_id: str, results: list[LongMemEvalResult]) -> str:
    """Look up category for a question_id from fixture map, fallback to 'unknown'."""
    return _FIXTURE_CATEGORY_MAP.get(question_id, "unknown")
