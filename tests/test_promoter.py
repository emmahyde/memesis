"""Tests for core/promoter.py — stored-vs-stored contradiction resolution."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import close_db, init_db
from core.models import (
    ContradictionReview,
    ConsolidationLog,
    Memory,
    MemoryEdge,
)
from core.promoter import (
    MAX_LLM_RETRIES,
    _recheck_fingerprint,
    _resolve_edge,
    has_blocking_contradiction,
    resolve_contradictions_pass,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _mem(stage="consolidated", title="T", content="C", **kwargs) -> Memory:
    now = datetime.now(timezone.utc).isoformat()
    return Memory.create(
        stage=stage,
        title=title,
        summary=f"sum {title}",
        content=content,
        tags=json.dumps([]),
        importance=kwargs.get("importance", 0.5),
        reinforcement_count=0,
        usage_count=0,
        created_at=now,
        updated_at=now,
    )


def _edge(src: Memory, tgt: Memory, state: str = "unresolved") -> MemoryEdge:
    return MemoryEdge.create(
        source_id=src.id,
        target_id=tgt.id,
        edge_type="contradicts",
        weight=0.7,
        metadata=json.dumps({"rationale": "test contradiction"}),
        resolution_state=state,
    )


# ---------------------------------------------------------------------------
# has_blocking_contradiction
# ---------------------------------------------------------------------------


class TestHasBlockingContradiction:
    def test_no_edges_not_blocked(self, db):
        m = _mem()
        blocked, _ = has_blocking_contradiction(m.id)
        assert not blocked

    def test_unresolved_edge_blocks(self, db):
        a, b = _mem(), _mem()
        _edge(a, b, state="unresolved")
        blocked, reason = has_blocking_contradiction(a.id)
        assert blocked
        assert "unresolved" in reason

    def test_queued_edge_blocks(self, db):
        a, b = _mem(), _mem()
        _edge(a, b, state="queued")
        blocked, _ = has_blocking_contradiction(a.id)
        assert blocked

    def test_resolved_edge_not_blocked(self, db):
        a, b = _mem(), _mem()
        _edge(a, b, state="resolved")
        blocked, _ = has_blocking_contradiction(a.id)
        assert not blocked

    def test_edge_incident_to_different_memory_not_blocked(self, db):
        a, b, c = _mem(), _mem(), _mem()
        _edge(b, c, state="unresolved")
        blocked, _ = has_blocking_contradiction(a.id)
        assert not blocked

    def test_target_side_edge_also_blocks(self, db):
        a, b = _mem(), _mem()
        _edge(b, a, state="unresolved")  # a is target
        blocked, _ = has_blocking_contradiction(a.id)
        assert blocked


# ---------------------------------------------------------------------------
# _resolve_edge — per-verdict transitions
# ---------------------------------------------------------------------------


SUPERSEDE_RESPONSE = json.dumps({
    "verdict": "SUPERSEDE",
    "winner_id": "__WINNER__",
    "merged_content": None,
    "rationale": "B is outdated",
})

ARCHIVE_RESPONSE = json.dumps({
    "verdict": "ARCHIVE",
    "winner_id": "__WINNER__",
    "merged_content": None,
    "rationale": "A is stale",
})

REFINE_RESPONSE = json.dumps({
    "verdict": "REFINE",
    "winner_id": "__WINNER__",
    "merged_content": "merged content here",
    "rationale": "Both partly right",
})

BLOCK_RESPONSE = json.dumps({
    "verdict": "BLOCK",
    "winner_id": None,
    "merged_content": None,
    "rationale": "Ambiguous",
})


class TestResolveEdgeSUPERSEDE:
    def test_edge_resolved_loser_archived_no_review(self, db):
        a, b = _mem(content="A content"), _mem(content="B content")
        e = _edge(a, b)
        resp = SUPERSEDE_RESPONSE.replace("__WINNER__", a.id)
        with patch("core.promoter.call_llm", return_value=resp):
            _resolve_edge(e, "sess")
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "resolved"
        b_fresh = Memory.get_by_id(b.id)
        assert b_fresh.archived_at is not None
        assert "[Superseded]" in b_fresh.title
        assert ContradictionReview.select().count() == 0


class TestResolveEdgeARCHIVE:
    def test_edge_resolved_loser_archived(self, db):
        a, b = _mem(content="A content"), _mem(content="B content")
        e = _edge(a, b)
        resp = ARCHIVE_RESPONSE.replace("__WINNER__", b.id)
        with patch("core.promoter.call_llm", return_value=resp):
            _resolve_edge(e, "sess")
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "resolved"
        a_fresh = Memory.get_by_id(a.id)
        assert a_fresh.archived_at is not None


class TestResolveEdgeREFINE:
    def test_edge_resolved_winner_updated_loser_archived(self, db):
        a, b = _mem(content="A content"), _mem(content="B content")
        e = _edge(a, b)
        resp = REFINE_RESPONSE.replace("__WINNER__", a.id)
        with patch("core.promoter.call_llm", return_value=resp):
            _resolve_edge(e, "sess")
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "resolved"
        a_fresh = Memory.get_by_id(a.id)
        assert a_fresh.content == "merged content here"
        b_fresh = Memory.get_by_id(b.id)
        assert b_fresh.archived_at is not None
        assert ContradictionReview.select().count() == 0


class TestResolveEdgeBLOCK:
    def test_edge_queued_review_row_created(self, db):
        a, b = _mem(), _mem()
        e = _edge(a, b)
        with patch("core.promoter.call_llm", return_value=BLOCK_RESPONSE):
            _resolve_edge(e, "sess")
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "queued"
        review = ContradictionReview.select().first()
        assert review is not None
        assert review.status == "open"
        assert review.edge_id == e.id


# ---------------------------------------------------------------------------
# Instinctive guard
# ---------------------------------------------------------------------------


class TestInstinctiveGuard:
    def test_both_instinctive_no_llm_call_edge_queued(self, db):
        a = _mem(stage="instinctive")
        b = _mem(stage="instinctive")
        e = _edge(a, b)
        with patch("core.promoter.call_llm") as mock_llm:
            _resolve_edge(e, "sess")
            mock_llm.assert_not_called()
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "queued"
        review = ContradictionReview.select().first()
        assert review.llm_rationale == "both-instinctive-guard"

    def test_instinctive_guard_in_pass_skips_both(self, db):
        a = _mem(stage="instinctive")
        b = _mem(stage="instinctive")
        _edge(a, b)
        with patch("core.promoter.call_llm") as mock_llm:
            result = resolve_contradictions_pass("sess")
            mock_llm.assert_not_called()
        assert result["skipped"] == 1


# ---------------------------------------------------------------------------
# C-recheck
# ---------------------------------------------------------------------------


class TestCRecheck:
    def test_fingerprint_change_reopens_edge(self, db):
        a, b = _mem(content="original A"), _mem(content="original B")
        e = _edge(a, b, state="queued")
        fp = _recheck_fingerprint(a, b)
        ContradictionReview.create(
            memory_id=a.id,
            edge_id=e.id,
            other_memory_id=b.id,
            status="open",
            created_at=datetime.now(timezone.utc).isoformat(),
            recheck_fingerprint=fp,
            retry_count=0,
        )
        # Mutate memory A so fingerprint changes
        Memory.update(content="updated A content").where(Memory.id == a.id).execute()

        with patch("core.promoter.call_llm", return_value=BLOCK_RESPONSE):
            result = resolve_contradictions_pass("sess")

        assert result["rechecked"] == 1
        review = ContradictionReview.get_by_id(1)
        assert review.status == "resolved"
        assert review.llm_rationale == "superseded-by-recheck"
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "unresolved"

    def test_unchanged_fingerprint_does_not_reopen(self, db):
        a, b = _mem(content="stable A"), _mem(content="stable B")
        e = _edge(a, b, state="queued")
        fp = _recheck_fingerprint(a, b)
        ContradictionReview.create(
            memory_id=a.id,
            edge_id=e.id,
            other_memory_id=b.id,
            status="open",
            created_at=datetime.now(timezone.utc).isoformat(),
            recheck_fingerprint=fp,
            retry_count=0,
        )
        with patch("core.promoter.call_llm", return_value=BLOCK_RESPONSE):
            result = resolve_contradictions_pass("sess")
        assert result["rechecked"] == 0
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "queued"


# ---------------------------------------------------------------------------
# LLM error path
# ---------------------------------------------------------------------------


class TestLLMErrorPath:
    def test_single_error_retry_count_1_edge_stays_unresolved(self, db):
        a, b = _mem(), _mem()
        e = _edge(a, b)
        with patch("core.promoter.call_llm", side_effect=RuntimeError("timeout")):
            _resolve_edge(e, "sess")
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "unresolved"
        review = ContradictionReview.select().first()
        assert review.retry_count == 1

    def test_max_retries_converts_to_queued(self, db):
        a, b = _mem(), _mem()
        e = _edge(a, b)
        with patch("core.promoter.call_llm", side_effect=RuntimeError("timeout")):
            for _ in range(MAX_LLM_RETRIES):
                _resolve_edge(e, "sess")
        e_fresh = MemoryEdge.get_by_id(e.id)
        assert e_fresh.resolution_state == "queued"
        review = ContradictionReview.select().first()
        assert review.retry_count == MAX_LLM_RETRIES


# ---------------------------------------------------------------------------
# Promotion gate integration
# ---------------------------------------------------------------------------


class TestPromotionGate:
    def test_queued_edge_blocks_can_promote(self, db):
        from core.lifecycle import LifecycleManager
        a, b = _mem(stage="consolidated"), _mem(stage="consolidated")
        _edge(a, b, state="queued")
        mgr = LifecycleManager()
        ok, reason = mgr.can_promote(a.id)
        assert not ok
        assert "contradiction" in reason.lower()

    def test_resolved_edge_does_not_block(self, db):
        from core.lifecycle import LifecycleManager
        a = _mem(stage="consolidated", importance=0.8)
        b2 = _mem(stage="consolidated")
        _edge(a, b2, state="resolved")
        mgr = LifecycleManager()
        # consolidated→crystallized requires reinforcement; just check it doesn't
        # block on the contradiction guard (reason won't mention "contradiction")
        ok, reason = mgr.can_promote(a.id)
        assert "contradiction" not in reason.lower()

    def test_no_edges_not_blocked_by_guard(self, db):
        from core.lifecycle import LifecycleManager
        m = _mem(stage="consolidated")
        mgr = LifecycleManager()
        ok, reason = mgr.can_promote(m.id)
        assert "contradiction" not in reason.lower()


# ---------------------------------------------------------------------------
# Bidirectional sync in resolve_contradictions_pass
# ---------------------------------------------------------------------------


class TestBidirectionalSync:
    def test_reverse_edge_synced_to_resolved(self, db):
        a, b = _mem(), _mem()
        fwd = _edge(a, b, state="unresolved")
        rev = _edge(b, a, state="unresolved")
        resp = SUPERSEDE_RESPONSE.replace("__WINNER__", a.id)
        with patch("core.promoter.call_llm", return_value=resp):
            result = resolve_contradictions_pass("sess")
        fwd_fresh = MemoryEdge.get_by_id(fwd.id)
        rev_fresh = MemoryEdge.get_by_id(rev.id)
        assert fwd_fresh.resolution_state == "resolved"
        assert rev_fresh.resolution_state == "resolved"
        assert result["resolved"] == 1  # pair counted once
