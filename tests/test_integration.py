"""
End-to-end integration tests for the full memory lifecycle.

Validates: ephemeral → consolidated → crystallized → injected
All LLM calls are mocked; no real API requests are made.
Runs in isolated tmp_path directories; no real filesystem pollution.
"""

import json
import sqlite3
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
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> MemoryStore:
    """Return a fresh MemoryStore backed by tmp_path."""
    return MemoryStore(base_dir=str(tmp_path / "memory"))


def _mock_decisions(kept_count: int = 2, prune_count: int = 8) -> list[dict]:
    """
    Build the list of decision dicts that _call_llm returns.

    _call_llm returns a list[dict] (already parsed from JSON).  The task spec
    shows json.dumps() as the return value — that works only if _call_llm is
    being patched on the *Anthropic client* side; when patching _call_llm
    itself we return the list directly so consolidate_session can iterate it.
    """
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


# ---------------------------------------------------------------------------
# Test: full lifecycle — ephemeral → consolidated → crystallized → injected
# ---------------------------------------------------------------------------


class TestFullLifecycleEphemeralToCrystallized:
    """
    Validates the complete promotion ladder:
      Session 1  — 10 observations, 2 KEEP → consolidated
      Sessions 2-4 — same 2 memories reinforced via PROMOTE → reinforcement_count grows
      After session 4 — both memories have reinforcement_count >= 3
      Promote — both move to crystallized
      Session 5 — RetrievalEngine injects crystallized memories
    """

    def test_full_lifecycle_ephemeral_to_crystallized(self, tmp_path):
        store = _make_store(tmp_path)
        lifecycle = LifecycleManager(store)
        consolidator = Consolidator(store=store, lifecycle=lifecycle)

        ephemeral_dir = store.base_dir / "ephemeral"
        ephemeral_dir.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # Session 1: 10 observations → 2 KEEP, 8 PRUNE
        # ------------------------------------------------------------------
        session1_file = tmp_path / "session1.md"
        _write_ephemeral(tmp_path, "session1.md", n_obs=10)

        with patch.object(consolidator, "_call_llm", return_value=_mock_decisions(2, 8)):
            result = consolidator.consolidate_session(str(session1_file), "session-1")

        assert len(result["kept"]) == 2, f"Expected 2 kept, got {result['kept']}"
        assert len(result["pruned"]) == 8

        kept_ids = result["kept"]

        # Verify 2 files exist in consolidated/
        consolidated = store.list_by_stage("consolidated")
        assert len(consolidated) == 2

        # ------------------------------------------------------------------
        # Sessions 2–4: reinforce the 2 kept memories via PROMOTE decisions
        # ------------------------------------------------------------------
        for session_num in range(2, 5):
            session_file = tmp_path / f"session{session_num}.md"
            _write_ephemeral(tmp_path, f"session{session_num}.md", n_obs=2)

            promote_decisions = _promote_decisions_for(kept_ids)

            with patch.object(consolidator, "_call_llm", return_value=promote_decisions):
                result = consolidator.consolidate_session(
                    str(session_file), f"session-{session_num}"
                )

            assert len(result["promoted"]) == 2, (
                f"Session {session_num}: expected 2 promoted, got {result['promoted']}"
            )

        # ------------------------------------------------------------------
        # Spacing effect: backdate reinforcement log entries to different days
        # so the spacing check sees multi-day spread (in production, sessions
        # genuinely happen on different days).
        # ------------------------------------------------------------------
        with sqlite3.connect(store.db_path) as conn:
            rows = conn.execute(
                """SELECT id, session_id FROM consolidation_log
                   WHERE action = 'promoted' AND from_stage = to_stage
                   ORDER BY id""",
            ).fetchall()
            for i, (row_id, _session_id) in enumerate(rows):
                fake_date = (datetime(2026, 3, 10) + timedelta(days=i)).isoformat()
                conn.execute(
                    "UPDATE consolidation_log SET timestamp = ? WHERE id = ?",
                    (fake_date, row_id),
                )
            conn.commit()

        # ------------------------------------------------------------------
        # After session 4: reinforcement_count must be >= 3
        # ------------------------------------------------------------------
        for mid in kept_ids:
            memory = store.get(mid)
            assert memory["reinforcement_count"] >= 3, (
                f"Memory {mid} has reinforcement_count={memory['reinforcement_count']}, want >=3"
            )

        # ------------------------------------------------------------------
        # get_promotion_candidates must return both memories
        # ------------------------------------------------------------------
        candidates = lifecycle.get_promotion_candidates()
        candidate_ids = {c["id"] for c in candidates}
        for mid in kept_ids:
            assert mid in candidate_ids, f"{mid} not in promotion candidates"

        # ------------------------------------------------------------------
        # Promote to crystallized
        # ------------------------------------------------------------------
        for mid in kept_ids:
            new_stage = lifecycle.promote(mid, rationale="3+ reinforcements reached")
            assert new_stage == "crystallized"

        # Verify files moved to crystallized/
        crystallized = store.list_by_stage("crystallized")
        assert len(crystallized) == 2

        # consolidated/ should now be empty
        assert store.list_by_stage("consolidated") == []

        # ------------------------------------------------------------------
        # Session 5: injection must include crystallized memories
        # ------------------------------------------------------------------
        retrieval = RetrievalEngine(store)
        injected_context = retrieval.inject_for_session("session-5")

        assert injected_context, "Expected non-empty injected context"
        assert "---MEMORY CONTEXT---" in injected_context
        assert "Context-Relevant Knowledge" in injected_context

        # At least one crystallized memory title must appear
        for mid in kept_ids:
            memory = store.get(mid)
            title = memory.get("title") or ""
            if title:
                assert title in injected_context, (
                    f"Crystallized memory title '{title}' not found in injected context"
                )
                break  # One confirmed is sufficient for the assertion


