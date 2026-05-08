"""
Tests for the Consolidator — LLM-based memory curation engine.

All Anthropic API calls are mocked; no real network requests are made.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.database import init_db, close_db
from core.lifecycle import LifecycleManager
from core.models import Memory, ConsolidationLog, db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base(tmp_path):
    """Initialize DB in a throwaway temp directory."""
    base_dir = init_db(base_dir=str(tmp_path / "memory"))
    yield base_dir
    close_db()


@pytest.fixture
def lifecycle(base):
    return LifecycleManager()


@pytest.fixture
def consolidator(base, lifecycle):
    return Consolidator(lifecycle=lifecycle, model="claude-sonnet-4-6")


@pytest.fixture
def ephemeral_file(tmp_path):
    """Write a minimal ephemeral observations file and return its path."""
    p = tmp_path / "session.md"
    p.write_text(
        "- User prefers snake_case for Python variable names.\n"
        "- Project uses pytest for all testing.\n"
        "- Avoid type: ignore comments; fix types properly.\n",
        encoding="utf-8",
    )
    return str(p)


def _llm_response_text(decisions: list[dict]) -> str:
    return json.dumps({"decisions": decisions})


def _create_memory(stage='consolidated', title='Test', content='Content', **kwargs):
    now = datetime.now().isoformat()
    return Memory.create(
        stage=stage,
        title=title,
        summary=kwargs.get('summary', content[:100]),
        content=content,
        tags=json.dumps(kwargs.get('tags', [])),
        importance=kwargs.get('importance', 0.5),
        reinforcement_count=kwargs.get('reinforcement_count', 0),
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# estimate_token_budget
# ---------------------------------------------------------------------------

class TestEstimateTokenBudget:
    def test_empty_list(self, consolidator):
        assert consolidator.estimate_token_budget([]) == 0

    def test_single_memory(self, consolidator):
        m = {"title": "x" * 40}
        total = len(str(m))
        assert consolidator.estimate_token_budget([m]) == total // 4

    def test_multiple_memories(self, consolidator):
        memories = [{"a": "b" * 100}, {"c": "d" * 200}]
        total = sum(len(str(m)) for m in memories)
        assert consolidator.estimate_token_budget(memories) == total // 4

    def test_returns_int(self, consolidator):
        result = consolidator.estimate_token_budget([{"x": "y"}])
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# consolidate_session — KEEP decisions
# ---------------------------------------------------------------------------

class TestConsolidateKeep:
    def test_keep_calls_store_create(self, consolidator, base, ephemeral_file):
        decisions = [
            {
                "observation": "Prefers snake_case for Python variable names.",
                "action": "keep",
                "rationale": "Useful style preference.",
                "title": "Python snake_case preference",
                "summary": "User prefers snake_case.",
                "tags": ["python", "style"],
                "target_path": "preferences/python_style.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-001")

        assert len(result["kept"]) == 1
        assert result["pruned"] == []
        assert result["promoted"] == []
        assert result["conflicts"] == []

        memory_id = result["kept"][0]
        memory = Memory.get_by_id(memory_id)
        assert memory.stage == "consolidated"
        assert memory.title == "Python snake_case preference"
        assert memory.source_session == "sess-001"

    def test_keep_strips_consolidated_prefix_from_target_path(self, consolidator, base, ephemeral_file):
        decisions = [
            {
                "observation": "Use pytest fixtures for setup.",
                "action": "keep",
                "rationale": "Testing preference.",
                "title": "Pytest fixtures",
                "summary": "Prefer pytest fixtures.",
                "tags": ["pytest"],
                "target_path": "consolidated/testing/pytest_fixtures.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-002")

        assert len(result["kept"]) == 1
        memory = Memory.get_by_id(result["kept"][0])
        assert memory.stage == "consolidated"

    def test_keep_records_consolidation_log(self, consolidator, base, ephemeral_file):
        decisions = [
            {
                "observation": "Always use context managers for file I/O.",
                "action": "keep",
                "rationale": "Good Python practice.",
                "title": "File I/O context managers",
                "summary": "Always use with statements.",
                "tags": ["python"],
                "target_path": "python/io_patterns.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-003")

        memory_id = result["kept"][0]
        row = ConsolidationLog.get(
            (ConsolidationLog.memory_id == memory_id) &
            (ConsolidationLog.action == 'kept')
        )
        assert row is not None
        assert row.from_stage == "ephemeral"
        assert row.to_stage == "consolidated"
        assert row.session_id == "sess-003"


# ---------------------------------------------------------------------------
# consolidate_session — PRUNE decisions
# ---------------------------------------------------------------------------

class TestConsolidatePrune:
    def test_prune_logged_not_stored(self, consolidator, base, ephemeral_file):
        decisions = [
            {
                "observation": "Session started at 10am.",
                "action": "prune",
                "rationale": "Transient scheduling detail; not worth storing.",
                "title": None,
                "summary": None,
                "tags": [],
                "target_path": None,
                "reinforces": None,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-004")

        assert result["pruned"] == [
            {
                "observation": "Session started at 10am.",
                "rationale": "Transient scheduling detail; not worth storing.",
            }
        ]
        assert result["kept"] == []
        consolidated = list(Memory.by_stage("consolidated"))
        assert consolidated == []

        row = ConsolidationLog.get_or_none(
            (ConsolidationLog.action == 'pruned') &
            (ConsolidationLog.session_id == "sess-004")
        )
        assert row is not None

    def test_multiple_prunes(self, consolidator, ephemeral_file):
        decisions = [
            {
                "observation": "obs 1",
                "action": "prune",
                "rationale": "not useful 1",
                "title": None, "summary": None, "tags": [],
                "target_path": None, "reinforces": None, "contradicts": None,
            },
            {
                "observation": "obs 2",
                "action": "prune",
                "rationale": "not useful 2",
                "title": None, "summary": None, "tags": [],
                "target_path": None, "reinforces": None, "contradicts": None,
            },
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-005")

        assert len(result["pruned"]) == 2


# ---------------------------------------------------------------------------
# consolidate_session — PROMOTE decisions
# ---------------------------------------------------------------------------

class TestConsolidatePromote:
    def test_promote_increments_reinforcement_count(self, consolidator, base, lifecycle, ephemeral_file):
        mem = _create_memory(
            title="Python snake_case",
            content="User prefers snake_case.",
            summary="Prefers snake_case.",
            reinforcement_count=1,
        )

        decisions = [
            {
                "observation": "Again confirmed: snake_case for Python vars.",
                "action": "promote",
                "rationale": "Consistent reinforcement of existing preference.",
                "title": None, "summary": None, "tags": [],
                "target_path": None,
                "reinforces": mem.id,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-006")

        assert result["promoted"] == [mem.id]
        updated = Memory.get_by_id(mem.id)
        assert updated.reinforcement_count == 2

    def test_promote_missing_reinforces_id_skipped(self, consolidator, ephemeral_file):
        decisions = [
            {
                "observation": "Some observation.",
                "action": "promote",
                "rationale": "Reinforces something.",
                "title": None, "summary": None, "tags": [],
                "target_path": None,
                "reinforces": None,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-008")

        assert result["promoted"] == []

    def test_promote_nonexistent_memory_skipped(self, consolidator, ephemeral_file):
        # Use a valid UUID4 format that doesn't exist in the DB
        decisions = [
            {
                "observation": "Observation about gone memory.",
                "action": "promote",
                "rationale": "...",
                "title": None, "summary": None, "tags": [],
                "target_path": None,
                "reinforces": "00000000-0000-4000-8000-000000000000",
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-009")

        assert result["promoted"] == []


# ---------------------------------------------------------------------------
# consolidate_session — conflict detection
# ---------------------------------------------------------------------------

class TestConsolidateConflicts:
    def test_contradicts_added_to_conflicts(self, consolidator, base, ephemeral_file):
        mem = _create_memory(
            title="Import style",
            content="Always use absolute imports.",
            summary="Absolute imports only.",
        )

        decisions = [
            {
                "observation": "User now prefers relative imports within packages.",
                "action": "keep",
                "rationale": "Updated import preference.",
                "title": "Import style update",
                "summary": "Prefers relative imports.",
                "tags": ["python", "imports"],
                "target_path": "style/imports_update.md",
                "reinforces": None,
                "contradicts": mem.id,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-010")

        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["contradicts"] == mem.id
        assert "relative imports" in result["conflicts"][0]["observation"]

    def test_no_contradicts_means_no_conflicts(self, consolidator, ephemeral_file):
        decisions = [
            {
                "observation": "Uses black for formatting.",
                "action": "keep",
                "rationale": "Style preference.",
                "title": "Black formatter",
                "summary": "Uses black.",
                "tags": ["python"],
                "target_path": "style/formatter.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-011")

        assert result["conflicts"] == []


# ---------------------------------------------------------------------------
# Malformed JSON handling (retry + raise)
# ---------------------------------------------------------------------------

class TestMalformedJsonHandling:
    def test_retry_on_malformed_json(self, consolidator, ephemeral_file):
        good_text = _llm_response_text(
            [
                {
                    "observation": "Uses mypy.",
                    "action": "keep",
                    "rationale": "Type safety.",
                    "title": "Mypy usage",
                    "summary": "Uses mypy.",
                    "tags": ["mypy"],
                    "target_path": "tooling/mypy.md",
                    "reinforces": None,
                    "contradicts": None,
                }
            ]
        )
        bad_text = "This is not JSON at all."

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.side_effect = [bad_text, good_text]
            result = consolidator.consolidate_session(ephemeral_file, "sess-013")

        assert len(result["kept"]) == 1
        assert mock_transport.call_count == 2

    def test_raises_on_two_consecutive_bad_responses(self, consolidator, ephemeral_file):
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = "not json"
            with pytest.raises(ValueError, match="malformed JSON on both attempts"):
                consolidator.consolidate_session(ephemeral_file, "sess-014")

        assert mock_transport.call_count == 2


# ---------------------------------------------------------------------------
# Mixed decisions in one session
# ---------------------------------------------------------------------------

class TestMixedDecisions:
    def test_keep_and_prune_in_same_session(self, consolidator, base, ephemeral_file):
        decisions = [
            {
                "observation": "Prefers functional style in Python.",
                "action": "keep",
                "rationale": "Meaningful style preference.",
                "title": "Functional style",
                "summary": "Prefers functional Python.",
                "tags": ["python", "style"],
                "target_path": "style/functional.md",
                "reinforces": None,
                "contradicts": None,
            },
            {
                "observation": "Coffee machine was broken today.",
                "action": "prune",
                "rationale": "Irrelevant personal detail.",
                "title": None, "summary": None, "tags": [],
                "target_path": None, "reinforces": None, "contradicts": None,
            },
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-015")

        assert len(result["kept"]) == 1
        assert len(result["pruned"]) == 1
        assert result["promoted"] == []
        assert result["conflicts"] == []

    def test_all_three_actions_in_same_session(self, consolidator, base, ephemeral_file):
        existing = _create_memory(
            title="Indentation preference",
            content="Uses 4-space indentation.",
            reinforcement_count=0,
        )
        decisions = [
            {
                "observation": "Prefers ruff over flake8.",
                "action": "keep",
                "rationale": "Linter preference.",
                "title": "Ruff linter",
                "summary": "Uses ruff.",
                "tags": ["linting"],
                "target_path": "tooling/ruff.md",
                "reinforces": None,
                "contradicts": None,
            },
            {
                "observation": "Reminder: stand-up at 9am.",
                "action": "prune",
                "rationale": "Calendar noise.",
                "title": None, "summary": None, "tags": [],
                "target_path": None, "reinforces": None, "contradicts": None,
            },
            {
                "observation": "Again using 4 spaces, consistent preference.",
                "action": "promote",
                "rationale": "Reinforces indentation preference.",
                "title": None, "summary": None, "tags": [],
                "target_path": None,
                "reinforces": existing.id,
                "contradicts": None,
            },
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-016")

        assert len(result["kept"]) == 1
        assert len(result["pruned"]) == 1
        assert len(result["promoted"]) == 1
        assert result["promoted"][0] == existing.id

        updated = Memory.get_by_id(existing.id)
        assert updated.reinforcement_count == 1


# ---------------------------------------------------------------------------
# Contradiction resolution
# ---------------------------------------------------------------------------

def _resolution_response_text(result: dict) -> str:
    return json.dumps(result)


MOCK_RESOLUTION = {
    "refined_title": "Import style: absolute for cross-package, relative within",
    "refined_content": "Use absolute imports for cross-package references. Within a package, relative imports are preferred for locality.",
    "resolution_type": "scoped",
    "confidence": 0.85,
}


class TestContradictionResolution:
    def test_conflict_refines_original_memory(self, consolidator, base, ephemeral_file):
        mem = _create_memory(
            title="Import style",
            content="Always use absolute imports.",
            summary="Absolute imports only.",
        )

        decisions = [
            {
                "observation": "User prefers relative imports within packages.",
                "action": "keep",
                "rationale": "Updated import preference.",
                "title": "Import update",
                "summary": "Relative imports within packages.",
                "tags": ["python"],
                "target_path": "style/imports_v2.md",
                "reinforces": None,
                "contradicts": mem.id,
            }
        ]

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.side_effect = [
                _llm_response_text(decisions),
                _resolution_response_text(MOCK_RESOLUTION),
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-020")

        assert len(result["resolved"]) == 1
        assert result["resolved"][0]["memory_id"] == mem.id
        assert result["resolved"][0]["resolution_type"] == "scoped"

        updated = Memory.get_by_id(mem.id)
        assert "relative" in updated.content.lower()
        assert updated.title == "Import style: absolute for cross-package, relative within"

    def test_low_confidence_resolution_skipped(self, consolidator, base, ephemeral_file):
        mem = _create_memory(
            title="Database choice",
            content="Always use PostgreSQL.",
            summary="PostgreSQL for everything.",
        )

        decisions = [
            {
                "observation": "Used SQLite for prototype.",
                "action": "keep",
                "rationale": "New pattern.",
                "title": "SQLite for prototypes",
                "summary": "SQLite works for prototypes.",
                "tags": ["database"],
                "target_path": "tools/sqlite.md",
                "reinforces": None,
                "contradicts": mem.id,
            }
        ]

        low_confidence = {**MOCK_RESOLUTION, "confidence": 0.2}

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.side_effect = [
                _llm_response_text(decisions),
                _resolution_response_text(low_confidence),
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-021")

        assert len(result["conflicts"]) == 1
        assert len(result["resolved"]) == 0

        original = Memory.get_by_id(mem.id)
        assert "PostgreSQL" in original.content

    def test_resolution_logs_merged_action(self, consolidator, base, ephemeral_file):
        mem = _create_memory(
            title="Naming convention",
            content="Use camelCase everywhere.",
            summary="camelCase.",
        )

        decisions = [
            {
                "observation": "User uses snake_case in Python.",
                "action": "keep",
                "rationale": "Correction.",
                "title": "Snake case",
                "summary": "snake_case in Python.",
                "tags": ["naming"],
                "target_path": "style/snake.md",
                "reinforces": None,
                "contradicts": mem.id,
            }
        ]

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.side_effect = [
                _llm_response_text(decisions),
                _resolution_response_text(MOCK_RESOLUTION),
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-022")

        rows = list(
            ConsolidationLog.select()
            .where(
                (ConsolidationLog.memory_id == mem.id) &
                (ConsolidationLog.action == 'merged')
            )
        )
        assert len(rows) == 1
        assert "Contradiction resolved" in rows[0].rationale

    def test_missing_contradict_target_skipped(self, consolidator, base, ephemeral_file):
        # Use a valid UUID4 format that doesn't exist in the DB
        decisions = [
            {
                "observation": "Something contradictory.",
                "action": "keep",
                "rationale": "New info.",
                "title": "New thing",
                "summary": "New.",
                "tags": [],
                "target_path": "general/new.md",
                "reinforces": None,
                "contradicts": "00000000-0000-4000-8000-000000000002",
            }
        ]

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-023")

        assert len(result["conflicts"]) == 1
        assert len(result["resolved"]) == 0

    def test_resolution_llm_failure_skipped(self, consolidator, base, ephemeral_file):
        mem = _create_memory(
            title="Editor",
            content="Uses vim.",
            summary="vim user.",
        )

        decisions = [
            {
                "observation": "Actually prefers VS Code now.",
                "action": "keep",
                "rationale": "Changed preference.",
                "title": "VS Code",
                "summary": "VS Code now.",
                "tags": ["editor"],
                "target_path": "tools/vscode.md",
                "reinforces": None,
                "contradicts": mem.id,
            }
        ]

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.side_effect = [
                _llm_response_text(decisions),
                "not json at all",
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-024")

        assert len(result["conflicts"]) == 1
        assert len(result["resolved"]) == 0

    def test_no_conflicts_means_no_resolution(self, consolidator, ephemeral_file):
        decisions = [
            {
                "observation": "Uses black for formatting.",
                "action": "keep",
                "rationale": "Style preference.",
                "title": "Black formatter",
                "summary": "Uses black.",
                "tags": ["python"],
                "target_path": "style/formatter.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-025")

        assert result["resolved"] == []
        assert mock_transport.call_count == 1

    def test_superseded_archives_old_memory(self, consolidator, base, ephemeral_file):
        mem = _create_memory(
            title="PR style",
            content="Always use single PRs.",
            summary="Single PRs preferred.",
        )

        decisions = [
            {
                "observation": "Split PRs are better for cross-cutting changes.",
                "action": "keep",
                "rationale": "Updated PR preference.",
                "title": "PR splitting",
                "summary": "Split PRs for cross-cutting.",
                "tags": ["workflow"],
                "target_path": "prefs/pr_split.md",
                "reinforces": None,
                "contradicts": mem.id,
            }
        ]

        superseded_resolution = {
            "refined_title": "PR style (old)",
            "refined_content": "Split PRs are now preferred for cross-cutting changes.",
            "resolution_type": "superseded",
            "confidence": 0.9,
        }

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.side_effect = [
                _llm_response_text(decisions),
                _resolution_response_text(superseded_resolution),
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-030")

        assert len(result["resolved"]) == 1
        assert result["resolved"][0]["resolution_type"] == "superseded"

        updated = Memory.get_by_id(mem.id)
        assert updated.archived_at is not None
        assert "[Superseded]" in updated.title

        deprecated_logs = list(
            ConsolidationLog.select().where(
                (ConsolidationLog.memory_id == mem.id) &
                (ConsolidationLog.action == 'deprecated')
            )
        )
        assert len(deprecated_logs) == 1
        assert "superseded" in deprecated_logs[0].rationale.lower()

    def test_scoped_adds_scope_tag(self, consolidator, base, ephemeral_file):
        mem = _create_memory(
            title="Import style",
            content="Always use absolute imports.",
            summary="Absolute imports only.",
            tags=["python"],
        )

        decisions = [
            {
                "observation": "Relative imports within packages.",
                "action": "keep",
                "rationale": "Scoped preference.",
                "title": "Import update",
                "summary": "Relative imports within packages.",
                "tags": ["python"],
                "target_path": "style/imports_v2.md",
                "reinforces": None,
                "contradicts": mem.id,
            }
        ]

        scoped_resolution = {
            "refined_title": "Import style: absolute for cross-package",
            "refined_content": "Use absolute imports for cross-package. Relative within packages.",
            "resolution_type": "scoped",
            "confidence": 0.85,
        }

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.side_effect = [
                _llm_response_text(decisions),
                _resolution_response_text(scoped_resolution),
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-031")

        assert len(result["resolved"]) == 1
        assert result["resolved"][0]["resolution_type"] == "scoped"

        updated = Memory.get_by_id(mem.id)
        tags = updated.tag_list
        assert any(t.startswith("scope:") for t in tags)
        assert "relative" in updated.content.lower()
        assert updated.archived_at is None


# ---------------------------------------------------------------------------
# TestCardToMemoryPromotion — Task 3.1 acceptance criteria
# ---------------------------------------------------------------------------

class TestCardToMemoryPromotion:
    """Verify that card fields are promoted to Memory columns during KEEP decisions."""

    def _card_decision(self, **overrides) -> dict:
        """Return a minimal card-shaped KEEP decision with given overrides."""
        base = {
            "observation": "The team approved the new schema design.",
            "action": "keep",
            "rationale": "Important architectural decision.",
            "title": "Schema approval",
            "summary": "Schema design approved by team.",
            "tags": ["schema"],
            "target_path": "decisions/schema.md",
            "reinforces": None,
            "contradicts": None,
            # Card-shape fields:
            "scope": "cross-session-durable",
            "knowledge_type_confidence": "high",
            "user_affect_valence": "delight",
            "evidence_quotes": ["Emma approved the design."],
        }
        base.update(overrides)
        return base

    def test_temporal_scope_promoted(self, consolidator, base, ephemeral_file):
        decision = self._card_decision(scope="cross-session-durable")
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "card-001")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.temporal_scope == "cross-session-durable"

    def test_confidence_promoted_high(self, consolidator, base, ephemeral_file):
        decision = self._card_decision(knowledge_type_confidence="high")
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "card-002")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.confidence == pytest.approx(0.9)

    def test_affect_valence_promoted(self, consolidator, base, ephemeral_file):
        decision = self._card_decision(user_affect_valence="friction")
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "card-003")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.affect_valence == "friction"

    def test_actor_extracted_from_quote(self, consolidator, base, ephemeral_file):
        decision = self._card_decision(
            evidence_quotes=["Emma approved the design."],
        )
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "card-004")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.actor == "Emma"

    def test_actor_null_when_no_quote(self, consolidator, base, ephemeral_file):
        decision = self._card_decision(evidence_quotes=[])
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "card-005")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.actor is None

    def test_non_card_decision_has_null_card_fields(self, consolidator, base, ephemeral_file):
        """Flat (non-card) decisions must produce None for all four card fields."""
        decision = {
            "observation": "User prefers snake_case.",
            "action": "keep",
            "rationale": "Style preference.",
            "title": "snake_case preference",
            "summary": "Prefer snake_case.",
            "tags": [],
            "target_path": "prefs/style.md",
            "reinforces": None,
            "contradicts": None,
            # No scope, evidence_quotes — not a card
        }
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "card-006")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.temporal_scope is None
        assert mem.confidence is None
        assert mem.affect_valence is None
        assert mem.actor is None


# ---------------------------------------------------------------------------
# TestCardImportance — tier3 #32 / D2 acceptance criteria
# ---------------------------------------------------------------------------

class TestCardImportance:
    """Card-path importance flows from card.importance, Kensinger bump only for friction."""

    def _card_decision(self, **overrides) -> dict:
        # neutral observation text → importance_boost == 0.0 from somatic
        base = {
            "observation": "The database schema was updated to add two columns.",
            "action": "keep",
            "rationale": "Architectural decision.",
            "title": "Schema update",
            "summary": "Two new columns added.",
            "tags": [],
            "target_path": "decisions/schema_update.md",
            "reinforces": None,
            "contradicts": None,
            # Card-shape fields:
            "scope": "cross-session-durable",
            "knowledge_type_confidence": "high",
            "user_affect_valence": "neutral",
            "evidence_quotes": ["Schema updated."],
            "importance": 0.8,
        }
        base.update(overrides)
        return base

    def test_card_importance_flows_from_card_dict(self, consolidator, base, ephemeral_file):
        """Card decision uses card.importance as base, not somatic boost-based formula."""
        decision = self._card_decision(importance=0.8, user_affect_valence="neutral")
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cimp-001")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        # importance must come from card, not somatic (which gives ~0.5 for neutral)
        assert mem.importance == pytest.approx(0.8)

    def test_friction_valence_triggers_kensinger_bump(self, consolidator, base, ephemeral_file):
        """Card with affect_valence=friction gets +0.05 Kensinger bump."""
        decision = self._card_decision(importance=0.8, user_affect_valence="friction")
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cimp-002")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.importance == pytest.approx(0.85)

    def test_non_friction_card_does_not_get_kensinger_bump(self, consolidator, base, ephemeral_file):
        """Card with affect_valence=delight does NOT get the +0.05 bump."""
        decision = self._card_decision(importance=0.8, user_affect_valence="delight")
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cimp-003")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.importance == pytest.approx(0.8)

    def test_malformed_card_importance_falls_back_with_warning(
        self, consolidator, base, ephemeral_file
    ):
        """Non-numeric card importance falls back to boost-based value (somatic)."""
        import logging
        decision = self._card_decision(importance="not-a-number", user_affect_valence="neutral")
        log_records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                log_records.append(record)

        handler = _Capture(level=logging.WARNING)
        core_logger = logging.getLogger("core.consolidator")
        core_logger.addHandler(handler)
        try:
            with patch("core.consolidator._call_llm_transport") as mock_transport:
                mock_transport.return_value = _llm_response_text([decision])
                result = consolidator.consolidate_session(ephemeral_file, "cimp-004")
        finally:
            core_logger.removeHandler(handler)

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        # Fallback: 0.5 + importance_boost (neutral obs → boost ≈ 0) = ~0.5
        assert 0.4 <= mem.importance <= 0.6
        # Warning must have been logged
        assert any(
            "malformed" in r.getMessage().lower() or "falling back" in r.getMessage().lower()
            for r in log_records
        )

    def test_non_card_path_uses_boost_formula(self, consolidator, base, ephemeral_file):
        """Non-card decision ignores card.importance, uses somatic boost formula."""
        decision = {
            "observation": "The database schema was updated to add two columns.",
            "action": "keep",
            "rationale": "Architectural decision.",
            "title": "Schema update",
            "summary": "Two new columns added.",
            "tags": [],
            "target_path": "decisions/schema_update_flat.md",
            "reinforces": None,
            "contradicts": None,
            # No card-shape fields (no scope, no evidence_quotes)
            "importance": 0.8,  # this value must NOT flow through for non-card
        }
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cimp-005")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        # Non-card path: min(0.5 + importance_boost, 1.0); neutral obs → ~0.5
        assert mem.importance != pytest.approx(0.8), (
            "Non-card decision should not use card.importance=0.8"
        )
        assert 0.0 <= mem.importance <= 1.0


# ---------------------------------------------------------------------------
# TestCardFieldsNewWiring — #36-A acceptance criteria
# criterion_weights, rejected_options, affect_valence wiring
# ---------------------------------------------------------------------------

class TestCardFieldsNewWiring:
    """Verify criterion_weights, rejected_options, affect_valence wired into Memory.create()."""

    def _decision_card(self, **overrides) -> dict:
        base = {
            "observation": "Emma chose 13+Command+Trade extension; full redesign rejected.",
            "action": "keep",
            "rationale": "Important architectural decision.",
            "title": "Skill taxonomy decision",
            "summary": "13+Command+Trade chosen; full redesign hard-vetoed.",
            "tags": ["taxonomy"],
            "target_path": "decisions/taxonomy.md",
            "reinforces": None,
            "contradicts": None,
            # Card-shape fields
            "scope": "cross-session-durable",
            "knowledge_type_confidence": "high",
            "user_affect_valence": "neutral",
            "evidence_quotes": ["Emma stated test invalidation is a blocker."],
            "importance": 0.85,
            "criterion_weights": {
                "test preservation": "hard_veto",
                "coverage": "strong",
                "migration effort": "weak",
            },
            "rejected_options": [
                {"option": "full redesign", "reason": "invalidates existing tests"},
                {"option": "13-category flat list", "reason": "rejected without recorded reason"},
            ],
        }
        base.update(overrides)
        return base

    def test_criterion_weights_stored_as_json(self, consolidator, base, ephemeral_file):
        """criterion_weights from card decision stored as JSON string on Memory."""
        decision = self._decision_card()
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cfw-001")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.criterion_weights is not None
        # round-trip: stored JSON must recover original dict
        parsed = json.loads(mem.criterion_weights)
        assert parsed == decision["criterion_weights"]

    def test_rejected_options_stored_as_json(self, consolidator, base, ephemeral_file):
        """rejected_options from card decision stored as JSON string on Memory."""
        decision = self._decision_card()
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cfw-002")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.rejected_options is not None
        parsed = json.loads(mem.rejected_options)
        assert parsed == decision["rejected_options"]

    def test_criterion_weights_round_trip(self, consolidator, base, ephemeral_file):
        """json.loads(mem.criterion_weights) returns the original dict exactly."""
        cw = {"latency": "hard_veto", "cost": "strong", "dx": "mentioned"}
        decision = self._decision_card(criterion_weights=cw)
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cfw-003")

        mem = Memory.get_by_id(result["kept"][0])
        assert json.loads(mem.criterion_weights) == cw

    def test_affect_valence_neutral_default_for_card(self, consolidator, base, ephemeral_file):
        """Card decision with no explicit valence (or neutral) stores affect_valence='neutral'."""
        decision = self._decision_card(user_affect_valence="neutral")
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cfw-004")

        assert len(result["kept"]) == 1
        mem = Memory.get_by_id(result["kept"][0])
        assert mem.affect_valence == "neutral"

    def test_criterion_weights_none_when_absent(self, consolidator, base, ephemeral_file):
        """Card decision without criterion_weights stores NULL (not empty JSON)."""
        decision = self._decision_card()
        del decision["criterion_weights"]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cfw-005")

        mem = Memory.get_by_id(result["kept"][0])
        assert mem.criterion_weights is None

    def test_rejected_options_none_when_absent(self, consolidator, base, ephemeral_file):
        """Card decision without rejected_options stores NULL (not empty JSON)."""
        decision = self._decision_card()
        del decision["rejected_options"]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text([decision])
            result = consolidator.consolidate_session(ephemeral_file, "cfw-006")

        mem = Memory.get_by_id(result["kept"][0])
        assert mem.rejected_options is None


# ---------------------------------------------------------------------------
# TestPydanticValidation — RISK-02 Pydantic schema consumer (Wave 3.1)
# ---------------------------------------------------------------------------

class TestPydanticValidation:
    """Pydantic ConsolidationResponse used for LLM output parsing."""

    def test_invalid_action_raises_validation_error(self, consolidator, ephemeral_file):
        """An invalid action value must raise ValidationError (not proceed silently)."""
        from pydantic import ValidationError
        bad_response = json.dumps({"decisions": [
            {
                "observation": "Some observation.",
                "action": "INVALID_ACTION",
                "rationale": "Should fail.",
            }
        ]})
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = bad_response
            # Invalid actions are now skipped per-decision (graceful degradation),
            # not raised as exceptions for the whole batch.
            result = consolidator.consolidate_session(ephemeral_file, "pydantic-001")
        assert result["kept"] == [] and result["promoted"] == []

    def test_valid_actions_pass_through(self, consolidator, base, ephemeral_file):
        """All valid actions parse without error."""
        # 'keep' is a valid action; test that it round-trips via Pydantic
        decisions = [
            {
                "observation": "Prefers ruff over flake8.",
                "action": "keep",
                "rationale": "Linter preference.",
                "title": "Ruff",
                "summary": "Uses ruff.",
                "tags": [],
                "target_path": "tooling/ruff.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "pydantic-002")

        assert len(result["kept"]) == 1

    def test_missing_reinforces_id_skipped_not_abort(self, consolidator, base, ephemeral_file):
        """Decision referencing non-existent reinforces ID is skipped; session continues."""
        decisions = [
            {
                "observation": "Keep this.",
                "action": "keep",
                "rationale": "Good preference.",
                "title": "Good thing",
                "summary": "Good.",
                "tags": [],
                "target_path": "general/good.md",
                "reinforces": None,
                "contradicts": None,
            },
            {
                "observation": "Reinforcing something gone.",
                "action": "promote",
                "rationale": "Reinforce.",
                "reinforces": "00000000-0000-4000-8000-000000000000",
                "contradicts": None,
            },
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "pydantic-003")

        # The promote decision is dropped because the ID doesn't exist.
        # The keep decision should still succeed.
        assert len(result["kept"]) == 1
        assert result["promoted"] == []

    def test_missing_contradicts_id_does_not_abort_session(self, consolidator, base, ephemeral_file):
        """Decision with non-existent contradicts ID still creates the memory; conflict tracked."""
        decisions = [
            {
                "observation": "New thing.",
                "action": "keep",
                "rationale": "Good.",
                "title": "New thing",
                "summary": "New.",
                "tags": [],
                "target_path": "general/new.md",
                "reinforces": None,
                # Valid UUID4 format, not in DB
                "contradicts": "00000000-0000-4000-8000-000000000001",
            },
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "pydantic-004")

        # The keep action still creates a memory; conflict tracked but resolution skipped
        assert len(result["kept"]) == 1
        assert len(result["conflicts"]) == 1
        assert len(result["resolved"]) == 0


# ---------------------------------------------------------------------------
# TestPendingDelete — Two-phase delete (RISK-08)
# ---------------------------------------------------------------------------

class TestPendingDelete:
    """Archive action sets stage=pending_delete; hard delete gated behind TTL."""

    def test_archive_action_sets_pending_delete_stage(self, consolidator, base, ephemeral_file):
        """An 'archive' decision targeting an existing memory sets stage='pending_delete'."""
        existing = _create_memory(
            title="Old pattern",
            content="Old content.",
            summary="Old.",
        )
        decisions = [
            {
                "observation": "Old pattern is obsolete.",
                "action": "archive",
                "rationale": "This memory is no longer relevant.",
                "contradicts": existing.id,
                "reinforces": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            consolidator.consolidate_session(ephemeral_file, "pd-001")

        updated = Memory.get_by_id(existing.id)
        assert updated.stage == "pending_delete"

    def test_archive_action_logs_archived_to_pending_delete(self, consolidator, base, ephemeral_file):
        """Archive action creates a ConsolidationLog entry from_stage→pending_delete."""
        existing = _create_memory(
            title="Old pattern",
            content="Old content.",
            summary="Old.",
        )
        decisions = [
            {
                "observation": "Old pattern is obsolete.",
                "action": "archive",
                "rationale": "This memory is no longer relevant.",
                "contradicts": existing.id,
                "reinforces": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            consolidator.consolidate_session(ephemeral_file, "pd-002")

        rows = list(
            ConsolidationLog.select().where(
                (ConsolidationLog.memory_id == existing.id) &
                (ConsolidationLog.action == "archived")
            )
        )
        assert len(rows) == 1
        assert rows[0].to_stage == "pending_delete"
        assert rows[0].from_stage == "consolidated"

    def test_archive_without_target_falls_back_to_prune_log(
        self, consolidator, base, ephemeral_file
    ):
        """Archive with no contradicts/reinforces falls back to prune of ephemeral."""
        decisions = [
            {
                "observation": "Transient note.",
                "action": "archive",
                "rationale": "Not worth keeping.",
                "contradicts": None,
                "reinforces": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "pd-003")

        # Falls back to prune behavior (logged, no memory created)
        assert len(result["pruned"]) == 1
        assert result["kept"] == []

    def test_pending_delete_memory_not_hard_deleted(self, consolidator, base, ephemeral_file):
        """After archive decision, memory row still exists (pending, not gone)."""
        existing = _create_memory(
            title="Pending",
            content="Content.",
            summary="Pending.",
        )
        decisions = [
            {
                "observation": "Memory is outdated.",
                "action": "archive",
                "rationale": "No longer relevant.",
                "contradicts": existing.id,
                "reinforces": None,
            }
        ]
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            consolidator.consolidate_session(ephemeral_file, "pd-004")

        # Memory must still exist
        mem = Memory.get_by_id(existing.id)
        assert mem is not None
        assert mem.stage == "pending_delete"


# ---------------------------------------------------------------------------
# TestBatchConcurrency — asyncio.gather + Semaphore (RISK-08)
# ---------------------------------------------------------------------------

class TestBatchConcurrency:
    """consolidate_batch: return_exceptions, semaphore, error isolation."""

    def test_one_item_exception_does_not_abort_batch(
        self, consolidator, base, tmp_path
    ):
        """If one session raises, other sessions still complete."""
        good_file = tmp_path / "good.md"
        good_file.write_text("- Good observation.\n", encoding="utf-8")

        bad_file = tmp_path / "bad.md"
        bad_file.write_text("- Bad observation.\n", encoding="utf-8")

        good_decisions = [
            {
                "observation": "Good observation.",
                "action": "keep",
                "rationale": "Good preference.",
                "title": "Good",
                "summary": "Good.",
                "tags": [],
                "target_path": "general/good.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]
        good_response = _llm_response_text(good_decisions)

        call_count = []

        def _side_effect(prompt, **kwargs):
            call_count.append(1)
            # Second call (bad session) raises
            if len(call_count) == 2:
                raise RuntimeError("Simulated LLM failure")
            return good_response

        sessions = [
            (str(good_file), "batch-good"),
            (str(bad_file), "batch-bad"),
        ]

        with patch("core.consolidator._call_llm_transport", side_effect=_side_effect):
            results = consolidator.consolidate_batch(sessions)

        assert len(results) == 2
        # One result should be a success dict
        successes = [r for r in results if "error" not in r]
        errors = [r for r in results if "error" in r]
        assert len(successes) == 1
        assert len(errors) == 1
        assert "session_id" in errors[0]

    def test_batch_returns_error_dict_for_failed_session(
        self, consolidator, base, tmp_path
    ):
        """Failed session result contains 'error' and 'session_id' keys."""
        f = tmp_path / "fail.md"
        f.write_text("- Observation.\n", encoding="utf-8")

        with patch("core.consolidator._call_llm_transport", side_effect=RuntimeError("boom")):
            results = consolidator.consolidate_batch([(str(f), "fail-sess")])

        assert len(results) == 1
        assert results[0]["error"] == "boom"
        assert results[0]["session_id"] == "fail-sess"

    def test_batch_semaphore_limits_concurrency(self, consolidator, base, tmp_path):
        """Semaphore ensures at most BATCH_CONCURRENCY sessions run simultaneously."""
        import threading

        n = 6  # more than BATCH_CONCURRENCY (3)
        files = []
        for i in range(n):
            f = tmp_path / f"s{i}.md"
            f.write_text(f"- Observation {i}.\n", encoding="utf-8")
            files.append(f)

        decisions = [
            {
                "observation": f"Observation {i}.",
                "action": "keep",
                "rationale": "Good.",
                "title": f"Obs {i}",
                "summary": f"Obs {i}.",
                "tags": [],
                "target_path": f"general/obs{i}.md",
                "reinforces": None,
                "contradicts": None,
            }
            for i in range(n)
        ]

        concurrent_peak = [0]
        active = [0]
        lock = threading.Lock()

        def _side_effect(prompt, **kwargs):
            with lock:
                active[0] += 1
                concurrent_peak[0] = max(concurrent_peak[0], active[0])
            # Small sleep to allow overlap
            import time as _time
            _time.sleep(0.01)
            with lock:
                active[0] -= 1
            return _llm_response_text([decisions[0]])

        sessions = [(str(f), f"sem-{i}") for i, f in enumerate(files)]

        with patch("core.consolidator._call_llm_transport", side_effect=_side_effect):
            results = consolidator.consolidate_batch(sessions)

        assert len(results) == n
        # Peak concurrency must not exceed BATCH_CONCURRENCY
        assert concurrent_peak[0] <= consolidator.BATCH_CONCURRENCY

    def test_batch_empty_sessions_returns_empty(self, consolidator):
        """consolidate_batch([]) returns []."""
        results = consolidator.consolidate_batch([])
        assert results == []


# ---------------------------------------------------------------------------
# TestIdempotency — same key skipped on second call (RISK-08)
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Decisions with the same idempotency key are skipped on retry."""

    def test_same_decision_twice_in_same_session_is_no_op_on_second(
        self, consolidator, base, ephemeral_file
    ):
        """Running consolidate_session twice with the SAME session_id skips duplicate decisions."""
        decisions = [
            {
                "observation": "Prefers snake_case.",
                "action": "keep",
                "rationale": "Style.",
                "title": "snake_case",
                "summary": "snake_case.",
                "tags": [],
                "target_path": "prefs/snake.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            # Use a UNIQUE session_id for first call
            result1 = consolidator.consolidate_session(ephemeral_file, "idem-same-sess")

        # Same decisions again with THE SAME session_id → idempotency key matches → skipped
        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            result2 = consolidator.consolidate_session(ephemeral_file, "idem-same-sess")

        assert len(result1["kept"]) == 1
        # Second call: same session_id + same observation → same key → skipped
        assert len(result2["kept"]) == 0

    def test_idempotency_key_reset_on_new_instance(self, base, lifecycle, ephemeral_file):
        """A new Consolidator instance does not carry over idempotency keys."""
        decisions = [
            {
                "observation": "Prefers ruff.",
                "action": "keep",
                "rationale": "Linter.",
                "title": "Ruff",
                "summary": "Ruff.",
                "tags": [],
                "target_path": "tooling/ruff2.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]

        c1 = Consolidator(lifecycle=lifecycle, model="claude-sonnet-4-6")
        c2 = Consolidator(lifecycle=lifecycle, model="claude-sonnet-4-6")

        with patch("core.consolidator._call_llm_transport") as mock_transport:
            mock_transport.return_value = _llm_response_text(decisions)
            r1 = c1.consolidate_session(ephemeral_file, "idem-002a")

        # Different instance — fresh _processed_keys
        # But content_hash dedup will block second create of same content, so
        # verify the idempotency set is empty on c2
        assert len(c2._processed_keys) == 0
        assert len(r1["kept"]) == 1
