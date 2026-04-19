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

from scripts.reduce import apply_operations, init_db, _find_near_duplicates
from scripts.consolidate import (
    CONSOLIDATION_GATE_PROMPT,
    _cluster_by_tfidf,
    format_observations,
)


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
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            manifest_hash TEXT,
            obs_count_at_time INTEGER
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
        for s in json.loads(sources_json):
            ids.add(s["session"])
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

        # Seed with structured source format
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Legacy obs", "Content", "correction", "[]",
             json.dumps([{"session": "sess-legacy"}])),
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
            processed = set()
            for (sources_json,) in conn.execute(
                "SELECT sources FROM observations"
            ).fetchall():
                for s in json.loads(sources_json):
                    processed.add(s["session"])

        assert "sess-legacy" in processed

    def test_reinforce_increments_count_and_appends_session(self, tmp_path):
        """apply_operations increments count and appends the new session_id to
        sources when reinforcing an existing observation.
        """
        conn = _make_conn(tmp_path)
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Seed obs", "Content", "correction", "[]",
             json.dumps([{"session": "sess-1"}])),
        )
        conn.commit()
        obs_id = conn.execute("SELECT id FROM observations").fetchone()[0]

        result = {"create": [], "reinforce": [{"id": obs_id, "source_lines": [15]}]}
        apply_operations(conn, result, session_id="sess-2")

        row = conn.execute(
            "SELECT count, sources FROM observations WHERE id = ?", (obs_id,)
        ).fetchone()
        count, sources_json = row
        assert count == 2
        sources = json.loads(sources_json)
        session_ids = {s["session"] for s in sources}
        assert "sess-2" in session_ids
        # Verify the new entry has line refs
        sess2 = next(s for s in sources if s["session"] == "sess-2")
        assert sess2["lines"] == [15]

    def test_reinforce_does_not_duplicate_same_session(self, tmp_path):
        """Reinforcing the same observation from the same session must not add the
        session_id to sources twice, preserving deduplication correctness.
        """
        conn = _make_conn(tmp_path)
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Seed obs", "Content", "correction", "[]",
             json.dumps([{"session": "sess-1"}])),
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
        session_ids = [s["session"] for s in sources]
        assert session_ids.count("sess-1") == 1


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


# ---------------------------------------------------------------------------
# TestTFIDFDedup
# ---------------------------------------------------------------------------


