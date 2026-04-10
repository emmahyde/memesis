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
        decisions = [
            {
                "observation": "Observation about gone memory.",
                "action": "promote",
                "rationale": "...",
                "title": None, "summary": None, "tags": [],
                "target_path": None,
                "reinforces": "nonexistent-uuid-1234",
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
                "contradicts": "nonexistent-uuid",
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
