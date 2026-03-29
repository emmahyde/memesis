"""
Tests for memesis hooks.

Organisation:
  - session_start tests (Task 3.1)
  - pre_compact tests  (Task 3.2)

sys.path is configured here so each test module stands alone without
requiring conftest.py to have already run the insert.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory so `from core.xxx import ...` resolves correctly
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, get_base_dir, get_db_path
from core.models import Memory, ConsolidationLog, RetrievalLog, NarrativeThread, ThreadMember, db
from core.retrieval import RetrievalEngine
from hooks.session_start import create_ephemeral_buffer

# === session_start tests ===


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base(tmp_path):
    """Initialize DB in a throwaway temp directory."""
    base_dir = init_db(base_dir=str(tmp_path / "memory"))
    yield base_dir
    close_db()


def _make_memory(stage, title, content, summary=None, importance=0.5):
    """Create a memory and return its ID."""
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage=stage,
        title=title,
        summary=summary or f"Summary of {title}",
        content=content,
        tags=json.dumps([]),
        importance=importance,
        created_at=now,
        updated_at=now,
    )
    return mem.id


def _record_injection(memory_id, session_id):
    """Record an injection in the retrieval log."""
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
# create_ephemeral_buffer
# ---------------------------------------------------------------------------


class TestSessionStartEphemeralBuffer:
    def test_session_start_creates_buffer_file(self, base):
        """Buffer file is created under ephemeral/ with today's date."""
        buf = create_ephemeral_buffer(base)
        assert buf.exists()

    def test_session_start_buffer_path_contains_date(self, base):
        """Buffer filename includes today's date (YYYY-MM-DD)."""
        today = datetime.now().strftime("%Y-%m-%d")
        buf = create_ephemeral_buffer(base)
        assert f"session-{today}.md" == buf.name

    def test_session_start_buffer_under_ephemeral_dir(self, base):
        """Buffer is placed inside the store's ephemeral/ directory."""
        buf = create_ephemeral_buffer(base)
        assert buf.parent == base / "ephemeral"

    def test_session_start_buffer_has_header_content(self, base):
        """New buffer file starts with the expected heading."""
        buf = create_ephemeral_buffer(base)
        content = buf.read_text()
        today = datetime.now().strftime("%Y-%m-%d")
        assert f"# Session Observations — {today}" in content

    def test_session_start_buffer_not_overwritten_if_exists(self, base):
        """If the buffer for today already exists it is left unchanged."""
        buf = create_ephemeral_buffer(base)
        buf.write_text("# Previous content\n\nsome notes\n")
        create_ephemeral_buffer(base)
        assert "Previous content" in buf.read_text()

    def test_session_start_buffer_creates_missing_directories(self, tmp_path):
        """ephemeral/ dir is created automatically when absent."""
        base_dir = init_db(base_dir=str(tmp_path / "brand_new_store"))
        try:
            ephemeral_dir = base_dir / "ephemeral"
            buf = create_ephemeral_buffer(base_dir)
            assert ephemeral_dir.exists()
            assert buf.exists()
        finally:
            close_db()


# ---------------------------------------------------------------------------
# main() -- run hook as a subprocess to capture stdout / exit code
# ---------------------------------------------------------------------------

HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "session_start.py")


