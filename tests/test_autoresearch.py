"""
tests/test_autoresearch.py — Unit tests for core.autoresearch.Autoresearcher.

Coverage:
    - guard failure → discard
    - token budget exhaustion → mid-iteration halt
    - out-of-surface mutation → ValueError
    - atomic write on keep
    - iteration count tracking
    - config load/save round-trip
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.autoresearch import Autoresearcher, _load_yaml, _dump_yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(tmp_path: Path, config: dict | None = None) -> Path:
    """Create a minimal session directory with autoresearch.yaml."""
    session = tmp_path / "evolve" / "test-session"
    session.mkdir(parents=True)
    cfg = config or {"max_iterations": 10, "token_budget": 100000, "iteration_count": 0, "token_spend": 0}
    _dump_yaml(cfg, session / "autoresearch.yaml")
    return session


def _make_researcher(
    session: Path,
    *,
    token_counter=None,
    project_root: Path | None = None,
) -> Autoresearcher:
    return Autoresearcher(
        session_path=session,
        eval_slug="test-eval",
        token_counter=token_counter or (lambda: 0),
        project_root=project_root,
    )


# ---------------------------------------------------------------------------
# TestConfigLoad
# ---------------------------------------------------------------------------

class TestConfigLoad:
    def test_creates_defaults_when_absent(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        r = _make_researcher(session)
        cfg = r._load_config()
        assert cfg["max_iterations"] == 10
        assert cfg["token_budget"] == 0
        assert cfg["iteration_count"] == 0
        assert cfg["token_spend"] == 0
        # File was created
        assert (session / "autoresearch.yaml").exists()

    def test_reads_existing_config(self, tmp_path):
        session = _make_session(tmp_path, {"max_iterations": 5, "token_budget": 9999, "iteration_count": 2, "token_spend": 100})
        r = _make_researcher(session)
        cfg = r._load_config()
        assert cfg["max_iterations"] == 5
        assert cfg["token_budget"] == 9999
        assert cfg["iteration_count"] == 2
        assert cfg["token_spend"] == 100

    def test_fills_missing_keys(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        _dump_yaml({"max_iterations": 3}, session / "autoresearch.yaml")
        r = _make_researcher(session)
        cfg = r._load_config()
        assert cfg["token_budget"] == 0
        assert cfg["iteration_count"] == 0
        assert cfg["token_spend"] == 0

    def test_save_config_round_trip(self, tmp_path):
        session = _make_session(tmp_path)
        r = _make_researcher(session)
        r._config = r._load_config()
        r._config["iteration_count"] = 3
        r._config["token_spend"] = 500
        r._save_config(r._config)
        reloaded = _load_yaml(session / "autoresearch.yaml")
        assert int(reloaded["iteration_count"]) == 3
        assert int(reloaded["token_spend"]) == 500


# ---------------------------------------------------------------------------
# TestMutationSurface
# ---------------------------------------------------------------------------

class TestMutationSurface:
    def test_out_of_surface_raises_value_error(self, tmp_path):
        session = _make_session(tmp_path)
        r = _make_researcher(session)
        outside_file = tmp_path / "core" / "database.py"
        outside_file.parent.mkdir(parents=True, exist_ok=True)
        outside_file.write_text("# not in surface")
        with pytest.raises(ValueError, match="outside the D-14 mutation surface"):
            r._validate_mutation_target(outside_file)

    def test_in_surface_does_not_raise(self, tmp_path):
        session = _make_session(tmp_path)
        # Use the real project root so the surface files exist
        from core.autoresearch import _PROJECT_ROOT
        r = Autoresearcher(session_path=session, eval_slug="x", token_counter=lambda: 0)
        # prompts.py is in the surface
        prompts = _PROJECT_ROOT / "core" / "prompts.py"
        if prompts.exists():
            resolved = r._validate_mutation_target(prompts)
            assert resolved == prompts.resolve()

    def test_apply_mutation_out_of_surface_raises(self, tmp_path):
        session = _make_session(tmp_path)
        r = _make_researcher(session)
        outside = tmp_path / "outside.py"
        outside.write_text("x = 1")
        with pytest.raises(ValueError, match="outside the D-14 mutation surface"):
            r._apply_mutation(outside, "x = 2")

    def test_keep_out_of_surface_raises(self, tmp_path):
        session = _make_session(tmp_path)
        r = _make_researcher(session)
        outside = tmp_path / "outside.py"
        outside.write_text("x = 1")
        with pytest.raises(ValueError, match="outside the D-14 mutation surface"):
            r._keep(outside, "x = 2")


# ---------------------------------------------------------------------------
# TestAtomicWrite
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_keep_writes_new_content(self, tmp_path):
        """_keep() writes new content to a surface file atomically."""
        session = _make_session(tmp_path)

        # Use a custom project_root so we can place surface files under tmp_path
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        target = project_root / "core" / "prompts.py"
        target.write_text("# original")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 0,
            project_root=project_root,
        )
        r._keep(target, "# mutated content")
        assert target.read_text() == "# mutated content"

    def test_keep_is_atomic_no_tmp_leftover(self, tmp_path):
        """After _keep(), no .tmp files remain next to the target."""
        session = _make_session(tmp_path)
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        target = project_root / "core" / "crystallizer.py"
        target.write_text("# original")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 0,
            project_root=project_root,
        )
        r._keep(target, "# new content")
        tmp_files = list((project_root / "core").glob("*.tmp"))
        assert tmp_files == [], f"Unexpected tmp files: {tmp_files}"
        assert target.read_text() == "# new content"


# ---------------------------------------------------------------------------
# TestGuardSuite
# ---------------------------------------------------------------------------

class TestGuardSuite:
    def test_guard_pass_returns_true(self, tmp_path):
        session = _make_session(tmp_path)
        r = _make_researcher(session)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="passed", stderr="")
            result = r._run_guard_suite()
        assert result is True

    def test_unit_suite_failure_returns_false(self, tmp_path):
        session = _make_session(tmp_path)
        r = _make_researcher(session)

        call_count = 0

        def side_effect(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            # First call (full unit suite) fails
            rc = 1 if call_count == 1 else 0
            return MagicMock(returncode=rc, stdout="FAILED", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            result = r._run_guard_suite()
        assert result is False

    def test_tier3_failure_returns_false(self, tmp_path):
        """Tier-3 classes are part of the full unit suite; a unit suite failure
        (which would include tier-3 failures) causes _run_guard_suite to return False."""
        session = _make_session(tmp_path)
        r = _make_researcher(session)

        def side_effect(*_args, **_kwargs):
            # Unit suite fails (simulating a tier-3 class failure within it)
            return MagicMock(returncode=1, stdout="FAILED tier-3", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            result = r._run_guard_suite()
        assert result is False

    def test_eval_recall_failure_returns_false(self, tmp_path):
        session = _make_session(tmp_path)

        # Use a custom project_root so we don't pollute the real project
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        (project_root / "eval" / "recall").mkdir(parents=True)
        (project_root / "eval" / "recall" / "session_test_recall.py").write_text("# stub")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 0,
            project_root=project_root,
        )

        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            # Unit suite (tests/) passes; eval/recall invocation fails.
            # Distinguish by checking whether the eval/recall dir path appears in cmd.
            cmd_str = " ".join(str(a) for a in cmd)
            if "eval" in cmd_str and "recall" in cmd_str:
                return MagicMock(returncode=1, stdout="RECALL FAILED", stderr="")
            return MagicMock(returncode=0, stdout="passed", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            result = r._run_guard_suite()
        assert result is False


# ---------------------------------------------------------------------------
# TestGuardFailureDiscard
# ---------------------------------------------------------------------------

class TestGuardFailureDiscard:
    def test_guard_failure_triggers_discard(self, tmp_path):
        """When guard suite fails, discard() is called (git checkout --)."""
        session = _make_session(tmp_path, {
            "max_iterations": 2,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        })
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        # Create all 5 surface files
        for fname in ["prompts.py", "issue_cards.py", "rule_registry.py", "consolidator.py", "crystallizer.py"]:
            (project_root / "core" / fname).write_text(f"# {fname}")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 0,
            project_root=project_root,
        )

        discard_calls = []

        def mock_discard(target_file):
            discard_calls.append(target_file)

        def mock_propose(target_file):
            return "# mutated"

        def mock_apply(target_file, content):
            pass  # no-op

        # Guard always fails
        def mock_guard():
            return False

        r._propose_mutation = mock_propose
        r._apply_mutation = mock_apply
        r._run_guard_suite = mock_guard
        r._discard = mock_discard

        result = r.run()
        assert result.mutations_discarded == 2
        assert result.mutations_kept == 0
        assert len(discard_calls) == 2

    def test_guard_pass_calls_keep_not_discard(self, tmp_path):
        """When guard suite passes, keep() is called; discard() is not."""
        session = _make_session(tmp_path, {
            "max_iterations": 1,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        })
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        for fname in ["prompts.py", "issue_cards.py", "rule_registry.py", "consolidator.py", "crystallizer.py"]:
            (project_root / "core" / fname).write_text(f"# {fname}")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 0,
            project_root=project_root,
        )

        kept_calls = []
        discarded_calls = []

        def mock_keep(target_file, content):
            kept_calls.append(target_file)

        def mock_discard(target_file):
            discarded_calls.append(target_file)

        r._propose_mutation = lambda f: "# mutated"
        r._apply_mutation = lambda f, c: None
        r._run_guard_suite = lambda: True
        r._keep = mock_keep
        r._discard = mock_discard

        result = r.run()
        assert result.mutations_kept == 1
        assert len(kept_calls) == 1
        assert len(discarded_calls) == 0


# ---------------------------------------------------------------------------
# TestTokenBudgetHalt
# ---------------------------------------------------------------------------

class TestTokenBudgetHalt:
    def test_token_budget_exhaustion_halts_mid_iteration(self, tmp_path):
        """When token_budget is exceeded, run() halts before the next iteration."""
        session = _make_session(tmp_path, {
            "max_iterations": 10,
            "token_budget": 100,
            "iteration_count": 0,
            "token_spend": 0,
        })
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        for fname in ["prompts.py", "issue_cards.py", "rule_registry.py", "consolidator.py", "crystallizer.py"]:
            (project_root / "core" / fname).write_text(f"# {fname}")

        # Token counter returns 200 on first call — immediately over budget
        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 200,
            project_root=project_root,
        )

        iteration_calls = []

        def mock_propose(f):
            iteration_calls.append(f)
            return "# mutated"

        r._propose_mutation = mock_propose
        r._apply_mutation = lambda f, c: None
        r._run_guard_suite = lambda: True
        r._keep = lambda f, c: None
        r._discard = lambda f: None

        result = r.run()
        assert result.halt_reason == "token_budget"
        # Should halt before any iteration starts
        assert len(iteration_calls) == 0
        assert result.iterations_completed == 0

    def test_token_budget_zero_means_unlimited(self, tmp_path):
        """token_budget=0 means unlimited — does not halt on tokens."""
        session = _make_session(tmp_path, {
            "max_iterations": 2,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        })
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        for fname in ["prompts.py", "issue_cards.py", "rule_registry.py", "consolidator.py", "crystallizer.py"]:
            (project_root / "core" / fname).write_text(f"# {fname}")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 999999,  # huge token count — should not halt
            project_root=project_root,
        )
        r._propose_mutation = lambda f: "# mutated"
        r._apply_mutation = lambda f, c: None
        r._run_guard_suite = lambda: True
        r._keep = lambda f, c: None
        r._discard = lambda f: None

        result = r.run()
        # With token_budget=0, should complete all 2 iterations
        assert result.iterations_completed == 2
        assert result.halt_reason == "iteration_cap"

    def test_token_budget_exceeded_after_first_iteration(self, tmp_path):
        """Budget exceeded after first iteration: second iteration halts."""
        call_count = [0]

        def token_counter():
            # Returns 0 on first call (before iter 1), 200 on second (before iter 2)
            call_count[0] += 1
            if call_count[0] <= 1:
                return 0
            return 200

        session = _make_session(tmp_path, {
            "max_iterations": 5,
            "token_budget": 100,
            "iteration_count": 0,
            "token_spend": 0,
        })
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        for fname in ["prompts.py", "issue_cards.py", "rule_registry.py", "consolidator.py", "crystallizer.py"]:
            (project_root / "core" / fname).write_text(f"# {fname}")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=token_counter,
            project_root=project_root,
        )
        r._propose_mutation = lambda f: "# mutated"
        r._apply_mutation = lambda f, c: None
        r._run_guard_suite = lambda: True
        r._keep = lambda f, c: None
        r._discard = lambda f: None

        result = r.run()
        assert result.iterations_completed == 1
        assert result.halt_reason == "token_budget"


# ---------------------------------------------------------------------------
# TestIterationCapHalt
# ---------------------------------------------------------------------------

class TestIterationCapHalt:
    def test_iteration_cap_respected(self, tmp_path):
        """run() completes exactly max_iterations iterations then halts."""
        session = _make_session(tmp_path, {
            "max_iterations": 3,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        })
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        for fname in ["prompts.py", "issue_cards.py", "rule_registry.py", "consolidator.py", "crystallizer.py"]:
            (project_root / "core" / fname).write_text(f"# {fname}")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 0,
            project_root=project_root,
        )
        r._propose_mutation = lambda f: "# mutated"
        r._apply_mutation = lambda f, c: None
        r._run_guard_suite = lambda: True
        r._keep = lambda f, c: None
        r._discard = lambda f: None

        result = r.run()
        assert result.iterations_completed == 3
        assert result.halt_reason == "iteration_cap"

    def test_iteration_count_written_to_yaml(self, tmp_path):
        """After kept mutations, iteration_count in YAML reflects completed iterations."""
        session = _make_session(tmp_path, {
            "max_iterations": 2,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        })
        project_root = tmp_path / "project"
        (project_root / "core").mkdir(parents=True)
        for fname in ["prompts.py", "issue_cards.py", "rule_registry.py", "consolidator.py", "crystallizer.py"]:
            (project_root / "core" / fname).write_text(f"# {fname}")

        r = Autoresearcher(
            session_path=session,
            eval_slug="x",
            token_counter=lambda: 0,
            project_root=project_root,
        )
        # Guard passes — all mutations kept
        r._propose_mutation = lambda f: "# mutated"
        r._apply_mutation = lambda f, c: None
        r._run_guard_suite = lambda: True
        kept_files = []
        r._keep = lambda f, c: kept_files.append(f)
        r._discard = lambda f: None

        result = r.run()

        # Read back YAML
        cfg = _load_yaml(session / "autoresearch.yaml")
        assert int(cfg["iteration_count"]) == 2
        assert result.mutations_kept == 2


# ---------------------------------------------------------------------------
# TestYAMLHelpers
# ---------------------------------------------------------------------------

class TestYAMLHelpers:
    def test_dump_and_load_round_trip(self, tmp_path):
        data = {"max_iterations": 5, "token_budget": 12345, "iteration_count": 3, "token_spend": 678}
        path = tmp_path / "test.yaml"
        _dump_yaml(data, path)
        loaded = _load_yaml(path)
        assert int(loaded["max_iterations"]) == 5
        assert int(loaded["token_budget"]) == 12345
        assert int(loaded["iteration_count"]) == 3
        assert int(loaded["token_spend"]) == 678

    def test_dump_is_atomic_no_tmp_leftover(self, tmp_path):
        path = tmp_path / "test.yaml"
        _dump_yaml({"key": "value"}, path)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []
        assert path.exists()
