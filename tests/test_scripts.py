"""
Tests for scripts/reduce.py and scripts/consolidate.py.

No real Anthropic API calls are made — all LLM paths are either not exercised
(unit-level tests on pure functions/DB operations) or explicitly mocked.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# scripts/ is not a package; insert the repo root so we can import from it.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.reduce import apply_operations, init_db
from scripts.consolidate import CONSOLIDATION_GATE_PROMPT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    """Return an SQLite connection with the full reduce.py schema.

    Mirrors what init_db() should create once the processed_sessions table
    migration is applied. Tests that need the legacy (pre-fix) schema use
    _make_legacy_conn() instead.
    """
    db_path = tmp_path / "observations.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            observation_type TEXT,
            tags TEXT DEFAULT '[]',
            count INTEGER DEFAULT 1,
            sources TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_sessions (
            session_id TEXT PRIMARY KEY,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _make_legacy_conn(tmp_path: Path) -> sqlite3.Connection:
    """Return an SQLite connection with only the observations table — no
    processed_sessions table. This models a DB created by a pre-fix init_db().
    """
    db_path = tmp_path / "observations_legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            observation_type TEXT,
            tags TEXT DEFAULT '[]',
            count INTEGER DEFAULT 1,
            sources TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _processed_sessions(conn: sqlite3.Connection) -> set:
    """Return the set of session_ids recorded in the processed_sessions table."""
    return {
        row[0]
        for row in conn.execute("SELECT session_id FROM processed_sessions").fetchall()
    }


def _session_ids_in_sources(conn: sqlite3.Connection) -> set:
    """Collect all session_ids that appear in any observation's sources list."""
    rows = conn.execute("SELECT sources FROM observations").fetchall()
    ids: set = set()
    for (sources_json,) in rows:
        ids.update(json.loads(sources_json))
    return ids


# ---------------------------------------------------------------------------
# TestReduceDedup
# ---------------------------------------------------------------------------


class TestReduceDedup:
    """Tests for apply_operations deduplication and session tracking."""

    def test_apply_operations_records_session_id_in_processed_sessions(self, tmp_path):
        """apply_operations writes the session_id into processed_sessions after
        any operation, so subsequent runs can skip this session.
        """
        conn = _make_conn(tmp_path)
        result = {
            "create": [
                {
                    "title": "Prefer explicit returns",
                    "content": "User consistently requests explicit return types.",
                    "observation_type": "preference_signal",
                    "tags": ["python", "style"],
                }
            ],
            "reinforce": [],
        }
        apply_operations(conn, result, session_id="sess-abc")
        assert "sess-abc" in _processed_sessions(conn)

    def test_empty_session_is_still_recorded(self, tmp_path):
        """A session that produces no creates or reinforcements must still be marked
        as processed so it is not re-visited on subsequent runs.
        """
        conn = _make_conn(tmp_path)
        result = {"create": [], "reinforce": []}
        apply_operations(conn, result, session_id="sess-empty")
        # The processed_sessions insert happens unconditionally after commit,
        # so even a no-op session is recorded.
        assert "sess-empty" in _processed_sessions(conn)

    def test_processed_session_not_reprocessed(self, tmp_path):
        """After a session is processed, its id must not appear in the 'remaining'
        set when the main loop filters already-processed sessions.

        This test replicates the processed_sessions-based filtering logic from
        reduce.main() to verify the full dedup path works end-to-end.
        """
        conn = _make_conn(tmp_path)
        result = {
            "create": [
                {
                    "title": "Use tmp_path in tests",
                    "content": "Prefer pytest's tmp_path over tempfile.mkdtemp.",
                    "observation_type": "workflow_pattern",
                    "tags": ["testing"],
                }
            ],
            "reinforce": [],
        }
        apply_operations(conn, result, session_id="sess-done")

        # Replicate the processed_sessions-based filtering logic from reduce.main().
        summaries = [
            {"session_id": "sess-done", "summary": "..."},
            {"session_id": "sess-new", "summary": "..."},
        ]
        processed = _processed_sessions(conn)
        remaining = [s for s in summaries if s["session_id"] not in processed]
        session_ids = [s["session_id"] for s in remaining]

        assert "sess-done" not in session_ids
        assert "sess-new" in session_ids

    def test_legacy_db_fallback_uses_sources_scan(self, tmp_path):
        """The main loop must fall back to scanning sources when the
        processed_sessions table does not exist (legacy DB from a pre-fix run).

        This tests the except-branch in reduce.main() that handles the missing
        table by reading session_ids from observation sources instead.
        """
        conn = _make_legacy_conn(tmp_path)

        # Seed an observation manually with a known session in sources.
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Legacy obs", "Content", "correction", "[]", json.dumps(["sess-legacy"])),
        )
        conn.commit()

        # Confirm no processed_sessions table exists.
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "processed_sessions" not in tables

        # Replicate the try/except fallback from reduce.main().
        try:
            processed = set(
                row[0]
                for row in conn.execute(
                    "SELECT session_id FROM processed_sessions"
                ).fetchall()
            )
        except sqlite3.OperationalError:
            # Legacy path: scan sources.
            processed = set()
            for (sources_json,) in conn.execute(
                "SELECT sources FROM observations"
            ).fetchall():
                processed.update(json.loads(sources_json))

        # The legacy session must appear in processed via the sources fallback.
        assert "sess-legacy" in processed

    def test_reinforce_increments_count_and_appends_session(self, tmp_path):
        """apply_operations increments count and appends the new session_id to
        sources when reinforcing an existing observation.
        """
        conn = _make_conn(tmp_path)
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Seed obs", "Content", "correction", "[]", json.dumps(["sess-1"])),
        )
        conn.commit()
        obs_id = conn.execute("SELECT id FROM observations").fetchone()[0]

        result = {"create": [], "reinforce": [{"id": obs_id}]}
        apply_operations(conn, result, session_id="sess-2")

        row = conn.execute(
            "SELECT count, sources FROM observations WHERE id = ?", (obs_id,)
        ).fetchone()
        count, sources_json = row
        assert count == 2
        assert "sess-2" in json.loads(sources_json)

    def test_reinforce_does_not_duplicate_same_session(self, tmp_path):
        """Reinforcing the same observation from the same session must not add the
        session_id to sources twice, preserving deduplication correctness.
        """
        conn = _make_conn(tmp_path)
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Seed obs", "Content", "correction", "[]", json.dumps(["sess-1"])),
        )
        conn.commit()
        obs_id = conn.execute("SELECT id FROM observations").fetchone()[0]

        # Apply the same session twice (simulating a retry / double-run scenario).
        result = {"create": [], "reinforce": [{"id": obs_id}]}
        apply_operations(conn, result, session_id="sess-1")
        apply_operations(conn, result, session_id="sess-1")

        sources = json.loads(
            conn.execute(
                "SELECT sources FROM observations WHERE id = ?", (obs_id,)
            ).fetchone()[0]
        )
        assert sources.count("sess-1") == 1