def _run_hook(env_overrides=None, cwd=None):
    """Run session_start.py as a subprocess and return (stdout, returncode)."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    result = subprocess.run(
        [sys.executable, HOOK_PATH],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or tempfile.mkdtemp(),
        timeout=10,
    )
    return result.stdout, result.returncode


class TestSessionStartMain:
    def test_session_start_exits_zero_no_memories(self, tmp_path):
        """Hook exits 0 when the store is empty."""
        _, code = _run_hook(
            env_overrides={"HOME": str(tmp_path)},
            cwd=str(tmp_path),
        )
        assert code == 0

    def test_session_start_seeds_instinctive_layer(self, tmp_path):
        """Hook seeds instinctive memories (self-model + observation habit) on fresh store."""
        stdout, _ = _run_hook(
            env_overrides={"HOME": str(tmp_path)},
            cwd=str(tmp_path),
        )
        assert "MEMORY CONTEXT" in stdout
        assert "Self-Model" in stdout or "Observation Habit" in stdout

    def test_session_start_prints_injected_context_with_memories(self, tmp_path):
        """Hook outputs memory context block when memories exist."""
        # Pre-populate via init_db + create
        init_db(project_context=str(tmp_path))
        _make_memory("instinctive", "Conciseness", "Always be concise")
        close_db()

        stdout, code = _run_hook(
            env_overrides={"HOME": str(Path.home()), "CLAUDE_SESSION_ID": "test-session"},
            cwd=str(tmp_path),
        )
        assert code == 0
        assert "---MEMORY CONTEXT---" in stdout
        assert "---END MEMORY CONTEXT---" in stdout

    def test_session_start_creates_ephemeral_buffer_on_run(self, tmp_path):
        """Hook creates ephemeral/session-{date}.md during execution."""
        init_db(project_context=str(tmp_path))
        base_dir = get_base_dir()
        today = datetime.now().strftime("%Y-%m-%d")
        expected_buffer = base_dir / "ephemeral" / f"session-{today}.md"
        close_db()

        assert not expected_buffer.exists()

        _run_hook(
            env_overrides={"HOME": str(Path.home())},
            cwd=str(tmp_path),
        )

        assert expected_buffer.exists()

    def test_session_start_handles_error_gracefully_bad_home(self, tmp_path):
        """Hook never crashes even when HOME is a non-writable location."""
        bad_home = str(tmp_path / "does_not_exist_at_all" / "nested")
        stdout, code = _run_hook(
            env_overrides={"HOME": bad_home},
            cwd=str(tmp_path),
        )
        assert code == 0
        assert "Traceback" not in stdout

    def test_session_start_exit_zero_even_on_exception(self, tmp_path):
        """Hook exits 0 regardless of internal errors."""
        stdout, code = _run_hook(
            env_overrides={"HOME": "/nonexistent_directory_xyzzy"},
            cwd=str(tmp_path),
        )
        assert code == 0

    def test_session_start_session_id_env_var_used(self, tmp_path):
        """CLAUDE_SESSION_ID is read from the environment."""
        session_id = "my-custom-session-42"
        init_db(project_context=str(tmp_path))
        _make_memory("instinctive", "Guideline", "Guideline content")
        close_db()

        _run_hook(
            env_overrides={
                "HOME": str(Path.home()),
                "CLAUDE_SESSION_ID": session_id,
            },
            cwd=str(tmp_path),
        )

        # Re-open DB to check the log
        init_db(project_context=str(tmp_path))
        log = list(RetrievalLog.select())
        logged_sessions = {entry.session_id for entry in log}
        close_db()
        assert session_id in logged_sessions

    def test_session_start_session_id_defaults_to_unknown(self, tmp_path):
        """When CLAUDE_SESSION_ID is absent, session_id defaults to 'unknown'."""
        init_db(project_context=str(tmp_path))
        _make_memory("instinctive", "Default Session", "Guideline content")
        close_db()

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_SESSION_ID"}
        env["HOME"] = str(Path.home())
        subprocess.run(
            [sys.executable, HOOK_PATH],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            timeout=10,
        )

        init_db(project_context=str(tmp_path))
        log = list(RetrievalLog.select())
        logged_sessions = {entry.session_id for entry in log}
        close_db()
        assert "unknown" in logged_sessions


# ---------------------------------------------------------------------------
# Injection logging via create_ephemeral_buffer + inject_for_session
# ---------------------------------------------------------------------------


class TestSessionStartInjectionLogging:
    def test_session_start_injection_logged_for_instinctive_memory(self, base):
        """record_injection is called for each instinctive memory surfaced."""
        memory_id = _make_memory("instinctive", "Inst Memory", "Content")
        retriever = RetrievalEngine()
        retriever.inject_for_session("log-test-session")

        log = list(RetrievalLog.select())
        logged_ids = {e.memory_id for e in log}
        assert memory_id in logged_ids

    def test_session_start_injection_logged_for_crystallized_memory(self, base):
        """record_injection is called for each crystallized memory surfaced."""
        memory_id = _make_memory("crystallized", "Cryst", "Crystal content")
        retriever = RetrievalEngine()
        retriever.inject_for_session("log-test-session-2")

        log = list(RetrievalLog.select())
        logged_ids = {e.memory_id for e in log}
        assert memory_id in logged_ids

    def test_session_start_no_injection_log_when_empty_store(self, base):
        """No retrieval log entries written when store has no memories."""
        retriever = RetrievalEngine()
        retriever.inject_for_session("empty-session")

        log = list(RetrievalLog.select())
        assert log == []

    def test_session_start_injection_count_increments_per_session(self, base):
        """injection_count on the memory record tracks how many times it was injected."""
        memory_id = _make_memory("instinctive", "Inst", "Content")
        retriever = RetrievalEngine()

        retriever.inject_for_session("sess-1")
        retriever.inject_for_session("sess-2")
        retriever.inject_for_session("sess-3")

        memory = Memory.get_by_id(memory_id)
        assert memory.injection_count == 3

    def test_session_start_retrieval_type_is_injected(self, base):
        """Log entries created by inject_for_session have retrieval_type='injected'."""
        _make_memory("instinctive", "Title", "Body")
        retriever = RetrievalEngine()
        retriever.inject_for_session("type-check-session")

        log = list(RetrievalLog.select())
        assert all(e.retrieval_type == "injected" for e in log)


# =============================================================================
# === pre_compact tests ===
# =============================================================================

from core.consolidator import Consolidator
from core.feedback import FeedbackLoop
from core.lifecycle import LifecycleManager
from core.manifest import ManifestGenerator
from hooks.pre_compact import main as pre_compact_main


_FAKE_CONSOLIDATION_RESULT = {
    "kept": ["mem-001", "mem-002"],
    "pruned": [{"observation": "stale note", "rationale": "Not useful"}],
    "promoted": [],
    "conflicts": [],
}


def _write_ephemeral_today(base_dir: Path, content: str) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    path = base_dir / "ephemeral" / f"session-{today}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests: no ephemeral buffer -- exits cleanly
# ---------------------------------------------------------------------------


class TestPreCompactNoEphemeral:
    def test_exits_cleanly_when_no_ephemeral_file(self, tmp_path, capsys, monkeypatch):
        """main() should return without error when no ephemeral file exists."""
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-no-buffer")
        monkeypatch.chdir(tmp_path)

        # init_db will set up directories; pre_compact_main calls init_db internally
        # We need to mock init_db to use our temp path
        with patch("hooks.pre_compact.init_db") as mock_init:
            mock_init.return_value = tmp_path / "memory"
            (tmp_path / "memory" / "ephemeral").mkdir(parents=True, exist_ok=True)
            (tmp_path / "memory" / "meta").mkdir(parents=True, exist_ok=True)

            with patch("hooks.pre_compact.SelfReflector"):
                pre_compact_main()

        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_stdout_empty_on_no_ephemeral(self, tmp_path, capsys, monkeypatch):
        """stdout must always be empty so Claude Code hook protocol is respected."""
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-stdout-check")
        monkeypatch.chdir(tmp_path)

        with patch("hooks.pre_compact.init_db") as mock_init:
            mock_init.return_value = tmp_path / "mem"
            (tmp_path / "mem" / "ephemeral").mkdir(parents=True, exist_ok=True)
            (tmp_path / "mem" / "meta").mkdir(parents=True, exist_ok=True)

            with patch("hooks.pre_compact.SelfReflector"):
                pre_compact_main()

        out, _ = capsys.readouterr()
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Tests: full consolidation cycle (mocked LLM)
# ---------------------------------------------------------------------------


class TestPreCompactConsolidation:
    def _run_main_with_mocks(self, tmp_path, monkeypatch, session_id="sess-test"):
        """Helper: run main() with all heavy dependencies mocked."""
        monkeypatch.setenv("CLAUDE_SESSION_ID", session_id)
        monkeypatch.chdir(tmp_path)

        base_dir = init_db(base_dir=str(tmp_path))
        _write_ephemeral_today(base_dir, "## Observations\n- Used SQLite for local caching\n")

        mock_consolidator = MagicMock(spec=Consolidator)
        mock_consolidator.consolidate_session.return_value = _FAKE_CONSOLIDATION_RESULT

        mock_feedback = MagicMock(spec=FeedbackLoop)
        mock_manifest = MagicMock(spec=ManifestGenerator)

        with patch("hooks.pre_compact.init_db", return_value=base_dir), \
             patch("hooks.pre_compact.LifecycleManager") as MockLifecycle, \
             patch("hooks.pre_compact.Consolidator", return_value=mock_consolidator), \
             patch("hooks.pre_compact.ManifestGenerator", return_value=mock_manifest), \
             patch("hooks.pre_compact.FeedbackLoop", return_value=mock_feedback), \
             patch("hooks.pre_compact.SelfReflector"), \
             patch("hooks.pre_compact.Crystallizer") as MockCryst, \
             patch("hooks.pre_compact.build_threads", return_value=[]), \
             patch("hooks.pre_compact.RelevanceEngine") as MockRel, \
             patch("hooks.pre_compact.embed_for_memory", return_value=None):
            MockRel.return_value.run_maintenance.return_value = {"archived": [], "rehydrated": []}
            MockCryst.return_value.crystallize_candidates.return_value = []
            pre_compact_main()

        close_db()
        return mock_consolidator, mock_feedback, MockLifecycle, mock_manifest

    def test_consolidate_session_called(self, tmp_path, monkeypatch, capsys):
        mock_consolidator, _, _, _ = self._run_main_with_mocks(tmp_path, monkeypatch)
        mock_consolidator.consolidate_session.assert_called_once()

    def test_consolidate_session_receives_ephemeral_path(self, tmp_path, monkeypatch, capsys):
        mock_consolidator, _, _, _ = self._run_main_with_mocks(tmp_path, monkeypatch)
        call_args = mock_consolidator.consolidate_session.call_args
        ephemeral_arg = call_args[0][0]
        today = datetime.now().strftime("%Y-%m-%d")
        assert f"session-{today}.md" in ephemeral_arg

    def test_consolidate_session_receives_session_id(self, tmp_path, monkeypatch):
        mock_consolidator, _, _, _ = self._run_main_with_mocks(
            tmp_path, monkeypatch, session_id="my-session-42"
        )
        call_args = mock_consolidator.consolidate_session.call_args
        assert call_args[0][1] == "my-session-42"

    def test_feedback_update_importance_called(self, tmp_path, monkeypatch):
        _, mock_feedback, _, _ = self._run_main_with_mocks(
            tmp_path, monkeypatch, session_id="sess-fb"
        )
        mock_feedback.update_importance_scores.assert_called_once_with("sess-fb")

    def test_manifest_written_after_consolidation(self, tmp_path, monkeypatch):
        _, _, _, mock_manifest = self._run_main_with_mocks(tmp_path, monkeypatch)
        mock_manifest.write_manifest.assert_called_once()

    def test_stderr_summary_contains_counts(self, tmp_path, monkeypatch, capsys):
        self._run_main_with_mocks(tmp_path, monkeypatch)
        _, err = capsys.readouterr()
        assert "2" in err
        assert "1" in err

    def test_stdout_empty_after_consolidation(self, tmp_path, monkeypatch, capsys):
        self._run_main_with_mocks(tmp_path, monkeypatch)
        out, _ = capsys.readouterr()
        assert out.strip() == ""

    def test_session_id_defaults_to_unknown(self, tmp_path, monkeypatch, capsys):
        """When CLAUDE_SESSION_ID is unset, session_id should default to 'unknown'."""
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.chdir(tmp_path)

        base_dir = init_db(base_dir=str(tmp_path))
        _write_ephemeral_today(base_dir, "## Note\n- something\n")

        mock_consolidator = MagicMock(spec=Consolidator)
        mock_consolidator.consolidate_session.return_value = _FAKE_CONSOLIDATION_RESULT
        mock_feedback = MagicMock(spec=FeedbackLoop)
        mock_manifest = MagicMock(spec=ManifestGenerator)

        with patch("hooks.pre_compact.init_db", return_value=base_dir), \
             patch("hooks.pre_compact.LifecycleManager"), \
             patch("hooks.pre_compact.Consolidator", return_value=mock_consolidator), \
             patch("hooks.pre_compact.ManifestGenerator", return_value=mock_manifest), \
             patch("hooks.pre_compact.FeedbackLoop", return_value=mock_feedback), \
             patch("hooks.pre_compact.SelfReflector"), \
             patch("hooks.pre_compact.Crystallizer") as MockCryst, \
             patch("hooks.pre_compact.build_threads", return_value=[]), \
             patch("hooks.pre_compact.RelevanceEngine") as MockRel, \
             patch("hooks.pre_compact.embed_for_memory", return_value=None):
            MockRel.return_value.run_maintenance.return_value = {"archived": [], "rehydrated": []}
            MockCryst.return_value.crystallize_candidates.return_value = []
            pre_compact_main()

        close_db()
        call_args = mock_consolidator.consolidate_session.call_args
        assert call_args[0][1] == "unknown"


# ---------------------------------------------------------------------------
# Tests: usage tracking wired into PreCompact
# ---------------------------------------------------------------------------


class TestPreCompactUsageTracking:
    def test_track_usage_called_when_injections_exist(self, tmp_path, monkeypatch, capsys):
        """track_usage should be called with injected IDs and ephemeral content."""
        session_id = "sess-usage-track"
        monkeypatch.setenv("CLAUDE_SESSION_ID", session_id)
        monkeypatch.chdir(tmp_path)

        base_dir = init_db(base_dir=str(tmp_path))
        mem_id = _make_memory("consolidated", "Rubocop Rule", "Always use rubocop")
        _record_injection(mem_id, session_id)
        _write_ephemeral_today(base_dir, "## Obs\n- Used rubocop for linting today\n")

        mock_consolidator = MagicMock(spec=Consolidator)
        mock_consolidator.consolidate_session.return_value = {
            "kept": [], "pruned": [], "promoted": [], "conflicts": []
        }
        mock_feedback = MagicMock(spec=FeedbackLoop)
        mock_manifest = MagicMock(spec=ManifestGenerator)

        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        with patch("hooks.pre_compact.init_db", return_value=base_dir), \
             patch("hooks.pre_compact.LifecycleManager"), \
             patch("hooks.pre_compact.Consolidator", return_value=mock_consolidator), \
             patch("hooks.pre_compact.ManifestGenerator", return_value=mock_manifest), \
             patch("hooks.pre_compact.FeedbackLoop", return_value=mock_feedback), \
             patch("hooks.pre_compact.SelfReflector"), \
             patch("hooks.pre_compact.Crystallizer") as MockCryst, \
             patch("hooks.pre_compact.build_threads", return_value=[]), \
             patch("hooks.pre_compact.RelevanceEngine") as MockRel, \
             patch("hooks.pre_compact.embed_for_memory", return_value=None):
            MockRel.return_value.run_maintenance.return_value = {"archived": [], "rehydrated": []}
            MockCryst.return_value.crystallize_candidates.return_value = []
            pre_compact_main()

        close_db()
        mock_feedback.track_usage.assert_called_once()
        call_args = mock_feedback.track_usage.call_args
        assert call_args[0][0] == session_id
        assert mem_id in call_args[0][1]
        assert "rubocop" in call_args[0][2].lower()

    def test_track_usage_not_called_when_no_injections(self, tmp_path, monkeypatch, capsys):
        """track_usage should be skipped when no memories were injected this session."""
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-no-inject")
        monkeypatch.chdir(tmp_path)

        base_dir = init_db(base_dir=str(tmp_path))
        _write_ephemeral_today(base_dir, "## Obs\n- Some observation\n")

        mock_consolidator = MagicMock(spec=Consolidator)
        mock_consolidator.consolidate_session.return_value = {
            "kept": [], "pruned": [], "promoted": [], "conflicts": []
        }
        mock_feedback = MagicMock(spec=FeedbackLoop)
        mock_manifest = MagicMock(spec=ManifestGenerator)

        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        with patch("hooks.pre_compact.init_db", return_value=base_dir), \
             patch("hooks.pre_compact.LifecycleManager"), \
             patch("hooks.pre_compact.Consolidator", return_value=mock_consolidator), \
             patch("hooks.pre_compact.ManifestGenerator", return_value=mock_manifest), \
             patch("hooks.pre_compact.FeedbackLoop", return_value=mock_feedback), \
             patch("hooks.pre_compact.SelfReflector"), \
             patch("hooks.pre_compact.Crystallizer") as MockCryst, \
             patch("hooks.pre_compact.build_threads", return_value=[]), \
             patch("hooks.pre_compact.RelevanceEngine") as MockRel, \
             patch("hooks.pre_compact.embed_for_memory", return_value=None):
            MockRel.return_value.run_maintenance.return_value = {"archived": [], "rehydrated": []}
            MockCryst.return_value.crystallize_candidates.return_value = []
            pre_compact_main()

        close_db()
        mock_feedback.track_usage.assert_not_called()

    def test_track_usage_includes_conversation_text(self, tmp_path, monkeypatch, capsys):
        """When stdin has conversation content, it's included in usage text."""
        session_id = "sess-convo"
        monkeypatch.setenv("CLAUDE_SESSION_ID", session_id)
        monkeypatch.chdir(tmp_path)

        base_dir = init_db(base_dir=str(tmp_path))
        mem_id = _make_memory("instinctive", "Self-Model Check", "Check self-model")
        _record_injection(mem_id, session_id)
        _write_ephemeral_today(base_dir, "## Obs\n- Minimal obs\n")

        mock_consolidator = MagicMock(spec=Consolidator)
        mock_consolidator.consolidate_session.return_value = {
            "kept": [], "pruned": [], "promoted": [], "conflicts": []
        }
        mock_feedback = MagicMock(spec=FeedbackLoop)
        mock_manifest = MagicMock(spec=ManifestGenerator)

        import io
        convo = "I checked my self-model tendencies before starting the task."
        monkeypatch.setattr("sys.stdin", io.StringIO(convo))

        with patch("hooks.pre_compact.init_db", return_value=base_dir), \
             patch("hooks.pre_compact.LifecycleManager"), \
             patch("hooks.pre_compact.Consolidator", return_value=mock_consolidator), \
             patch("hooks.pre_compact.ManifestGenerator", return_value=mock_manifest), \
             patch("hooks.pre_compact.FeedbackLoop", return_value=mock_feedback), \
             patch("hooks.pre_compact.SelfReflector"), \
             patch("hooks.pre_compact.Crystallizer") as MockCryst, \
             patch("hooks.pre_compact.build_threads", return_value=[]), \
             patch("hooks.pre_compact.RelevanceEngine") as MockRel, \
             patch("hooks.pre_compact.embed_for_memory", return_value=None):
            MockRel.return_value.run_maintenance.return_value = {"archived": [], "rehydrated": []}
            MockCryst.return_value.crystallize_candidates.return_value = []
            pre_compact_main()

        close_db()
        call_args = mock_feedback.track_usage.call_args
        usage_text = call_args[0][2]
        assert "self-model" in usage_text.lower()