# ---------------------------------------------------------------------------
# Test: /learn — direct create in consolidated stage
# ---------------------------------------------------------------------------


class TestLearnMemoryExplicitly:
    def test_learn_memory_explicitly(self, tmp_path):
        store = _make_store(tmp_path)

        memory_id = store.create(
            path="learned/direct_fact.md",
            content="This is a fact learned directly via /learn.",
            metadata={
                "stage": "consolidated",
                "title": "Direct Fact",
                "summary": "A fact created via direct /learn invocation.",
                "tags": ["learned"],
            },
        )

        # Visible in consolidated stage
        consolidated = store.list_by_stage("consolidated")
        assert len(consolidated) == 1
        assert consolidated[0]["id"] == memory_id
        assert consolidated[0]["title"] == "Direct Fact"

        # Manifest reflects it after write
        manifest_gen = ManifestGenerator(store)
        manifest_gen.write_manifest()

        manifest_path = store.base_dir / "MEMORY.md"
        assert manifest_path.exists()
        content = manifest_path.read_text(encoding="utf-8")
        assert "Direct Fact" in content
        assert "Consolidated" in content


# ---------------------------------------------------------------------------
# Test: deprecate (forget) removes from store and archives
# ---------------------------------------------------------------------------


class TestForgetMemory:
    def test_forget_memory(self, tmp_path):
        store = _make_store(tmp_path)
        lifecycle = LifecycleManager(store)

        memory_id = store.create(
            path="facts/to_delete.md",
            content="This memory should be forgotten.",
            metadata={
                "stage": "consolidated",
                "title": "To Delete",
                "summary": "A memory that will be deprecated.",
            },
        )

        # Confirm it's there
        memory = store.get(memory_id)
        original_file = Path(memory["file_path"])
        assert original_file.exists()

        # Deprecate it
        lifecycle.deprecate(memory_id, rationale="User requested forget")

        # No longer retrievable from active store
        with pytest.raises(ValueError):
            store.get(memory_id)

        # File moved to archived/
        archived_dir = store.base_dir / "archived"
        archived_files = list(archived_dir.iterdir())
        assert len(archived_files) == 1, f"Expected 1 archived file, got {archived_files}"
        assert archived_files[0].name == original_file.name


# ---------------------------------------------------------------------------
# Test: retrieval log completeness
# ---------------------------------------------------------------------------