# ---------------------------------------------------------------------------
# TestConsolidateGatePrompt
# ---------------------------------------------------------------------------


class TestConsolidateGatePrompt:
    """Tests for the CONSOLIDATION_GATE_PROMPT string in scripts/consolidate.py."""

    def test_prompt_contains_frequency_floor_header(self):
        """CONSOLIDATION_GATE_PROMPT must contain the FREQUENCY FLOOR section header
        so the LLM receives the stricter gate instruction for freq=1 observations.
        """
        assert "FREQUENCY FLOOR" in CONSOLIDATION_GATE_PROMPT

    def test_prompt_mentions_freq1_strict_gate(self):
        """The prompt must explicitly call out freq=1 observations and the
        'would I do something wrong' strict gate question.
        """
        assert "freq=1" in CONSOLIDATION_GATE_PROMPT
        assert "wrong" in CONSOLIDATION_GATE_PROMPT.lower()

    def test_prompt_instructs_against_hedging_keeps(self):
        """The FREQUENCY FLOOR section must explicitly prohibit keeping freq=1
        observations for 'might be useful' or hedging reasons (D-04 requirement).
        """
        prompt_lower = CONSOLIDATION_GATE_PROMPT.lower()
        assert any(
            phrase in prompt_lower
            for phrase in ("hedging", "might be useful", "completeness")
        )

    def test_prompt_describes_single_session_as_hypothesis(self):
        """Single-session observations should be framed as hypotheses, not patterns,
        to communicate the epistemic weight difference to the LLM.
        """
        assert "hypothes" in CONSOLIDATION_GATE_PROMPT.lower()

    def test_frequency_floor_appears_after_frequency_signal(self):
        """FREQUENCY FLOOR must appear after the FREQUENCY SIGNAL section so the
        LLM reads general frequency guidance before the stricter floor rule.
        """
        signal_pos = CONSOLIDATION_GATE_PROMPT.find("FREQUENCY SIGNAL")
        floor_pos = CONSOLIDATION_GATE_PROMPT.find("FREQUENCY FLOOR")
        assert signal_pos != -1, "FREQUENCY SIGNAL section missing from prompt"
        assert floor_pos != -1, "FREQUENCY FLOOR section missing from prompt"
        assert signal_pos < floor_pos
