"""
Tests for scripts/audit_lifecycle.py

Uses tmp_path (pytest built-in) for DB isolation.
Does NOT touch ~/.claude/memory.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure project root is on path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.database import init_db, close_db
from core.models import ConsolidationLog, Memory, Observation

# Import the module under test
import importlib.util

_SCRIPT = _REPO_ROOT / "scripts" / "audit_lifecycle.py"
_spec = importlib.util.spec_from_file_location("audit_lifecycle", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_report = _mod.build_report
_pct = _mod._pct
_stuck_pending = _mod._stuck_pending
_gather_sessions = _mod._gather_sessions
_stage_counts = _mod._stage_counts
_count_ephemeral = _mod._count_ephemeral


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base(tmp_path):
    """Isolated in-memory DB per test."""
    mem_dir = tmp_path / "memory"
    init_db(base_dir=str(mem_dir))
    yield mem_dir
    close_db()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_memory(session_id: str, stage: str, importance: float = 0.5) -> Memory:
    m = Memory.create(
        stage=stage,
        source_session=session_id,
        title=f"mem-{stage}-{session_id[:8]}",
        summary="test summary",
        importance=importance,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    return m


def _make_observation(
    session_id: str,
    status: str = "pending",
    created_at: str | None = None,
) -> Observation:
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    obs = Observation.create(
        session_id=session_id,
        content="test observation content",
        status=status,
        created_at=created_at,
    )
    return obs


def _make_consolidation_log(session_id: str, action: str) -> ConsolidationLog:
    return ConsolidationLog.create(
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
        action=action,
        memory_id="fake-id",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLifecycleAudit:

    def test_basic_run_with_no_sessions(self, base):
        """Empty DB produces report with zero counts, doesn't crash."""
        report = build_report(base_dir=str(base))
        assert "# Lifecycle audit report" in report
        assert "0" in report  # zero counts somewhere
        assert "## Summary" in report
        assert "## Stuck-pending observations" in report
        assert "## Per-session breakdown" in report
        # Stuck section should say no stuck observations
        assert "No observations stuck" in report

    def test_section_structure(self, base):
        """Report always contains all required sections, even empty."""
        report = build_report(base_dir=str(base))
        required_sections = [
            "# Lifecycle audit report",
            "## Summary",
            "## Stuck-pending observations",
            "## Per-session breakdown",
        ]
        for section in required_sections:
            assert section in report, f"Missing section: {section!r}"

    def test_session_with_consolidated_memories(self, base):
        """5 consolidated memories for a session → audit reports 5 consolidated."""
        session_id = "test-session-consolidated-001"
        for _ in range(5):
            _make_memory(session_id, stage="consolidated")

        report = build_report(base_dir=str(base))

        assert session_id in report
        # The per-session table should contain session + consolidated count of 5
        # Find lines with the session_id
        session_lines = [l for l in report.splitlines() if session_id in l]
        assert session_lines, "Session not found in report"
        # The row should contain '5' for consolidated column
        row = session_lines[0]
        assert "5" in row, f"Expected '5' in session row, got: {row}"

    def test_crystallized_count_reported(self, base):
        """3 crystallized memories → crystallized count = 3 for that session."""
        session_id = "test-session-crystal-001"
        for _ in range(3):
            _make_memory(session_id, stage="crystallized")

        report = build_report(base_dir=str(base))

        assert session_id in report
        session_lines = [l for l in report.splitlines() if session_id in l]
        assert session_lines
        row = session_lines[0]
        assert "3" in row

    def test_instinctive_count_reported(self, base):
        """2 instinctive memories → instinctive count = 2 for that session."""
        session_id = "test-session-instinct-001"
        for _ in range(2):
            _make_memory(session_id, stage="instinctive", importance=0.9)

        report = build_report(base_dir=str(base))

        assert session_id in report
        session_lines = [l for l in report.splitlines() if session_id in l]
        assert session_lines
        row = session_lines[0]
        assert "2" in row

    def test_stuck_pending_detection(self, base):
        """Observations with old pending timestamp → flagged in stuck-pending section."""
        session_id = "test-session-stuck-001"
        old_ts = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        _make_observation(session_id, status="pending", created_at=old_ts)

        report = build_report(base_dir=str(base), stuck_pending_days=7)

        assert "ALERT" in report or "stuck" in report.lower()
        assert session_id in report

    def test_stuck_pending_not_flagged_when_recent(self, base):
        """Recent pending observations should NOT trigger the stuck-pending alert."""
        session_id = "test-session-recent-001"
        recent_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        _make_observation(session_id, status="pending", created_at=recent_ts)

        report = build_report(base_dir=str(base), stuck_pending_days=7)

        # The ALERT block should not appear
        assert "ALERT" not in report
        assert "No observations stuck" in report

    def test_stuck_pending_threshold_respected(self, base):
        """Observations at exactly threshold boundary are handled correctly."""
        session_id = "test-session-boundary-001"
        # 8 days old → stuck at 7-day threshold
        old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        _make_observation(session_id, status="pending", created_at=old_ts)

        report_7 = build_report(base_dir=str(base), stuck_pending_days=7)
        report_30 = build_report(base_dir=str(base), stuck_pending_days=30)

        assert "ALERT" in report_7
        assert "No observations stuck" in report_30

    def test_promotion_rate_calculation(self, base):
        """Verify promotion rate percentages computed correctly."""
        # 10 observations, 4 consolidated = 40%
        assert _pct(4, 10) == "40.0%"
        assert _pct(0, 10) == "0.0%"
        assert _pct(10, 10) == "100.0%"
        assert _pct(1, 3) == "33.3%"

    def test_promotion_rate_zero_denominator(self, base):
        """Zero denominator returns n/a."""
        assert _pct(0, 0) == "n/a"
        assert _pct(5, 0) == "n/a"

    def test_promotion_rate_in_report(self, base):
        """Report includes promotion rate for sessions with memories."""
        session_id = "test-session-rate-001"
        # 4 consolidated memories, 4 observations
        for _ in range(4):
            _make_memory(session_id, stage="consolidated")
            _make_observation(session_id, status="done")

        report = build_report(base_dir=str(base))
        assert "promotion_rate" in report or "%" in report

    def test_subsumed_count_from_consolidation_log(self, base):
        """Subsumed count comes from consolidation_log with action=subsumed."""
        session_id = "test-session-subsumed-001"
        _make_memory(session_id, stage="consolidated")
        _make_consolidation_log(session_id, action="subsumed")
        _make_consolidation_log(session_id, action="subsumed")
        _make_consolidation_log(session_id, action="kept")

        report = build_report(base_dir=str(base))
        assert session_id in report
        # 2 subsumed should appear in the row
        session_lines = [l for l in report.splitlines() if session_id in l]
        assert session_lines
        row = session_lines[0]
        assert "2" in row

    def test_limit_sessions(self, base):
        """--limit-sessions N only processes N most-recent sessions."""
        for i in range(5):
            sid = f"session-{i:04d}"
            _make_memory(sid, stage="consolidated")

        # With limit=2, only 2 sessions in per-session breakdown
        sessions = _gather_sessions(limit_sessions=2)
        assert len(sessions) == 2

    def test_no_limit_sessions(self, base):
        """Without limit, all sessions are gathered."""
        for i in range(4):
            sid = f"session-nolimit-{i:04d}"
            _make_memory(sid, stage="consolidated")

        sessions = _gather_sessions(limit_sessions=None)
        assert len(sessions) >= 4

    def test_report_valid_markdown_tables(self, base):
        """All table lines have consistent column counts."""
        session_id = "test-session-table-001"
        _make_memory(session_id, stage="consolidated")
        _make_observation(session_id, status="done")

        report = build_report(base_dir=str(base))

        # Find table blocks: consecutive lines starting with |
        table_lines: list[list[str]] = []
        current_table: list[str] = []
        for line in report.splitlines():
            if line.startswith("|"):
                current_table.append(line)
            else:
                if current_table:
                    table_lines.append(current_table)
                    current_table = []
        if current_table:
            table_lines.append(current_table)

        for table in table_lines:
            if len(table) < 2:
                continue
            # Header and separator must have same number of | chars
            col_counts = [line.count("|") for line in table]
            header_cols = col_counts[0]
            for i, cnt in enumerate(col_counts):
                assert cnt == header_cols, (
                    f"Table row {i} has {cnt} pipes, header has {header_cols}. "
                    f"Row: {table[i]!r}"
                )

    def test_stage_counts_function(self, base):
        """_stage_counts returns correct counts per stage for a session."""
        session_id = "test-session-stagect-001"
        _make_memory(session_id, stage="consolidated")
        _make_memory(session_id, stage="consolidated")
        _make_memory(session_id, stage="crystallized")

        counts = _stage_counts(session_id)
        assert counts["consolidated"] == 2
        assert counts["crystallized"] == 1
        assert counts["instinctive"] == 0

    def test_empty_sessions_no_crash(self, base):
        """Sessions with no memories/observations produce valid rows without crashing."""
        # Insert only a consolidation_log entry, no Memory or Observation
        _make_consolidation_log("orphan-session-001", action="kept")
        report = build_report(base_dir=str(base))
        assert "# Lifecycle audit report" in report

    def test_output_file_atomic_write(self, base, tmp_path):
        """Output file is written atomically (tempfile + move) without error."""
        import scripts.audit_lifecycle as aly

        out_path = tmp_path / "report.md"
        report = build_report(base_dir=str(base))

        # Simulate the atomic write used in main()
        import os
        import shutil
        import tempfile

        fd, tmp_name = tempfile.mkstemp(dir=tmp_path, prefix=".test_", suffix=".md")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(report)
            shutil.move(tmp_name, str(out_path))
        except Exception:
            os.unlink(tmp_name)
            raise

        assert out_path.exists()
        content = out_path.read_text()
        assert "# Lifecycle audit report" in content
