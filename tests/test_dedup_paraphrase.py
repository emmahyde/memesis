"""Paraphrase-aware dedup — LLM confirmation for near-miss duplicate candidates.

The cosine-0.85 auto-promote threshold misses reworded duplicates (the canvas
review confirmed the cron+cursor, evolve-skip-consolidation, and PROMOTE-gate
pairs). Candidates in the near-miss band [0.70, 0.85) are now escalated to an
LLM duplicate-confirmation call. These tests pin that mechanism and guard
against over-merge.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.database import close_db, init_db
from core.linking import auto_promote_if_dupe
from core.models import Memory


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _mem(title, content) -> Memory:
    return Memory.create(stage="consolidated", title=title, summary=title, content=content)


def _links_factory(near_miss_id, near_score=0.78):
    """find_links stub: nothing at the 0.85 hard threshold, a near-miss below it."""
    def fake(memory, candidates, threshold, top_k=1):
        if threshold >= 0.85:
            return []
        return [(near_miss_id, near_score)]
    return fake


def test_paraphrase_dupe_subsumed_when_llm_confirms(db):
    existing = _mem("Cron reads transcript via byte cursor",
                    "The cron tails the JSONL transcript from a per-session byte cursor.")
    new = _mem("Transcript ingestion is cursor-based",
               "A byte cursor per session lets the cron resume transcript reading.")

    with patch("core.linking.find_links_for_observation", _links_factory(existing.id)), \
         patch("core.llm.call_llm", return_value="DUPLICATE"):
        survivor = auto_promote_if_dupe(new)

    assert survivor == existing.id
    assert Memory.get_by_id(new.id).archived_at is not None
    assert Memory.get_by_id(new.id).subsumed_by == existing.id
    assert Memory.get_by_id(existing.id).reinforcement_count == 1


def test_near_miss_not_subsumed_when_llm_says_distinct(db):
    """Over-merge guard: a near-miss the LLM rejects must survive untouched."""
    existing = _mem("Use uv run for Python", "Always invoke Python via uv run.")
    new = _mem("Cron schedule is hourly", "The consolidation cron fires once an hour.")

    with patch("core.linking.find_links_for_observation", _links_factory(existing.id)), \
         patch("core.llm.call_llm", return_value="DISTINCT"):
        survivor = auto_promote_if_dupe(new)

    assert survivor is None
    assert Memory.get_by_id(new.id).archived_at is None
    assert Memory.get_by_id(existing.id).reinforcement_count == 0


def test_no_near_miss_skips_llm_entirely(db):
    existing = _mem("A", "content a")
    new = _mem("B", "content b")

    with patch("core.linking.find_links_for_observation", return_value=[]), \
         patch("core.llm.call_llm") as mock_llm:
        survivor = auto_promote_if_dupe(new)
        mock_llm.assert_not_called()

    assert survivor is None


def test_hard_cosine_hit_subsumes_without_llm(db):
    existing = _mem("A", "content a")
    new = _mem("B", "content b")

    def hard_hit(memory, candidates, threshold, top_k=1):
        return [(existing.id, 0.93)] if threshold >= 0.85 else []

    with patch("core.linking.find_links_for_observation", hard_hit), \
         patch("core.llm.call_llm") as mock_llm:
        survivor = auto_promote_if_dupe(new)
        mock_llm.assert_not_called()

    assert survivor == existing.id


def test_llm_error_does_not_subsume(db):
    """A failed LLM call must never trigger the destructive subsumption."""
    existing = _mem("A", "content a")
    new = _mem("B", "content b")

    with patch("core.linking.find_links_for_observation", _links_factory(existing.id)), \
         patch("core.llm.call_llm", side_effect=RuntimeError("timeout")):
        survivor = auto_promote_if_dupe(new)

    assert survivor is None
    assert Memory.get_by_id(new.id).archived_at is None
