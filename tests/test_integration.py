"""
End-to-end integration tests for the full memory lifecycle.

Validates: ephemeral -> consolidated -> crystallized -> injected
All LLM calls are mocked; no real API requests are made.
Runs in isolated tmp_path directories; no real filesystem pollution.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.feedback import FeedbackLoop
from core.lifecycle import LifecycleManager
from core.manifest import ManifestGenerator
from core.retrieval import RetrievalEngine
from core.database import init_db, close_db, get_base_dir, get_db_path
from core.models import Memory, ConsolidationLog, RetrievalLog, db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_memory(stage, title, content, summary=None, importance=0.5, tags=None):
    """Create a memory via Peewee and return its ID."""
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage=stage,
        title=title,
        summary=summary or f"Summary of {title}",
        content=content,
        tags=json.dumps(tags or []),
        importance=importance,
        created_at=now,
        updated_at=now,
    )
    return mem.id


def _mock_decisions(kept_count: int = 2, prune_count: int = 8) -> list[dict]:
    """Build the list of decision dicts that _call_llm returns."""
    decisions = []
    for i in range(kept_count):
        decisions.append({
            "observation": f"Observation {i}",
            "action": "keep",
            "rationale": f"Important fact {i}",
            "title": f"Memory {i}",
            "summary": f"Summary of memory {i}",
            "tags": ["test"],
            "target_path": f"observations/memory_{i}.md",
            "reinforces": None,
            "contradicts": None,
        })
    for i in range(prune_count):
        decisions.append({
            "observation": f"Trivial {i}",
            "action": "prune",
            "rationale": "Not worth keeping",
            "title": None,
            "summary": None,
            "tags": [],
            "target_path": None,
            "reinforces": None,
            "contradicts": None,
        })
    return decisions


def _promote_decisions_for(memory_ids: list[str]) -> list[dict]:
    """Return PROMOTE decisions that reinforce each memory in memory_ids."""
    return [
        {
            "observation": f"Re-confirmed observation for {mid}",
            "action": "promote",
            "rationale": "Reinforcing existing memory",
            "title": None,
            "summary": None,
            "tags": [],
            "target_path": None,
            "reinforces": mid,
            "contradicts": None,
        }
        for mid in memory_ids
    ]


def _write_ephemeral(tmp_path: Path, filename: str, n_obs: int = 10) -> Path:
    """Write n_obs lines to an ephemeral session file and return the path."""
    p = tmp_path / filename
    lines = [f"- Technical observation number {i}\n" for i in range(n_obs)]
    p.write_text("".join(lines), encoding="utf-8")
    return p


def _record_injection(memory_id, session_id):
    """Helper: record an injection."""
    now = datetime.now().isoformat()
    Memory.update(
        last_injected_at=now,
        injection_count=Memory.injection_count + 1,
    ).where(Memory.id == memory_id).execute()
    RetrievalLog.create(
        timestamp=now,
        session_id=session_id,
        memory_id=memory_id,
        retrieval_type='injected',
    )


# ---------------------------------------------------------------------------
# Test: full lifecycle -- ephemeral -> consolidated -> crystallized -> injected
# ---------------------------------------------------------------------------


class TestFullLifecycleEphemeralToCrystallized:

    def test_full_lifecycle_ephemeral_to_crystallized(self, tmp_path):
        init_db(base_dir=str(tmp_path / "memory"))
        try:
            lifecycle = LifecycleManager()
            consolidator = Consolidator(lifecycle=lifecycle)

            ephemeral_dir = get_base_dir() / "ephemeral"
            ephemeral_dir.mkdir(parents=True, exist_ok=True)

            # Session 1: 10 observations -> 2 KEEP, 8 PRUNE
            session1_file = tmp_path / "session1.md"
            _write_ephemeral(tmp_path, "session1.md", n_obs=10)

            with patch("core.consolidator._call_llm_transport",
                       return_value=json.dumps({"decisions": _mock_decisions(2, 8)})):
                result = consolidator.consolidate_session(str(session1_file), "session-1")

            assert len(result["kept"]) == 2
            assert len(result["pruned"]) == 8

            kept_ids = result["kept"]

            consolidated = list(Memory.by_stage("consolidated"))
            assert len(consolidated) == 2

            # Sessions 2-4: reinforce the 2 kept memories via PROMOTE decisions
            for session_num in range(2, 5):
                session_file = tmp_path / f"session{session_num}.md"
                _write_ephemeral(tmp_path, f"session{session_num}.md", n_obs=2)

                promote_decisions = _promote_decisions_for(kept_ids)

                with patch("core.consolidator._call_llm_transport",
                           return_value=json.dumps({"decisions": promote_decisions})):
                    result = consolidator.consolidate_session(
                        str(session_file), f"session-{session_num}"
                    )

                assert len(result["promoted"]) == 2

            # Backdate reinforcement log entries to different days
            rows = list(
                ConsolidationLog.select()
                .where(
                    (ConsolidationLog.action == 'promoted') &
                    (ConsolidationLog.from_stage == ConsolidationLog.to_stage)
                )
                .order_by(ConsolidationLog.id)
            )
            for i, row in enumerate(rows):
                fake_date = (datetime(2026, 3, 10) + timedelta(days=i)).isoformat()
                ConsolidationLog.update(timestamp=fake_date).where(
                    ConsolidationLog.id == row.id
                ).execute()

            # After session 4: reinforcement_count must be >= 3
            for mid in kept_ids:
                memory = Memory.get_by_id(mid)
                assert memory.reinforcement_count >= 3

            # get_promotion_candidates must return both memories
            candidates = lifecycle.get_promotion_candidates()
            candidate_ids = {c["id"] for c in candidates}
            for mid in kept_ids:
                assert mid in candidate_ids

            # Promote to crystallized
            for mid in kept_ids:
                new_stage = lifecycle.promote(mid, rationale="3+ reinforcements reached")
                assert new_stage == "crystallized"

            crystallized = list(Memory.by_stage("crystallized"))
            assert len(crystallized) == 2

            assert list(Memory.by_stage("consolidated")) == []

            # Session 5: injection must include crystallized memories
            retrieval = RetrievalEngine()
            injected_context = retrieval.inject_for_session("session-5")

            assert injected_context
            assert "---MEMORY CONTEXT---" in injected_context
            assert "Context-Relevant Knowledge" in injected_context

            for mid in kept_ids:
                memory = Memory.get_by_id(mid)
                title = memory.title or ""
                if title:
                    assert title in injected_context
                    break
        finally:
            close_db()


# ---------------------------------------------------------------------------
# Test: /learn -- direct create in consolidated stage
# ---------------------------------------------------------------------------


class TestLearnMemoryExplicitly:
    def test_learn_memory_explicitly(self, tmp_path):
        init_db(base_dir=str(tmp_path / "memory"))
        try:
            memory_id = _create_memory(
                "consolidated",
                "Direct Fact",
                "This is a fact learned directly via /learn.",
                summary="A fact created via direct /learn invocation.",
                tags=["learned"],
            )

            consolidated = list(Memory.by_stage("consolidated"))
            assert len(consolidated) == 1
            assert consolidated[0].id == memory_id
            assert consolidated[0].title == "Direct Fact"

            manifest_gen = ManifestGenerator()
            manifest_gen.write_manifest()

            manifest_path = get_base_dir() / "MEMORY.md"
            assert manifest_path.exists()
            content = manifest_path.read_text(encoding="utf-8")
            assert "Direct Fact" in content
            assert "Consolidated" in content
        finally:
            close_db()


# ---------------------------------------------------------------------------
# Test: deprecate (forget) removes from store and archives
# ---------------------------------------------------------------------------


class TestForgetMemory:
    def test_forget_memory(self, tmp_path):
        init_db(base_dir=str(tmp_path / "memory"))
        try:
            lifecycle = LifecycleManager()

            memory_id = _create_memory(
                "consolidated",
                "To Delete",
                "This memory should be forgotten.",
                summary="A memory that will be deprecated.",
            )

            # Deprecate it
            lifecycle.deprecate(memory_id, rationale="User requested forget")

            # No longer retrievable
            with pytest.raises(Memory.DoesNotExist):
                Memory.get_by_id(memory_id)

            # Verify deprecation was logged
            row = ConsolidationLog.get_or_none(
                (ConsolidationLog.memory_id == memory_id) &
                (ConsolidationLog.action == 'deprecated')
            )
            assert row is not None
        finally:
            close_db()


# ---------------------------------------------------------------------------
# Test: retrieval log completeness
# ---------------------------------------------------------------------------


class TestRetrievalLogCompleteness:
    def test_retrieval_log_completeness(self, tmp_path):
        init_db(base_dir=str(tmp_path / "memory"))
        try:
            crystal_id = _create_memory(
                "crystallized",
                "Crystal Memory",
                "A crystallized memory.",
                summary="Crystallized fact.",
                importance=0.8,
            )

            instinct_id = _create_memory(
                "instinctive",
                "Helpfulness Guideline",
                "Always be helpful.",
                summary="Always be helpful.",
            )

            retrieval = RetrievalEngine()
            injected_context = retrieval.inject_for_session("log-test-session")

            assert injected_context

            log = list(RetrievalLog.select())
            assert len(log) >= 2

            logged_memory_ids = {entry.memory_id for entry in log}
            assert crystal_id in logged_memory_ids
            assert instinct_id in logged_memory_ids

            for entry in log:
                assert entry.retrieval_type == "injected"
        finally:
            close_db()


# ---------------------------------------------------------------------------
# Test: manifest reflects actual state
# ---------------------------------------------------------------------------


class TestManifestReflectsActualState:
    def test_manifest_reflects_actual_state(self, tmp_path):
        init_db(base_dir=str(tmp_path / "memory"))
        try:
            _create_memory("instinctive", "Core Guideline", "Core behavioral guideline.", summary="Always active.")
            _create_memory("crystallized", "Crystal Fact", "An important crystallized memory.", summary="Important crystallized knowledge.", importance=0.9)
            _create_memory("consolidated", "Consolidated Note", "A consolidated observation.", summary="A recent observation.")

            manifest_gen = ManifestGenerator()
            manifest_gen.write_manifest()

            manifest_path = get_base_dir() / "MEMORY.md"
            assert manifest_path.exists()
            content = manifest_path.read_text(encoding="utf-8")

            assert "Instinctive" in content
            assert "Crystallized" in content
            assert "Consolidated" in content
            assert "Core Guideline" in content
            assert "Crystal Fact" in content
            assert "Consolidated Note" in content
            assert "Token budget" in content
        finally:
            close_db()


# ---------------------------------------------------------------------------
# Test: feedback importance decay
# ---------------------------------------------------------------------------


class TestFeedbackImportanceUpdates:
    def test_feedback_importance_updates(self, tmp_path):
        init_db(base_dir=str(tmp_path / "memory"))
        try:
            lifecycle = LifecycleManager()
            feedback = FeedbackLoop(lifecycle)

            memory_id = _create_memory(
                "crystallized",
                "Unused Crystal",
                "A crystallized memory that is never used.",
                summary="Never referenced.",
                importance=0.5,
            )

            for i in range(1, 4):
                _record_injection(memory_id, f"session-{i}")

            feedback.update_importance_scores("session-1")

            updated = Memory.get_by_id(memory_id)
            assert updated.importance < 0.5
        finally:
            close_db()


# ---------------------------------------------------------------------------
# Test: privacy filter blocks emotional state
# ---------------------------------------------------------------------------


class TestPrivacyFilterBlocksEmotionalState:
    def test_privacy_filter_blocks_emotional_state(self, tmp_path):
        init_db(base_dir=str(tmp_path / "memory"))
        try:
            lifecycle = LifecycleManager()
            consolidator = Consolidator(lifecycle=lifecycle)

            ephemeral_text = (
                "Emma seemed frustrated with the approach\n"
                "Uses pytest for all tests\n"
                "Prefers black for formatting\n"
            )

            filtered, was_filtered = consolidator.filter_privacy(ephemeral_text)

            assert was_filtered is True
            assert "frustrated" not in filtered
            assert "Emma seemed frustrated" not in filtered
            assert "pytest" in filtered
            assert "black" in filtered
        finally:
            close_db()