# ---------------------------------------------------------------------------
# Tests: promotion candidates are executed
# ---------------------------------------------------------------------------


class TestPreCompactCrystallization:
    def test_crystallizer_is_called(self, tmp_path, monkeypatch):
        """Pre-compact runs crystallization instead of direct promotion."""
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-crystallize")
        monkeypatch.chdir(tmp_path)

        base_dir = init_db(base_dir=str(tmp_path))
        _write_ephemeral_today(base_dir, "## Obs\n- some fact\n")

        mock_consolidator = MagicMock(spec=Consolidator)
        mock_consolidator.consolidate_session.return_value = {
            "kept": ["x"], "pruned": [], "promoted": [], "conflicts": []
        }
        mock_feedback = MagicMock(spec=FeedbackLoop)
        mock_crystallizer = MagicMock()
        mock_crystallizer.crystallize_candidates.return_value = [
            {"crystallized_id": "c1", "source_ids": ["s1", "s2"], "title": "Pattern", "group_size": 2}
        ]
        mock_manifest = MagicMock(spec=ManifestGenerator)

        with patch("hooks.pre_compact.init_db", return_value=base_dir), \
             patch("hooks.pre_compact.LifecycleManager"), \
             patch("hooks.pre_compact.Consolidator", return_value=mock_consolidator), \
             patch("hooks.pre_compact.Crystallizer", return_value=mock_crystallizer), \
             patch("hooks.pre_compact.ManifestGenerator", return_value=mock_manifest), \
             patch("hooks.pre_compact.FeedbackLoop", return_value=mock_feedback), \
             patch("hooks.pre_compact.SelfReflector"), \
             patch("hooks.pre_compact.build_threads", return_value=[]), \
             patch("hooks.pre_compact.RelevanceEngine") as MockRel, \
             patch("hooks.pre_compact.embed_for_memory", return_value=None):
            MockRel.return_value.run_maintenance.return_value = {"archived": [], "rehydrated": []}
            pre_compact_main()

        close_db()
        mock_crystallizer.crystallize_candidates.assert_called_once()

    def test_crystallizer_error_does_not_crash(self, tmp_path, monkeypatch):
        """Crystallizer errors should not crash the hook."""
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-crystal-err")
        monkeypatch.chdir(tmp_path)

        base_dir = init_db(base_dir=str(tmp_path))
        _write_ephemeral_today(base_dir, "## Note\n- detail\n")

        mock_consolidator = MagicMock(spec=Consolidator)
        mock_consolidator.consolidate_session.return_value = {
            "kept": [], "pruned": [], "promoted": [], "conflicts": []
        }
        mock_feedback = MagicMock(spec=FeedbackLoop)
        mock_crystallizer = MagicMock()
        mock_crystallizer.crystallize_candidates.side_effect = Exception("LLM down")
        mock_manifest = MagicMock(spec=ManifestGenerator)

        with patch("hooks.pre_compact.init_db", return_value=base_dir), \
             patch("hooks.pre_compact.LifecycleManager"), \
             patch("hooks.pre_compact.Consolidator", return_value=mock_consolidator), \
             patch("hooks.pre_compact.Crystallizer", return_value=mock_crystallizer), \
             patch("hooks.pre_compact.ManifestGenerator", return_value=mock_manifest), \
             patch("hooks.pre_compact.FeedbackLoop", return_value=mock_feedback), \
             patch("hooks.pre_compact.SelfReflector"), \
             patch("hooks.pre_compact.build_threads", return_value=[]), \
             patch("hooks.pre_compact.RelevanceEngine") as MockRel, \
             patch("hooks.pre_compact.embed_for_memory", return_value=None):
            MockRel.return_value.run_maintenance.return_value = {"archived": [], "rehydrated": []}
            pre_compact_main()

        close_db()
        mock_manifest.write_manifest.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: exception safety
# ---------------------------------------------------------------------------


class TestPreCompactExceptionSafety:
    def test_unexpected_exception_does_not_crash(self, tmp_path, monkeypatch, capsys):
        """Any unexpected exception must be caught; stdout remains empty."""
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-crash")
        monkeypatch.chdir(tmp_path)

        with patch("hooks.pre_compact.init_db", side_effect=RuntimeError("DB exploded")):
            pre_compact_main()

        out, err = capsys.readouterr()
        assert out.strip() == ""
        assert "PreCompact error" in err
        assert "DB exploded" in err

    def test_stdout_empty_on_exception(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-crash2")
        monkeypatch.chdir(tmp_path)

        with patch("hooks.pre_compact.init_db", side_effect=Exception("boom")):
            pre_compact_main()

        out, _ = capsys.readouterr()
        assert out.strip() == ""
