"""
Tests for the Consolidator — LLM-based memory curation engine.

All Anthropic API calls are mocked; no real network requests are made.
"""

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure the memesis package root is on sys.path so that
# `from core.xxx import ...` works when running pytest directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.lifecycle import LifecycleManager
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    """MemoryStore backed by a throwaway temp directory."""
    return MemoryStore(base_dir=str(tmp_path / "memory"))


@pytest.fixture
def lifecycle(tmp_store):
    return LifecycleManager(tmp_store)


@pytest.fixture
def consolidator(tmp_store, lifecycle):
    return Consolidator(store=tmp_store, lifecycle=lifecycle, model="claude-sonnet-4-6")


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


def _llm_response(decisions: list[dict]) -> MagicMock:
    """Build a mock Anthropic messages.create() return value."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps({"decisions": decisions}))]
    return mock_msg


# ---------------------------------------------------------------------------
# filter_privacy
# ---------------------------------------------------------------------------

class TestFilterPrivacy:
    def test_blocks_seemed_frustrated(self, consolidator):
        text = "The user seemed frustrated with the build system.\n"
        filtered, was_filtered = consolidator.filter_privacy(text)
        assert was_filtered
        assert "frustrated" not in filtered

    def test_blocks_was_excited(self, consolidator):
        text = "Emma was excited about the new feature.\n"
        filtered, was_filtered = consolidator.filter_privacy(text)
        assert was_filtered
        assert "excited" not in filtered

    def test_blocks_mood_keyword(self, consolidator):
        text = "Current mood: productive.\n"
        filtered, was_filtered = consolidator.filter_privacy(text)
        assert was_filtered

    def test_blocks_is_stressed(self, consolidator):
        text = "Emma is stressed about the deadline.\n"
        filtered, was_filtered = consolidator.filter_privacy(text)
        assert was_filtered

    def test_allows_technical_observation(self, consolidator):
        text = "Prefers pytest over unittest for test discovery.\n"
        filtered, was_filtered = consolidator.filter_privacy(text)
        assert not was_filtered
        assert filtered == text

    def test_allows_domain_knowledge(self, consolidator):
        text = "SQLite WAL mode improves concurrent read safety.\n"
        filtered, was_filtered = consolidator.filter_privacy(text)
        assert not was_filtered

    def test_allows_correction(self, consolidator):
        text = "Correction: use Path.read_text() instead of open().\n"
        filtered, was_filtered = consolidator.filter_privacy(text)
        assert not was_filtered

    def test_multi_line_partial_filter(self, consolidator):
        text = (
            "Prefers snake_case.\n"
            "User seemed annoyed with verbose logging.\n"
            "Uses mypy for type checking.\n"
        )
        filtered, was_filtered = consolidator.filter_privacy(text)
        assert was_filtered
        assert "snake_case" in filtered
        assert "mypy" in filtered
        assert "annoyed" not in filtered

    def test_returns_tuple(self, consolidator):
        result = consolidator.filter_privacy("anything")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_no_filter_returns_false(self, consolidator):
        _, was_filtered = consolidator.filter_privacy("Clean technical note.\n")
        assert was_filtered is False


# ---------------------------------------------------------------------------
# estimate_token_budget
# ---------------------------------------------------------------------------

class TestEstimateTokenBudget:
    def test_empty_list(self, consolidator):
        assert consolidator.estimate_token_budget([]) == 0

    def test_single_memory(self, consolidator):
        # 40 chars → 10 tokens
        m = {"title": "x" * 40}
        # str(m) will be longer than 40 due to dict repr overhead;
        # just assert it's chars // 4
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
    def test_keep_calls_store_create(self, consolidator, tmp_store, ephemeral_file):
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
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-001")

        assert len(result["kept"]) == 1
        assert result["pruned"] == []
        assert result["promoted"] == []
        assert result["conflicts"] == []

        # Memory must exist in consolidated stage
        memory_id = result["kept"][0]
        memory = tmp_store.get(memory_id)
        assert memory["stage"] == "consolidated"
        assert memory["title"] == "Python snake_case preference"
        assert memory["source_session"] == "sess-001"

    def test_keep_strips_consolidated_prefix_from_target_path(self, consolidator, tmp_store, ephemeral_file):
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
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-002")

        assert len(result["kept"]) == 1
        memory = tmp_store.get(result["kept"][0])
        assert memory["stage"] == "consolidated"

    def test_keep_records_consolidation_log(self, consolidator, tmp_store, ephemeral_file):
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
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-003")

        memory_id = result["kept"][0]
        with sqlite3.connect(tmp_store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM consolidation_log WHERE memory_id = ? AND action = 'kept'",
                (memory_id,),
            ).fetchone()

        assert row is not None
        assert row["from_stage"] == "ephemeral"
        assert row["to_stage"] == "consolidated"
        assert row["session_id"] == "sess-003"


# ---------------------------------------------------------------------------
# consolidate_session — PRUNE decisions
# ---------------------------------------------------------------------------

class TestConsolidatePrune:
    def test_prune_logged_not_stored(self, consolidator, tmp_store, ephemeral_file):
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
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-004")

        assert result["pruned"] == [
            {
                "observation": "Session started at 10am.",
                "rationale": "Transient scheduling detail; not worth storing.",
            }
        ]
        assert result["kept"] == []

        # Confirm nothing landed in consolidated stage
        consolidated = tmp_store.list_by_stage("consolidated")
        assert consolidated == []

        # Confirm prune was logged
        with sqlite3.connect(tmp_store.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM consolidation_log WHERE action = 'pruned' AND session_id = ?",
                ("sess-004",),
            ).fetchone()
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
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-005")

        assert len(result["pruned"]) == 2


# ---------------------------------------------------------------------------
# consolidate_session — PROMOTE decisions
# ---------------------------------------------------------------------------

class TestConsolidatePromote:
    def test_promote_increments_reinforcement_count(self, consolidator, tmp_store, lifecycle, ephemeral_file):
        # Create an existing consolidated memory to reinforce
        memory_id = tmp_store.create(
            path="python/snake_case.md",
            content="User prefers snake_case.",
            metadata={
                "stage": "consolidated",
                "title": "Python snake_case",
                "summary": "Prefers snake_case.",
                "reinforcement_count": 1,
            },
        )

        decisions = [
            {
                "observation": "Again confirmed: snake_case for Python vars.",
                "action": "promote",
                "rationale": "Consistent reinforcement of existing preference.",
                "title": None,
                "summary": None,
                "tags": [],
                "target_path": None,
                "reinforces": memory_id,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-006")

        assert result["promoted"] == [memory_id]
        updated = tmp_store.get(memory_id)
        assert updated["reinforcement_count"] == 2

    def test_promote_calls_store_update(self, consolidator, tmp_store, ephemeral_file):
        memory_id = tmp_store.create(
            path="preferences/editor.md",
            content="Prefers VS Code.",
            metadata={
                "stage": "consolidated",
                "title": "Editor preference",
                "reinforcement_count": 0,
            },
        )

        decisions = [
            {
                "observation": "Uses VS Code with vim keybindings.",
                "action": "promote",
                "rationale": "Reinforces editor preference.",
                "title": None, "summary": None, "tags": [],
                "target_path": None,
                "reinforces": memory_id,
                "contradicts": None,
            }
        ]
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            with patch.object(tmp_store, "update", wraps=tmp_store.update) as mock_update:
                consolidator.consolidate_session(ephemeral_file, "sess-007")

        mock_update.assert_called_once_with(
            memory_id,
            metadata={"reinforcement_count": 1},
        )

    def test_promote_missing_reinforces_id_skipped(self, consolidator, ephemeral_file):
        decisions = [
            {
                "observation": "Some observation.",
                "action": "promote",
                "rationale": "Reinforces something.",
                "title": None, "summary": None, "tags": [],
                "target_path": None,
                "reinforces": None,  # missing
                "contradicts": None,
            }
        ]
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
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
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-009")

        assert result["promoted"] == []


# ---------------------------------------------------------------------------
# consolidate_session — conflict detection
# ---------------------------------------------------------------------------

class TestConsolidateConflicts:
    def test_contradicts_added_to_conflicts(self, consolidator, tmp_store, ephemeral_file):
        memory_id = tmp_store.create(
            path="style/imports.md",
            content="Always use absolute imports.",
            metadata={
                "stage": "consolidated",
                "title": "Import style",
                "summary": "Absolute imports only.",
            },
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
                "contradicts": memory_id,
            }
        ]
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-010")

        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["contradicts"] == memory_id
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
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-011")

        assert result["conflicts"] == []


# ---------------------------------------------------------------------------
# Privacy filtering applied before LLM call
# ---------------------------------------------------------------------------

class TestPrivacyFilterIntegration:
    def test_emotional_lines_removed_before_prompt(self, consolidator, tmp_path):
        """The LLM prompt must not contain emotional state language."""
        ephemeral = tmp_path / "obs.md"
        ephemeral.write_text(
            "User seemed frustrated with the CI pipeline.\n"
            "Prefers pytest for unit tests.\n",
            encoding="utf-8",
        )

        decisions = [
            {
                "observation": "Prefers pytest for unit tests.",
                "action": "keep",
                "rationale": "Testing preference.",
                "title": "Pytest preference",
                "summary": "Uses pytest.",
                "tags": ["pytest"],
                "target_path": "testing/pytest.md",
                "reinforces": None,
                "contradicts": None,
            }
        ]

        captured_prompt = []

        def capture_create(**kwargs):
            captured_prompt.append(kwargs["messages"][0]["content"])
            return _llm_response(decisions)

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = capture_create
            consolidator.consolidate_session(str(ephemeral), "sess-012")

        assert len(captured_prompt) == 1
        prompt_text = captured_prompt[0]
        assert "frustrated" not in prompt_text
        assert "Prefers pytest" in prompt_text


# ---------------------------------------------------------------------------
# Malformed JSON handling (retry + raise)
# ---------------------------------------------------------------------------

class TestMalformedJsonHandling:
    def test_retry_on_malformed_json(self, consolidator, ephemeral_file):
        """First call returns garbage; second call returns valid JSON."""
        good_response = _llm_response(
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
        bad_response = MagicMock()
        bad_response.content = [MagicMock(text="This is not JSON at all.")]

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = [bad_response, good_response]
            result = consolidator.consolidate_session(ephemeral_file, "sess-013")

        # Should have succeeded on second attempt
        assert len(result["kept"]) == 1
        assert mock_cls.return_value.messages.create.call_count == 2

    def test_raises_on_two_consecutive_bad_responses(self, consolidator, ephemeral_file):
        """Both attempts return garbage → ValueError raised."""
        bad_response = MagicMock()
        bad_response.content = [MagicMock(text="not json")]

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = bad_response
            with pytest.raises(ValueError, match="malformed JSON on both attempts"):
                consolidator.consolidate_session(ephemeral_file, "sess-014")

        assert mock_cls.return_value.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# Mixed decisions in one session
# ---------------------------------------------------------------------------

class TestMixedDecisions:
    def test_keep_and_prune_in_same_session(self, consolidator, tmp_store, ephemeral_file):
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
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-015")

        assert len(result["kept"]) == 1
        assert len(result["pruned"]) == 1
        assert result["promoted"] == []
        assert result["conflicts"] == []

    def test_all_three_actions_in_same_session(self, consolidator, tmp_store, ephemeral_file):
        existing_id = tmp_store.create(
            path="preferences/indent.md",
            content="Uses 4-space indentation.",
            metadata={
                "stage": "consolidated",
                "title": "Indentation preference",
                "reinforcement_count": 0,
            },
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
                "reinforces": existing_id,
                "contradicts": None,
            },
        ]
        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-016")

        assert len(result["kept"]) == 1
        assert len(result["pruned"]) == 1
        assert len(result["promoted"]) == 1
        assert result["promoted"][0] == existing_id

        # Reinforcement count incremented
        updated = tmp_store.get(existing_id)
        assert updated["reinforcement_count"] == 1


# ---------------------------------------------------------------------------
# Contradiction resolution
# ---------------------------------------------------------------------------

def _resolution_response(result: dict) -> MagicMock:
    """Build a mock Anthropic messages.create() return for resolution LLM."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps(result))]
    return mock_msg