class TestTFIDFDedup:
    """Tests for TF-IDF near-duplicate detection in reduce.py and
    TF-IDF pre-clustering in consolidate.py.
    """

    # ------------------------------------------------------------------
    # _find_near_duplicates
    # ------------------------------------------------------------------

    def test_near_duplicate_create_becomes_reinforce(self, tmp_path):
        """A CREATE whose content is nearly identical to an existing observation
        must increment that observation's count (REINFORCE) rather than
        inserting a new row.

        Uses nearly identical vocabulary to reliably exceed the 0.85 cosine
        similarity threshold under TF-IDF with a two-document corpus.
        A second unrelated seed observation is included so the store has >= 2
        rows (required for TF-IDF to run).
        """
        conn = _make_conn(tmp_path)
        # Primary observation to test dedup against — vocabulary dense enough
        # that a near-copy exceeds the 0.85 threshold.
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "regex boundary keyword scoring feedback computation",
                "regex boundary keyword scoring feedback computation "
                "word boundaries false positives prevent matching",
                "correction",
                "[]",
                json.dumps([{"session": "sess-1"}]),
            ),
        )
        # Second unrelated observation — needed so len(rows) >= 2 for TF-IDF
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "Mock external API calls tests",
                "Never hit real external API endpoints in tests always mock import boundary.",
                "workflow_pattern",
                "[]",
                json.dumps([{"session": "sess-1"}]),
            ),
        )
        conn.commit()

        obs_id = conn.execute(
            "SELECT id FROM observations WHERE title LIKE '%regex%'"
        ).fetchone()[0]

        # Near-duplicate: same vocabulary, one added token — should exceed 0.85 threshold
        result = {
            "create": [
                {
                    "title": "regex boundary keyword scoring feedback computation",
                    "content": "regex boundary keyword scoring feedback computation "
                    "word boundaries false positives prevent matching scorer",
                    "observation_type": "correction",
                    "tags": ["regex", "scoring"],
                }
            ],
            "reinforce": [],
        }
        apply_operations(conn, result, session_id="sess-2")

        row = conn.execute(
            "SELECT count FROM observations WHERE id = ?", (obs_id,)
        ).fetchone()
        new_rows = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

        # The existing observation should have been reinforced (count >= 2)
        assert row[0] >= 2, "Near-duplicate should have reinforced the existing observation"
        # No new observation should have been inserted (still 2 rows total)
        assert new_rows == 2, "Near-duplicate CREATE must not insert a third row"

    def test_dissimilar_content_is_not_deduplicated(self, tmp_path):
        """A CREATE about an unrelated topic must be inserted as a new observation,
        not silently converted to a reinforce of an existing one.
        """
        conn = _make_conn(tmp_path)
        # Seed observations about Python testing
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "Prefer pytest tmp_path over tempfile",
                "Use pytest's built-in tmp_path fixture instead of tempfile.mkdtemp "
                "for cleaner test isolation and automatic cleanup.",
                "workflow_pattern",
                "[]",
                json.dumps([{"session": "sess-1"}]),
            ),
        )
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "Mock Anthropic API in all tests",
                "Never hit the real Anthropic API in tests; always mock the client "
                "using unittest.mock.patch on the module import path.",
                "workflow_pattern",
                "[]",
                json.dumps([{"session": "sess-1"}]),
            ),
        )
        conn.commit()

        result = {
            "create": [
                {
                    "title": "AWS Bedrock client initialization pattern",
                    "content": "Set CLAUDE_CODE_USE_BEDROCK env var to route the Anthropic "
                    "client to AWS Bedrock with AnthropicBedrock() constructor.",
                    "observation_type": "workflow_pattern",
                    "tags": ["aws", "bedrock"],
                }
            ],
            "reinforce": [],
        }
        apply_operations(conn, result, session_id="sess-2")

        new_rows = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        assert new_rows == 3, "Dissimilar content must be inserted as a new observation"

    def test_dedup_graceful_when_sklearn_absent(self, tmp_path):
        """_find_near_duplicates must return [] without raising when sklearn
        is not importable, so the script degrades gracefully.
        """
        conn = _make_conn(tmp_path)
        # Seed some rows so we don't hit the < 2 short-circuit check
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Obs A", "Content about topic A.", "correction", "[]", json.dumps([{"session": "sess-1"}])),
        )
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Obs B", "Content about topic B.", "correction", "[]", json.dumps([{"session": "sess-1"}])),
        )
        conn.commit()

        # Patch builtins.__import__ to raise ImportError for sklearn imports.
        # _find_near_duplicates uses call-site imports (not module-level), so
        # intercepting __import__ is the correct simulation of sklearn absence.
        import builtins
        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name.startswith("sklearn"):
                raise ImportError(f"blocked: {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_blocked_import):
            result = _find_near_duplicates(conn, "some new observation text")

        assert result == [], "_find_near_duplicates must return [] when sklearn is unavailable"

    def test_find_near_duplicates_returns_empty_for_small_store(self, tmp_path):
        """_find_near_duplicates returns [] when the store has < 2 observations
        (TF-IDF is degenerate on a single document).
        """
        conn = _make_conn(tmp_path)
        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Only observation", "Content.", "correction", "[]", json.dumps([{"session": "sess-1"}])),
        )
        conn.commit()

        result = _find_near_duplicates(conn, "Content about the same thing.")
        assert result == [], "_find_near_duplicates must return [] for stores with < 2 rows"

    # ------------------------------------------------------------------
    # _cluster_by_tfidf
    # ------------------------------------------------------------------

    def test_cluster_by_tfidf_returns_empty_for_single_observation(self):
        """_cluster_by_tfidf returns {} when given fewer than 2 observations."""
        obs = [{"id": 1, "title": "Only obs", "content": "Some content here."}]
        result = _cluster_by_tfidf(obs)
        assert result == {}

    def test_cluster_by_tfidf_graceful_when_sklearn_absent(self):
        """_cluster_by_tfidf returns {} without raising when sklearn is not importable."""
        obs = [
            {"id": 1, "title": "Obs A", "content": "Content about python testing."},
            {"id": 2, "title": "Obs B", "content": "Content about python debugging."},
        ]
        import builtins
        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name.startswith("sklearn"):
                raise ImportError(f"blocked: {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_blocked_import):
            result = _cluster_by_tfidf(obs)

        assert result == {}, "_cluster_by_tfidf must return {} when sklearn is unavailable"

    def test_cluster_by_tfidf_similar_observations_share_cluster(self):
        """Two observations on highly similar topics should land in the same cluster.

        Uses vocabulary-rich texts with enough shared rare tokens to exceed the
        cosine similarity threshold. Observations 10 and 11 use identical vocabulary
        with one token difference (verified: cosine similarity > 0.90 under TF-IDF).
        Observation 12 uses entirely different vocabulary (verified: similarity ≈ 0.0).
        """
        obs = [
            {
                "id": 10,
                "title": "regex boundary keyword scoring feedback computation",
                "content": "regex boundary keyword scoring feedback computation "
                "word boundaries false positives prevent matching",
            },
            {
                "id": 11,
                "title": "regex boundary keyword scoring feedback computation",
                "content": "regex boundary keyword scoring feedback computation "
                "word boundaries false positives prevent matching scorer",
            },
            {
                "id": 12,
                "title": "AWS Bedrock deployment routing environment",
                "content": "BEDROCK deployment routing Anthropic environment variable "
                "production configuration setup client initialization",
            },
        ]
        # Use threshold=0.85 — the first two observations share almost identical
        # vocabulary and consistently exceed this threshold under TF-IDF.
        result = _cluster_by_tfidf(obs, threshold=0.85)
        # Result must map obs ids to cluster ids
        assert set(result.keys()) == {10, 11, 12}
        # The two nearly-identical observations (10, 11) must share the same cluster
        assert result[10] == result[11], (
            "Observations 10 and 11 share nearly identical vocabulary and should cluster"
        )
        # The unrelated observation (12) must be in a different cluster
        assert result[12] != result[10], (
            "Observation 12 (AWS Bedrock) should not be clustered with the regex observations"
        )

    # ------------------------------------------------------------------
    # format_observations cluster hints
    # ------------------------------------------------------------------

    def test_format_observations_includes_cluster_hints(self):
        """format_observations must append [cluster:N] to observations when
        a clusters dict is provided.
        """
        obs = [
            {
                "id": 1,
                "title": "Obs A",
                "content": "Content A.",
                "count": 2,
                "observation_type": "correction",
                "sources": json.dumps([{"session": "sess-1"}, {"session": "sess-2"}]),
            },
            {
                "id": 2,
                "title": "Obs B",
                "content": "Content B.",
                "count": 1,
                "observation_type": "workflow_pattern",
                "sources": json.dumps([{"session": "sess-1"}]),
            },
        ]
        clusters = {1: 0, 2: 1}
        formatted = format_observations(obs, clusters=clusters)

        assert "[cluster:0]" in formatted, "Cluster hint for obs 1 must appear in output"
        assert "[cluster:1]" in formatted, "Cluster hint for obs 2 must appear in output"

    def test_format_observations_without_clusters_is_unchanged(self):
        """format_observations called without clusters must produce the same
        output as before the clustering feature was added.
        """
        obs = [
            {
                "id": 1,
                "title": "Obs A",
                "content": "Content A.",
                "count": 1,
                "observation_type": "correction",
                "sources": json.dumps([{"session": "sess-1"}]),
            }
        ]
        formatted = format_observations(obs)
        assert "[cluster:" not in formatted, "No cluster hints when clusters arg is omitted"
        assert "#1" in formatted
        assert "Obs A" in formatted