class TestRetrievalLogCompleteness:
    def test_retrieval_log_completeness(self, tmp_path):
        store = _make_store(tmp_path)

        # Create memories at different stages (retrieval engine uses crystallized
        # and instinctive; we create both)
        crystal_id = store.create(
            path="facts/crystal.md",
            content="A crystallized memory.",
            metadata={
                "stage": "crystallized",
                "title": "Crystal Memory",
                "summary": "Crystallized fact.",
                "importance": 0.8,
            },
        )

        instinct_id = store.create(
            path="guidelines/always.md",
            content="Always be helpful.",
            metadata={
                "stage": "instinctive",
                "title": "Helpfulness Guideline",
                "summary": "Always be helpful.",
            },
        )

        retrieval = RetrievalEngine(store)
        injected_context = retrieval.inject_for_session("log-test-session")

        # Both memories should have been injected
        assert injected_context, "Context should not be empty"

        log = store.get_retrieval_log()
        assert len(log) >= 2, f"Expected at least 2 log entries, got {len(log)}"

        logged_memory_ids = {entry["memory_id"] for entry in log}
        assert crystal_id in logged_memory_ids, "Crystallized memory not in retrieval log"
        assert instinct_id in logged_memory_ids, "Instinctive memory not in retrieval log"

        for entry in log:
            assert entry["retrieval_type"] == "injected", (
                f"Expected retrieval_type='injected', got '{entry['retrieval_type']}'"
            )


# ---------------------------------------------------------------------------
# Test: manifest reflects actual state
# ---------------------------------------------------------------------------


class TestManifestReflectsActualState:
    def test_manifest_reflects_actual_state(self, tmp_path):
        store = _make_store(tmp_path)

        store.create(
            path="guidelines/core.md",
            content="Core behavioral guideline.",
            metadata={
                "stage": "instinctive",
                "title": "Core Guideline",
                "summary": "Always active.",
            },
        )

        store.create(
            path="facts/crystal.md",
            content="An important crystallized memory.",
            metadata={
                "stage": "crystallized",
                "title": "Crystal Fact",
                "summary": "Important crystallized knowledge.",
                "importance": 0.9,
            },
        )

        store.create(
            path="notes/consolidated.md",
            content="A consolidated observation.",
            metadata={
                "stage": "consolidated",
                "title": "Consolidated Note",
                "summary": "A recent observation.",
            },
        )

        manifest_gen = ManifestGenerator(store)
        manifest_gen.write_manifest()

        manifest_path = store.base_dir / "MEMORY.md"
        assert manifest_path.exists()
        content = manifest_path.read_text(encoding="utf-8")

        # All three stages present
        assert "Instinctive" in content, "Instinctive section missing"
        assert "Crystallized" in content, "Crystallized section missing"
        assert "Consolidated" in content, "Consolidated section missing"

        # Memory titles appear
        assert "Core Guideline" in content
        assert "Crystal Fact" in content
        assert "Consolidated Note" in content

        # Token budget comment is present
        assert "Token budget" in content, "Token budget comment missing"


# ---------------------------------------------------------------------------
# Test: feedback importance decay
# ---------------------------------------------------------------------------


class TestFeedbackImportanceUpdates:
    def test_feedback_importance_updates(self, tmp_path):
        store = _make_store(tmp_path)
        lifecycle = LifecycleManager(store)
        feedback = FeedbackLoop(store=store, lifecycle=lifecycle)

        # Create a crystallized memory with default importance 0.5
        memory_id = store.create(
            path="facts/crystal_unused.md",
            content="A crystallized memory that is never used.",
            metadata={
                "stage": "crystallized",
                "title": "Unused Crystal",
                "summary": "Never referenced.",
                "importance": 0.5,
            },
        )

        # Record 3 injections with no usage (was_used stays 0)
        for i in range(1, 4):
            store.record_injection(memory_id, f"session-{i}")

        # Run importance update for an arbitrary session (no usage tracked)
        feedback.update_importance_scores("session-1")

        # Importance should have decreased
        updated = store.get(memory_id)
        assert updated["importance"] < 0.5, (
            f"Importance should have decreased from 0.5, got {updated['importance']}"
        )


# ---------------------------------------------------------------------------
# Test: privacy filter blocks emotional state
# ---------------------------------------------------------------------------


class TestPrivacyFilterBlocksEmotionalState:
    def test_privacy_filter_blocks_emotional_state(self, tmp_path):
        store = _make_store(tmp_path)
        lifecycle = LifecycleManager(store)
        consolidator = Consolidator(store=store, lifecycle=lifecycle)

        ephemeral_text = (
            "Emma seemed frustrated with the approach\n"
            "Uses pytest for all tests\n"
            "Prefers black for formatting\n"
        )

        filtered, was_filtered = consolidator.filter_privacy(ephemeral_text)

        assert was_filtered is True, "Privacy filter should have removed the emotional state line"
        assert "frustrated" not in filtered, "Emotional state word should be absent"
        assert "Emma seemed frustrated" not in filtered

        # Technical lines should survive
        assert "pytest" in filtered
        assert "black" in filtered