MOCK_RESOLUTION = {
    "refined_title": "Import style: absolute for cross-package, relative within",
    "refined_content": "Use absolute imports for cross-package references. Within a package, relative imports are preferred for locality.",
    "resolution_type": "scoped",
    "confidence": 0.85,
}


class TestContradictionResolution:
    def test_conflict_refines_original_memory(self, consolidator, tmp_store, ephemeral_file):
        """When a conflict is detected, the original memory gets refined."""
        memory_id = tmp_store.create(
            path="style/imports.md",
            content="Always use absolute imports.",
            metadata={
                "stage": "consolidated",
                "title": "Import style",
                "summary": "Absolute imports only.",
            },
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
                "contradicts": memory_id,
            }
        ]

        # Two LLM calls: consolidation then resolution
        consolidation_response = _llm_response(decisions)
        resolution_response = _resolution_response(MOCK_RESOLUTION)

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = [
                consolidation_response,
                resolution_response,
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-020")

        assert len(result["resolved"]) == 1
        assert result["resolved"][0]["memory_id"] == memory_id
        assert result["resolved"][0]["resolution_type"] == "scoped"

        # Original memory content was updated
        updated = tmp_store.get(memory_id)
        assert "relative" in updated["content"].lower()
        assert updated["title"] == "Import style: absolute for cross-package, relative within"

    def test_low_confidence_resolution_skipped(self, consolidator, tmp_store, ephemeral_file):
        """Resolution with confidence < 0.4 is not applied."""
        memory_id = tmp_store.create(
            path="tools/db.md",
            content="Always use PostgreSQL.",
            metadata={
                "stage": "consolidated",
                "title": "Database choice",
                "summary": "PostgreSQL for everything.",
            },
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
                "contradicts": memory_id,
            }
        ]

        low_confidence = {**MOCK_RESOLUTION, "confidence": 0.2}

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = [
                _llm_response(decisions),
                _resolution_response(low_confidence),
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-021")

        # Conflict detected but not resolved
        assert len(result["conflicts"]) == 1
        assert len(result["resolved"]) == 0

        # Original memory unchanged
        original = tmp_store.get(memory_id)
        assert "PostgreSQL" in original["content"]

    def test_resolution_logs_merged_action(self, consolidator, tmp_store, ephemeral_file):
        """Resolution creates a 'merged' consolidation log entry."""
        memory_id = tmp_store.create(
            path="style/naming.md",
            content="Use camelCase everywhere.",
            metadata={
                "stage": "consolidated",
                "title": "Naming convention",
                "summary": "camelCase.",
            },
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
                "contradicts": memory_id,
            }
        ]

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = [
                _llm_response(decisions),
                _resolution_response(MOCK_RESOLUTION),
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-022")

        with sqlite3.connect(tmp_store.db_path) as conn:
            rows = conn.execute(
                "SELECT action, rationale FROM consolidation_log WHERE memory_id = ? AND action = 'merged'",
                (memory_id,),
            ).fetchall()

        assert len(rows) == 1
        assert "Contradiction resolved" in rows[0][1]

    def test_missing_contradict_target_skipped(self, consolidator, tmp_store, ephemeral_file):
        """If the contradicted memory doesn't exist, resolution is skipped."""
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

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-023")

        assert len(result["conflicts"]) == 1
        assert len(result["resolved"]) == 0

    def test_resolution_llm_failure_skipped(self, consolidator, tmp_store, ephemeral_file):
        """If the resolution LLM call fails, the conflict is logged but not resolved."""
        memory_id = tmp_store.create(
            path="tools/editor.md",
            content="Uses vim.",
            metadata={
                "stage": "consolidated",
                "title": "Editor",
                "summary": "vim user.",
            },
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
                "contradicts": memory_id,
            }
        ]

        bad_resolution = MagicMock()
        bad_resolution.content = [MagicMock(text="not json at all")]

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = [
                _llm_response(decisions),
                bad_resolution,
            ]
            result = consolidator.consolidate_session(ephemeral_file, "sess-024")

        assert len(result["conflicts"]) == 1
        assert len(result["resolved"]) == 0

    def test_no_conflicts_means_no_resolution(self, consolidator, ephemeral_file):
        """When there are no conflicts, no resolution LLM calls are made."""
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

        with patch("core.consolidator.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(decisions)
            result = consolidator.consolidate_session(ephemeral_file, "sess-025")

        assert result["resolved"] == []
        # Only one LLM call (consolidation), no resolution call
        assert mock_cls.return_value.messages.create.call_count == 1
