"""Tests for the W5 schema back-derivation migration.

The W5 migration is a one-shot script that ran before #17's taxonomy
collapse. Post-#17, the script's output vocabulary partially diverges
from the live `KIND_VALUES` set, and the data it migrated is now
two generations old. The script is preserved for archival; this test
module is skipped because the live taxonomy supersedes what these
assertions encode. See task #17 for the post-collapse vocabulary.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(
    reason="Obsolete: W5 migration superseded by #17 single-kind taxonomy"
)

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.models import db
from scripts.migrate_w5_schema import (
    derive_from_observation_type,
    derive_from_concept_tags,
    derive_kind_from_mode,
    run_migration,
    OBSERVATION_TYPE_MAP,
    CONCEPT_TAGS_COLLAPSE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path / "memory"
    close_db()


def _now():
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Unit: concept_tags collapse map
# ---------------------------------------------------------------------------


class TestConceptTagsCollapse:
    def test_how_it_works_maps_to_conceptual(self):
        result = derive_from_concept_tags(json.dumps(["how-it-works"]))
        assert result["knowledge_type"] == "conceptual"
        assert result["knowledge_type_confidence"] == "high"

    def test_why_it_exists_maps_to_conceptual(self):
        result = derive_from_concept_tags(json.dumps(["why-it-exists"]))
        assert result["knowledge_type"] == "conceptual"

    def test_what_changed_maps_to_factual(self):
        result = derive_from_concept_tags(json.dumps(["what-changed"]))
        assert result["knowledge_type"] == "factual"
        assert result["knowledge_type_confidence"] == "high"

    def test_gotcha_maps_to_metacognitive(self):
        result = derive_from_concept_tags(json.dumps(["correction"]))
        assert result["knowledge_type"] == "metacognitive"

    def test_trade_off_maps_to_metacognitive(self):
        result = derive_from_concept_tags(json.dumps(["trade-off"]))
        assert result["knowledge_type"] == "metacognitive"

    def test_problem_solution_is_ambiguous(self):
        """problem-solution maps to None — flag for review."""
        result = derive_from_concept_tags(json.dumps(["problem-solution"]))
        assert result.get("knowledge_type") is None
        assert result.get("knowledge_type_confidence") == "low"
        assert result.get("_flag_for_review") is True

    def test_pattern_is_ambiguous(self):
        """pattern maps to None — flag for review."""
        result = derive_from_concept_tags(json.dumps(["pattern"]))
        assert result.get("knowledge_type") is None
        assert result.get("_flag_for_review") is True

    def test_null_input_returns_empty(self):
        assert derive_from_concept_tags(None) == {}

    def test_empty_list_returns_empty(self):
        assert derive_from_concept_tags("[]") == {}

    def test_unknown_tag_returns_empty(self):
        result = derive_from_concept_tags(json.dumps(["unknown-tag"]))
        assert result == {}

    def test_all_seven_tags_covered(self):
        """All 7 tags in the collapse map are handled without KeyError."""
        for tag in CONCEPT_TAGS_COLLAPSE:
            result = derive_from_concept_tags(json.dumps([tag]))
            assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Unit: mode → kind back-derivation
# ---------------------------------------------------------------------------


class TestModeToKind:
    @pytest.mark.parametrize("mode", [
        "decision", "fact", "preference", "directive", "correction", "open_question",
    ])
    def test_valid_mode_maps_1_to_1(self, mode):
        assert derive_kind_from_mode(mode) == mode

    def test_none_returns_none(self):
        assert derive_kind_from_mode(None) is None

    def test_unknown_mode_returns_none(self):
        assert derive_kind_from_mode("unknown_value") is None


# ---------------------------------------------------------------------------
# Unit: observation_type → (kind, subject, knowledge_type) back-derivation
# ---------------------------------------------------------------------------


class TestObservationTypeBackDerivation:
    @pytest.mark.parametrize("obs_type,expected_kind,expected_subject,expected_kt", [
        ("preference_signal",     "preference",  "user",          "metacognitive"),
        ("shared_insight",        "fact",     "domain",        "conceptual"),
        ("domain_knowledge",      "fact",     "domain",        "factual"),
        ("workflow_pattern",      "preference",  "workflow",      "procedural"),
        ("self_observation",      "fact",     "self",          "metacognitive"),
        ("personality",           "fact",     "user",          "metacognitive"),
        ("aesthetic",             "preference",  "user",          "metacognitive"),
        ("collaboration_dynamic", "fact",     "collaboration", "metacognitive"),
        ("system_change",         "fact",     "system",        "factual"),
    ])
    def test_unambiguous_mappings(self, obs_type, expected_kind, expected_subject, expected_kt):
        result = derive_from_observation_type(obs_type)
        assert result["kind"] == expected_kind
        assert result.get("subject") == expected_subject
        assert result["knowledge_type"] == expected_kt
        assert result.get("_flag_for_review") is not True

    def test_correction_is_flagged(self):
        """correction subject is ambiguous — must be flagged."""
        result = derive_from_observation_type("correction")
        assert result["kind"] == "correction"
        assert result.get("_flag_for_review") is True
        assert result["knowledge_type_confidence"] == "low"

    def test_decision_context_is_flagged(self):
        """decision_context subject is ambiguous — must be flagged."""
        result = derive_from_observation_type("decision_context")
        assert result["kind"] == "decision"
        assert result.get("_flag_for_review") is True

    def test_system_change_sets_work_event(self):
        result = derive_from_observation_type("system_change")
        assert result.get("work_event") == "change"

    def test_unknown_observation_type_returns_marker(self):
        result = derive_from_observation_type("nonexistent_type")
        assert "_unknown_observation_type" in result

    def test_all_11_observation_types_covered(self):
        """All 11 legacy observation_type values map without raising."""
        for obs_type in OBSERVATION_TYPE_MAP:
            result = derive_from_observation_type(obs_type)
            assert isinstance(result, dict)
            assert "kind" in result


# ---------------------------------------------------------------------------
# Integration: migration idempotence + run_migration stats
# ---------------------------------------------------------------------------


class TestRunMigration:
    @pytest.fixture(autouse=True)
    def _add_legacy_columns(self, store):
        """Add legacy columns (mode, observation_type, concept_tags) to fresh test DB.

        `store` is required for fixture ordering (ensures DB init runs first).
        """
        assert store is not None
        for col, typ in [("mode", "TEXT"), ("observation_type", "TEXT"), ("concept_tags", "TEXT")]:
            try:
                db.execute_sql(f"ALTER TABLE memories ADD COLUMN {col} {typ}")
            except Exception:
                pass  # already exists

    def _insert_raw(self, memory_id, mode=None, observation_type=None, concept_tags=None):
        """Insert a row with legacy fields via raw SQL (bypasses model field constraints)."""
        db.execute_sql(
            "INSERT INTO memories (id, stage, title, content, source, created_at, updated_at, "
            "mode, observation_type, concept_tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (memory_id, "ephemeral", "test", "content", "test", _now(), _now(),
             mode, observation_type, concept_tags),
        )

    def test_mode_to_kind_backfill(self, store):
        """Rows with mode= get kind= populated."""
        # Insert with a legacy mode field
        self._insert_raw("row-1", mode="decision")

        stats = run_migration(commit=True, db_path=str(store / "index.db"))
        assert stats["rows_back_derived"] >= 1

        # Re-init to re-read
        init_db(base_dir=str(store))
        result = db.execute_sql(
            "SELECT kind FROM memories WHERE id = ?", ("row-1",)
        ).fetchone()
        assert result[0] == "decision"

    def test_observation_type_backfill(self, store):
        """Rows with observation_type= get kind/subject/knowledge_type populated."""
        self._insert_raw("row-obs", observation_type="workflow_pattern")

        run_migration(commit=True, db_path=str(store / "index.db"))

        init_db(base_dir=str(store))
        result = db.execute_sql(
            "SELECT kind, subject, knowledge_type FROM memories WHERE id = ?",
            ("row-obs",),
        ).fetchone()
        assert result[0] == "preference"
        assert result[1] == "workflow"
        assert result[2] == "procedural"

    def test_concept_tags_backfill(self, store):
        """Rows with concept_tags get knowledge_type populated."""
        self._insert_raw("row-ct", concept_tags=json.dumps(["correction"]))

        run_migration(commit=True, db_path=str(store / "index.db"))

        init_db(base_dir=str(store))
        result = db.execute_sql(
            "SELECT knowledge_type FROM memories WHERE id = ?", ("row-ct",)
        ).fetchone()
        assert result[0] == "metacognitive"

    def test_idempotent_double_run(self, store):
        """Running migration twice produces same result."""
        self._insert_raw("row-idem", mode="fact")

        run_migration(commit=True, db_path=str(store / "index.db"))
        init_db(base_dir=str(store))
        result1 = db.execute_sql(
            "SELECT kind FROM memories WHERE id = ?", ("row-idem",)
        ).fetchone()

        run_migration(commit=True, db_path=str(store / "index.db"))
        init_db(base_dir=str(store))
        result2 = db.execute_sql(
            "SELECT kind FROM memories WHERE id = ?", ("row-idem",)
        ).fetchone()

        assert result1[0] == result2[0] == "fact"

    def test_dry_run_does_not_write(self, store):
        """Dry-run leaves kind as null."""
        self._insert_raw("row-dry", mode="directive")

        stats = run_migration(commit=False, db_path=str(store / "index.db"))
        assert stats["dry_run"] is True

        init_db(base_dir=str(store))
        result = db.execute_sql(
            "SELECT kind FROM memories WHERE id = ?", ("row-dry",)
        ).fetchone()
        assert result[0] is None

    def test_stats_counts(self, store):
        """Stats dict has expected keys and reasonable values."""
        self._insert_raw("row-s1", mode="preference")
        self._insert_raw("row-s2", mode="correction")

        stats = run_migration(commit=False, db_path=str(store / "index.db"))
        assert "rows_processed" in stats
        assert "rows_back_derived" in stats
        assert "rows_flagged" in stats
        assert "rows_skipped" in stats
        assert stats["rows_processed"] >= 2
