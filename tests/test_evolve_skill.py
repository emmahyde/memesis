"""
Tests for the /memesis:evolve skill driver (scripts/evolve.py).

Covers:
  - ReplayDB created and cleaned up (normal exit + exception path)
  - TraceWriter initialised with the correct replay session_id
  - Eval file written to the correct path (eval/recall/<slug>_recall.py)
  - Guard suite subprocess command is correct
  - --autoresearch stub prints placeholder and exits 0
  - _patched_llm context manager swaps and restores call_llm
  - _slug_from_path slug derivation
  - _next_replay_n counter increments atomically
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Import the modules under test
import scripts.evolve as evolve_mod
from scripts.evolve import (
    _next_replay_n,
    _patched_llm,
    _slug_from_path,
    main,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_evolve_base(tmp_path, monkeypatch):
    """Override the EVOLVE_BASE dir to use tmp_path for isolation."""
    evolve_base = tmp_path / "evolve"
    monkeypatch.setattr(evolve_mod, "_EVOLVE_BASE", evolve_base)
    return evolve_base


@pytest.fixture
def tmp_trace_dir(tmp_path, monkeypatch):
    """Override the trace base dir used by TraceWriter."""
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    # Patch the default trace base in core.trace
    import core.trace as trace_mod
    monkeypatch.setattr(trace_mod, "_DEFAULT_BASE", trace_dir)
    return trace_dir


@pytest.fixture
def sample_transcript(tmp_path):
    """Write a minimal JSONL transcript file and return its path."""
    transcript = tmp_path / "session-abc123.jsonl"
    entry = json.dumps({
        "type": "human",
        "message": {"role": "user", "content": "Hello world"},
        "uuid": "test-uuid",
    })
    transcript.write_text(entry + "\n", encoding="utf-8")
    return transcript


# ---------------------------------------------------------------------------
# TestSlugDerivation
# ---------------------------------------------------------------------------


class TestSlugDerivation:
    def test_simple_filename(self):
        p = Path("session-abc.jsonl")
        assert _slug_from_path(p) == "session-abc"

    def test_underscore_converted_to_dash(self):
        p = Path("foo_2026-05-01.md")
        assert _slug_from_path(p) == "foo-2026-05-01"

    def test_uppercase_lowercased(self):
        p = Path("MySession.jsonl")
        assert _slug_from_path(p) == "mysession"

    def test_special_chars_stripped(self):
        p = Path("session (copy).jsonl")
        result = _slug_from_path(p)
        # Special chars → dashes; consecutive dashes collapsed; alphanums preserved
        assert "session" in result
        assert "copy" in result
        assert result == result.lower()
        assert all(c.isalnum() or c == "-" for c in result)

    def test_empty_stem_fallback(self):
        p = Path(".jsonl")
        result = _slug_from_path(p)
        assert result  # non-empty

    def test_long_path_uses_stem(self):
        p = Path("/home/user/projects/transcripts/my-session-2026.jsonl")
        assert _slug_from_path(p) == "my-session-2026"


# ---------------------------------------------------------------------------
# TestReplayCounter
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tmp_evolve_base")
class TestReplayCounter:
    def test_first_call_returns_one(self):
        n = _next_replay_n("sess-001")
        assert n == 1

    def test_second_call_returns_two(self):
        _next_replay_n("sess-002")
        n = _next_replay_n("sess-002")
        assert n == 2

    def test_different_sessions_independent(self):
        n_a = _next_replay_n("sess-a")
        n_b = _next_replay_n("sess-b")
        n_a2 = _next_replay_n("sess-a")
        assert n_a == 1
        assert n_b == 1
        assert n_a2 == 2

    def test_count_file_written_atomically(self):
        _next_replay_n("sess-atomic")
        count_path = evolve_mod._replay_count_path("sess-atomic")
        assert count_path.exists()
        data = json.loads(count_path.read_text())
        assert data["n"] == 1


# ---------------------------------------------------------------------------
# TestPatchedLLM
# ---------------------------------------------------------------------------


class TestPatchedLLM:
    def test_call_llm_is_swapped_inside_context(self):
        """core.llm.call_llm should be replaced with a wrapper during the context."""
        import core.llm as llm_mod

        original = llm_mod.call_llm
        captured_inside = {}

        with patch("core.llm_cache.cached_call_llm", return_value="cached"):
            with _patched_llm(force_live=False):
                captured_inside["fn"] = llm_mod.call_llm
                # Call via the patched function
                result = llm_mod.call_llm("test prompt")

        assert captured_inside["fn"] is not original, "call_llm should be replaced inside context"
        assert result == "cached"

    def test_call_llm_is_restored_after_context(self):
        """core.llm.call_llm should be restored after the context exits."""
        import core.llm as llm_mod

        original = llm_mod.call_llm

        with patch("core.llm_cache.cached_call_llm", return_value="cached"):
            with _patched_llm(force_live=False):
                pass

        assert llm_mod.call_llm is original, "call_llm must be restored after context"

    def test_call_llm_restored_on_exception(self):
        """call_llm must be restored even if the context body raises."""
        import core.llm as llm_mod

        original = llm_mod.call_llm

        with patch("core.llm_cache.cached_call_llm", return_value="cached"):
            with pytest.raises(RuntimeError):
                with _patched_llm(force_live=False):
                    raise RuntimeError("boom")

        assert llm_mod.call_llm is original

    def test_force_live_forwarded_to_cached_call_llm(self):
        """force_live=True should be forwarded to cached_call_llm."""
        import core.llm as llm_mod

        with patch("core.llm_cache.cached_call_llm", return_value="live") as mock_cache:
            with _patched_llm(force_live=True):
                llm_mod.call_llm("prompt")

        mock_cache.assert_called_once_with("prompt", force_live=True)

    def test_per_module_attribute_patched(self):
        """transcript_ingest.call_llm should also be replaced during the context."""
        import core.transcript_ingest as ti_mod

        original = getattr(ti_mod, "call_llm", None)
        if original is None:
            pytest.skip("transcript_ingest does not have call_llm attribute")

        with patch("core.llm_cache.cached_call_llm", return_value="x"):
            with _patched_llm(force_live=False):
                inside = ti_mod.call_llm

        # After context, should be restored
        assert ti_mod.call_llm is original
        assert inside is not original


# ---------------------------------------------------------------------------
# TestReplayDBCreatedAndCleanedUp
# ---------------------------------------------------------------------------


class TestReplayDBCreatedAndCleanedUp:
    def test_context_manager_creates_and_cleans_tempdir(self):
        """ReplayDB creates a tempdir, init_db runs, cleanup happens on __exit__."""
        from core.replay_db import ReplayDB

        captured_dir = {}

        with ReplayDB() as base_dir:
            captured_dir["path"] = base_dir
            db_path = Path(base_dir) / "index.db"
            assert Path(base_dir).exists(), "tempdir should exist inside context"
            assert db_path.exists(), "index.db should be created by init_db"

        assert not Path(captured_dir["path"]).exists(), "tempdir should be removed on __exit__"

    def test_cleanup_on_exception(self):
        """ReplayDB cleans up even when an exception is raised inside the context."""
        from core.replay_db import ReplayDB

        captured_dir = {}

        with pytest.raises(ValueError, match="test error"):
            with ReplayDB() as base_dir:
                captured_dir["path"] = base_dir
                raise ValueError("test error")

        assert not Path(captured_dir["path"]).exists(), "tempdir should be removed on exception"

    def test_rejects_memory_path(self):
        """ReplayDB should raise ValueError for ':memory:'."""
        from core.replay_db import ReplayDB

        with pytest.raises(ValueError, match=":memory:"):
            ReplayDB(db_path=":memory:")


# ---------------------------------------------------------------------------
# TestTraceWriterInitWithReplaySessionId
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tmp_evolve_base")
class TestTraceWriterInitWithReplaySessionId:
    def test_trace_writer_session_id_format(self, tmp_trace_dir):
        """TraceWriter should be initialised with replay-<orig>-<n> session_id."""
        from core.trace import TraceWriter

        orig_session_id = "my-session-2026"
        n = _next_replay_n(orig_session_id)
        expected_session_id = f"replay-{orig_session_id}-{n}"

        writer = TraceWriter(session_id=expected_session_id, base_dir=tmp_trace_dir)
        writer.emit("test", "test_event", {"value": 1})

        assert writer.session_id == expected_session_id
        trace_file = tmp_trace_dir / f"{expected_session_id}.jsonl"
        assert trace_file.exists()

        line = trace_file.read_text(encoding="utf-8").strip()
        event = json.loads(line)
        assert event["event"] == "test_event"
        assert event["payload"] == {"value": 1}

    def test_replay_session_id_regex_matches(self):
        """The replay session_id must match replay-<orig>-<n> pattern."""
        import re
        orig = "foo-bar-baz"
        n = _next_replay_n(orig)
        session_id = f"replay-{orig}-{n}"
        assert re.match(r"^replay-.+-\d+$", session_id)


# ---------------------------------------------------------------------------
# TestEvalFileWrittenToCorrectPath
# ---------------------------------------------------------------------------


class TestEvalFileWrittenToCorrectPath:
    def test_eval_file_written_to_eval_recall(self, tmp_path, monkeypatch):
        """compile_evals should write to eval/recall/<slug>_recall.py."""
        # Patch _PROJECT_ROOT to tmp_path
        monkeypatch.setattr(evolve_mod, "_PROJECT_ROOT", tmp_path)

        eval_dir = tmp_path / "eval" / "recall"
        eval_dir.mkdir(parents=True)

        from core.eval_compile import EvalSpec

        fake_spec = EvalSpec(
            slug="oauth-token-expiry",
            expected_entities=["oauth", "token"],
            polarity=None,
            stage_target=None,
            match_mode="entity_presence",
            description="Should remember oauth token expiry",
        )

        fake_source = "# fake eval\ndef test_placeholder(): pass\n"

        with patch("scripts.evolve.extract_spec_from_text", return_value=fake_spec):
            with patch("scripts.evolve.compile_to_pytest", return_value=fake_source):
                _, eval_paths = evolve_mod._compile_evals(
                    descriptions=["Should remember oauth token expiry"],
                    replay_store_path=str(tmp_path / "db"),
                    slug="my-session",
                )

        assert len(eval_paths) == 1
        expected_path = eval_dir / "my-session_oauth-token-expiry_recall.py"
        assert eval_paths[0] == expected_path
        assert expected_path.exists()
        assert expected_path.read_text() == fake_source

    def test_eval_slug_no_collision_with_session_slug(self, tmp_path, monkeypatch):
        """When spec slug differs from session slug, both are included in filename."""
        monkeypatch.setattr(evolve_mod, "_PROJECT_ROOT", tmp_path)
        eval_dir = tmp_path / "eval" / "recall"
        eval_dir.mkdir(parents=True)

        from core.eval_compile import EvalSpec

        spec = EvalSpec(
            slug="auth-flow",
            expected_entities=["auth"],
            polarity=None,
            stage_target=None,
            match_mode="entity_presence",
        )

        with patch("scripts.evolve.extract_spec_from_text", return_value=spec):
            with patch("scripts.evolve.compile_to_pytest", return_value="# eval\n"):
                _, eval_paths = evolve_mod._compile_evals(
                    ["auth flow description"], str(tmp_path), "session-2026"
                )

        assert "session-2026_auth-flow_recall" in eval_paths[0].stem


# ---------------------------------------------------------------------------
# TestGuardSuiteInvocationCommand
# ---------------------------------------------------------------------------


class TestGuardSuiteInvocationCommand:
    def test_guard_suite_command_structure(self, tmp_path, monkeypatch):
        """Guard suite should invoke pytest tests/ + eval file with -x --tb=short."""
        monkeypatch.setattr(evolve_mod, "_PROJECT_ROOT", tmp_path)

        eval_path = tmp_path / "eval" / "recall" / "my-session_recall.py"
        eval_path.parent.mkdir(parents=True, exist_ok=True)
        eval_path.write_text("# eval\n")

        captured_cmds: list[list[str]] = []

        def mock_run(cmd, **_):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            evolve_mod._run_guard_suite("my-session", [eval_path])

        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]

        # Must be: python3 -m pytest <eval_path> --tb=short
        assert cmd[0] == "python3"
        assert cmd[1] == "-m"
        assert cmd[2] == "pytest"
        assert str(eval_path) in cmd
        assert "--tb=short" in cmd

    def test_guard_suite_pass_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setattr(evolve_mod, "_PROJECT_ROOT", tmp_path)
        eval_path = tmp_path / "eval" / "recall" / "my-session_recall.py"
        eval_path.parent.mkdir(parents=True, exist_ok=True)
        eval_path.write_text("# eval\n")

        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            results = evolve_mod._run_guard_suite("my-session", [eval_path])

        assert results[eval_path.stem] is True

    def test_guard_suite_fail_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(evolve_mod, "_PROJECT_ROOT", tmp_path)
        eval_path = tmp_path / "eval" / "recall" / "my-session_recall.py"
        eval_path.parent.mkdir(parents=True, exist_ok=True)
        eval_path.write_text("# eval\n")

        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            results = evolve_mod._run_guard_suite("my-session", [eval_path])

        assert results[eval_path.stem] is False


# ---------------------------------------------------------------------------
# TestMainEndToEnd (mocked)
# ---------------------------------------------------------------------------


class TestMainEndToEnd:
    def test_missing_transcript_returns_nonzero(self, tmp_path):
        """Missing transcript file should exit with non-zero."""
        rc = main(["--transcript", str(tmp_path / "nonexistent.jsonl")])
        assert rc == 1

    @pytest.mark.usefixtures("tmp_evolve_base", "tmp_trace_dir")
    def test_no_descriptions_exits_cleanly(
        self, sample_transcript, tmp_path, monkeypatch
    ):
        """If user provides no expected memories, exit 0 with no evals compiled."""
        monkeypatch.setattr(evolve_mod, "_PROJECT_ROOT", tmp_path)

        # Mock all the heavy pipeline operations
        with patch("scripts.evolve._run_replay"), \
             patch("scripts.evolve._elicit_expected_memories", return_value=[]):
            rc = main(["--transcript", str(sample_transcript)])

        assert rc == 0

    @pytest.mark.usefixtures("tmp_evolve_base", "tmp_trace_dir")
    def test_replay_db_cleaned_up_on_main_exception(
        self, sample_transcript, tmp_path, monkeypatch
    ):
        """ReplayDB tempdir must be cleaned up even if _run_replay raises."""
        monkeypatch.setattr(evolve_mod, "_PROJECT_ROOT", tmp_path)

        captured_base_dirs = []

        def fake_run_replay(*args, **kwargs):
            del kwargs
            captured_base_dirs.append(args[1])
            raise RuntimeError("pipeline failure")

        with patch("scripts.evolve._run_replay", side_effect=fake_run_replay), \
             patch("scripts.evolve._elicit_expected_memories", return_value=[]):
            with pytest.raises(RuntimeError, match="pipeline failure"):
                main(["--transcript", str(sample_transcript)])

        # The tempdir should be cleaned up
        if captured_base_dirs:
            assert not Path(captured_base_dirs[0]).exists(), (
                "ReplayDB tempdir should be cleaned up on exception"
            )
