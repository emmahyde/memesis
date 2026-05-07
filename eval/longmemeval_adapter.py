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
    # From: PR split session (ce238b2a) — user asked to split PR into deployable slices
    {
        "id": "ssu-001",
        "question": "How does the user want large PRs broken down?",
        "answer": "independently deployable slices",
        "memory_ids": [],
        "category": "single-session-user",
    },
    # From: glop PR review (15ecac0a) — user asked for multi-perspective panel review
    {
        "id": "ssu-002",
        "question": "How does the user prefer code reviews to be structured?",
        "answer": "multiple perspectives",
        "memory_ids": [],
        "category": "single-session-user",
    },
    # --- single-session-assistant (2) ---
    # From: minion review sessions — assistant learned to verify against git
    {
        "id": "ssa-001",
        "question": "What should the assistant verify before claiming a fix is missing?",
        "answer": "verify against current code",
        "memory_ids": [],
        "category": "single-session-assistant",
    },
    # From: review iteration sessions — assistant learned about stale review files
    {
        "id": "ssa-002",
        "question": "What risk exists with review files during iterative fix rounds?",
        "answer": "stale",
        "memory_ids": [],
        "category": "single-session-assistant",
    },
    # --- multi-session (2) ---
    # From: many sessions — user uses worktrees + .minions structure
    {
        "id": "ms-001",
        "question": "What directory structure does the user use for ticket work?",
        "answer": "worktree",
        "memory_ids": [],
        "category": "multi-session",
    },
    # From: many sessions — user expects autonomous execution without check-ins
    {
        "id": "ms-002",
        "question": "Does the user expect Claude to check in during multi-step tasks?",
        "answer": "without check-ins",
        "memory_ids": [],
        "category": "multi-session",
    },
    # --- temporal-reasoning (2) ---
    # From: bamboo CLI session (2c51f984) — recent hook-based fix for skill triggering
    {
        "id": "tr-001",
        "question": "What CLI tool had reliability issues with skill triggering recently?",
        "answer": "bamboo",
        "memory_ids": [],
        "category": "temporal-reasoning",
    },
    # From: YARD docs session (c1e19820) — investigated GitHub Pages generation
    {
        "id": "tr-002",
        "question": "What documentation system was the user investigating for the ai-tools repo?",
        "answer": "YARD",
        "memory_ids": [],
        "category": "temporal-reasoning",
    },
    # --- knowledge-update (2) ---
    # From: hook testing session (62b3a3d3) — hooks evaluated at runtime, can't test outside session
    {
        "id": "ku-001",
        "question": "What happens to plugin hooks when they are changed mid-session?",
        "answer": "snapshot",
        "memory_ids": [],
        "category": "knowledge-update",
    },
    # From: agents kill pattern — user kills off-track agents rather than waiting
    {
        "id": "ku-002",
        "question": "What does the user do when an agent goes off-track?",
        "answer": "kill",
        "memory_ids": [],
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

    Example (wired to retrieval engine — Phase 7+)::

        from core.retrieval import RetrievalEngine
        engine = RetrievalEngine(store)
        adapter = LongMemEvalAdapter(
            retrieval_fn=lambda q: [
                m["content"] for m in engine.active_search(q, session_id="eval")
            ]
        )
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
