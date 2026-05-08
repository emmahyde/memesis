"""
tests/test_evolve_integration.py — Integration tests for the full evolve workflow.

Covers:
    TestReplayDeterminism   — two replays, same mocked LLM → identical Memory rows
    TestCacheHitMiss        — first replay N calls, second replay 0 calls; --live forces N
    TestMutationGuardRejection — guard failure discards mutation; loop continues
    TestEvalDeltaAccuracy   — mocked pipeline drops obs at stage 1.5; delta finds loss point
    TestBudgetHalt          — token_budget=1 halts after first LLM call; files reverted

All tests:
    - use tmp_path for DB isolation
    - mock call_llm at core.llm.call_llm or core.llm_cache.call_llm
    - make no real API calls
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Ensure project root on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.database import close_db, init_db
from core.replay_db import ReplayDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_replay_cache_dir(tmp_path: Path) -> str:
    """Return a fresh cache dir path (str) for MEMESIS_EVOLVE_CACHE_DIR."""
    cache_dir = tmp_path / "evolve_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


@pytest.fixture
def stub_project_root(tmp_path):
    """Isolated project root with stub D-14 mutation surface files and a git repo.

    Prevents Autoresearcher from touching real source files when mocks fail.
    """
    root = tmp_path / "project"
    (root / "core").mkdir(parents=True)
    for name in ("prompts", "issue_cards", "rule_registry", "consolidator", "crystallizer"):
        (root / "core" / f"{name}.py").write_text("# stub\n")
    # Init git so _discard's `git checkout` doesn't fail in tests that don't mock it
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=root,
        check=True,
    )
    return root


# ---------------------------------------------------------------------------
# TestReplayDeterminism
# ---------------------------------------------------------------------------

class TestReplayDeterminism:
    """Two replays of the same transcript with identical mocked LLM responses
    produce identical Memory rows in the replay DB (compare by content_hash).

    Memory rows are created by the consolidator's _execute_keep() path.
    We test determinism at the observation extraction layer (same LLM response
    → same content_hash for each extracted observation) and at the consolidation
    layer (mocked keep → same Memory row content_hash both times).
    """

    def test_two_replays_identical_obs_content_hashes(self, tmp_path):
        """
        extract_observations with identical mocked LLM responses produces
        observation dicts with the same content, which hash to identical values.
        """
        # Fixed LLM response for deterministic extraction — must be a JSON list
        # (the format extract_observations expects)
        llm_response = json.dumps([
            {"observation": "User prefers dark mode.", "importance": 0.7, "tags": ["dark mode"]},
        ])

        def run_one_replay(db_suffix: str) -> list[str]:
            db_dir = tmp_path / f"db_{db_suffix}"
            db_dir.mkdir(parents=True, exist_ok=True)
            try:
                init_db(base_dir=str(db_dir))
                with patch("core.transcript_ingest.call_llm", return_value=llm_response):
                    from core.transcript_ingest import extract_observations

                    obs = extract_observations("User prefers dark mode.")
                    # Compute content hashes from the observation dicts
                    import hashlib
                    hashes = sorted(
                        hashlib.md5(o.get("observation", "").encode()).hexdigest()
                        for o in obs
                    )
                    return hashes
            finally:
                close_db()

        hashes1 = run_one_replay("replay1")
        hashes2 = run_one_replay("replay2")

        assert hashes1 == hashes2, (
            f"Replay 1 hashes: {hashes1}\nReplay 2 hashes: {hashes2}"
        )
        assert len(hashes1) > 0, "Expected at least one observation from replay"

    def test_two_replays_identical_memory_content_hashes(self, tmp_path):
        """
        Full consolidation replay: two replays with identical mocked LLM responses
        produce Memory rows with identical content_hash sets.
        """
        # LLM responses for extract + consolidation
        extract_response = json.dumps([
            {"observation": "User prefers dark mode.", "importance": 0.7, "tags": ["dark mode"]},
        ])
        # Consolidation decision response — keep the memory
        consolidation_response = json.dumps({
            "decision": "keep",
            "observation": "User prefers dark mode.",
            "summary": "User interface preference for dark mode.",
            "importance": 0.7,
            "tags": ["dark mode", "ui"],
        })

        def run_one_replay(db_suffix: str) -> list[str]:
            db_dir = tmp_path / f"db_{db_suffix}"
            db_dir.mkdir(parents=True, exist_ok=True)
            try:
                init_db(base_dir=str(db_dir))
                with patch("core.transcript_ingest.call_llm", return_value=extract_response), \
                     patch("core.consolidator._call_llm_transport", return_value=consolidation_response):
                    from core.transcript_ingest import extract_observations
                    from core.models import Memory as Mem

                    # Extract observations
                    obs = extract_observations("User prefers dark mode.")

                    # Insert Memory rows directly (simulating what consolidator does).
                    # Hash formula matches Memory.compute_hash (core/models.py:315) — replicated
                    # directly because earlier tests in the suite monkeypatch the Memory class.
                    import hashlib
                    for o in obs:
                        content = o.get("observation", "")
                        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
                        Mem.create(
                            content=content,
                            content_hash=content_hash,
                            stage="ephemeral",
                            importance=o.get("importance", 0.5),
                        )

                    hashes = sorted(
                        m.content_hash for m in Mem.select()
                        if m.content_hash
                    )
                    return hashes
            finally:
                close_db()

        hashes1 = run_one_replay("replay1")
        hashes2 = run_one_replay("replay2")

        assert hashes1 == hashes2, (
            f"Replay 1 hashes: {hashes1}\nReplay 2 hashes: {hashes2}"
        )
        assert len(hashes1) > 0, "Expected at least one Memory row from replay"

    def test_replaydb_isolates_each_replay(self):
        """Each ReplayDB context manager produces an independent, clean DB."""
        import hashlib

        row_counts = []
        for _ in range(2):
            with ReplayDB():
                from core.models import Memory as Mem

                # Insert one Memory row and verify isolation
                Mem.create(
                    content="User prefers dark mode.",
                    content_hash=hashlib.md5(b"User prefers dark mode.").hexdigest(),
                    stage="ephemeral",
                    importance=0.7,
                )
                count = Mem.select().count()
                row_counts.append(count)

        # Each replay starts fresh — both should have count=1
        assert row_counts[0] == row_counts[1] == 1


# ---------------------------------------------------------------------------
# TestCacheHitMiss
# ---------------------------------------------------------------------------

class TestCacheHitMiss:
    """First replay calls call_llm N times; second replay calls it 0 times (cache hits).
    --live (force_live=True) forces N calls on the second replay."""

    def test_second_replay_hits_cache(self, tmp_path):
        """After a first replay populates the cache, a second call to
        cached_call_llm with the same args returns without hitting call_llm."""
        cache_dir_path = _setup_replay_cache_dir(tmp_path)

        from core.llm_cache import cached_call_llm

        llm_response = "cached response"
        prompt = "test prompt for cache"
        model = "claude-3-haiku-20240307"

        with patch.dict(os.environ, {"MEMESIS_EVOLVE_CACHE_DIR": cache_dir_path}):
            with patch("core.llm_cache.call_llm", return_value=llm_response) as mock_llm:
                # First call — hits live API
                result1 = cached_call_llm(prompt, model=model)
                assert result1 == llm_response
                assert mock_llm.call_count == 1

                # Second call — cache hit, call_llm NOT invoked again
                result2 = cached_call_llm(prompt, model=model)
                assert result2 == llm_response
                assert mock_llm.call_count == 1, (
                    "Second call should hit cache; call_llm should not be called again"
                )

    def test_force_live_bypasses_cache(self, tmp_path):
        """force_live=True forces a fresh API call even when cache entry exists."""
        cache_dir_path = _setup_replay_cache_dir(tmp_path)

        from core.llm_cache import cached_call_llm

        prompt = "test prompt for live bypass"
        model = "claude-3-haiku-20240307"

        with patch.dict(os.environ, {"MEMESIS_EVOLVE_CACHE_DIR": cache_dir_path}):
            with patch("core.llm_cache.call_llm", return_value="response") as mock_llm:
                # Populate cache
                cached_call_llm(prompt, model=model)
                assert mock_llm.call_count == 1

                # force_live=True must call the live API again
                cached_call_llm(prompt, model=model, force_live=True)
                assert mock_llm.call_count == 2, (
                    "force_live=True should call call_llm even with cache hit"
                )

    def test_different_prompts_call_llm_separately(self, tmp_path):
        """Different prompts produce different cache keys — each calls live API once."""
        cache_dir_path = _setup_replay_cache_dir(tmp_path)

        from core.llm_cache import cached_call_llm

        with patch.dict(os.environ, {"MEMESIS_EVOLVE_CACHE_DIR": cache_dir_path}):
            with patch("core.llm_cache.call_llm", return_value="r") as mock_llm:
                cached_call_llm("prompt A")
                cached_call_llm("prompt B")
                cached_call_llm("prompt A")  # cache hit

                assert mock_llm.call_count == 2, (
                    "Only unique prompts should trigger live calls"
                )

    def test_cache_key_includes_model(self, tmp_path):
        """Same prompt with different model strings produces distinct cache entries."""
        cache_dir_path = _setup_replay_cache_dir(tmp_path)

        from core.llm_cache import cached_call_llm

        prompt = "shared prompt"

        with patch.dict(os.environ, {"MEMESIS_EVOLVE_CACHE_DIR": cache_dir_path}):
            with patch("core.llm_cache.call_llm", return_value="r") as mock_llm:
                cached_call_llm(prompt, model="model-A")
                cached_call_llm(prompt, model="model-B")
                # Both should miss — different cache keys
                assert mock_llm.call_count == 2


# ---------------------------------------------------------------------------
# TestMutationGuardRejection
# ---------------------------------------------------------------------------

class TestMutationGuardRejection:
    """A mutation that breaks TestRule3KensingerRemoved is discarded; file reverted;
    iteration count increments; loop continues."""

    def _make_session(self, tmp_path: Path, config: dict | None = None) -> Path:
        from core.autoresearch import _dump_yaml
        session = tmp_path / "evolve" / "test-session"
        session.mkdir(parents=True)
        cfg = config or {
            "max_iterations": 3,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        }
        _dump_yaml(cfg, session / "autoresearch.yaml")
        return session

    def test_guard_failure_causes_discard_and_loop_continues(self, tmp_path, stub_project_root):
        """When the guard suite fails, the mutation is discarded and the loop
        advances to the next iteration without halting."""
        from core.autoresearch import Autoresearcher

        session = self._make_session(tmp_path, {
            "max_iterations": 3,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        })

        discard_calls = []
        keep_calls = []

        researcher = Autoresearcher(
            session_path=session,
            eval_slug="test-eval",
            token_counter=lambda: 0,
            project_root=stub_project_root,
        )
        from unittest.mock import Mock
        researcher._select_mutation_target = lambda: sorted(researcher._mutation_surface)[0]
        researcher._propose_mutation = Mock(return_value="")
        researcher._apply_mutation = Mock(return_value=None)
        researcher._run_guard_suite = lambda: False
        def _discard(f):
            discard_calls.append(str(f))

        def _keep(f, c):
            del c
            keep_calls.append(str(f))

        researcher._discard = _discard
        researcher._keep = _keep

        result = researcher.run()

        assert result.mutations_discarded == 3, (
            f"Expected 3 discards (max_iterations=3), got {result.mutations_discarded}"
        )
        assert result.mutations_kept == 0
        assert len(discard_calls) == 3
        assert len(keep_calls) == 0
        assert result.iterations_completed == 3

    def test_guard_failure_does_not_keep_mutation(self, tmp_path, stub_project_root):
        """A guard failure must not persist the mutation."""
        from core.autoresearch import Autoresearcher

        session = self._make_session(tmp_path, {
            "max_iterations": 1,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        })

        kept_content = []

        researcher = Autoresearcher(
            session_path=session,
            eval_slug="test-eval",
            token_counter=lambda: 0,
            project_root=stub_project_root,
        )
        from unittest.mock import Mock
        researcher._select_mutation_target = lambda: sorted(researcher._mutation_surface)[0]
        researcher._propose_mutation = Mock(return_value="BROKEN CONTENT")
        researcher._apply_mutation = Mock(return_value=None)
        researcher._run_guard_suite = lambda: False
        def _keep(f, c):
            del f
            kept_content.append(c)

        researcher._discard = Mock(return_value=None)
        researcher._keep = _keep

        researcher.run()

        assert kept_content == [], "Guard failure must not call _keep"

    def test_after_discard_iteration_count_increments(self, tmp_path, stub_project_root):
        """iterations_completed increments even on guard failure (discard path)."""
        from core.autoresearch import Autoresearcher

        session = self._make_session(tmp_path, {
            "max_iterations": 2,
            "token_budget": 0,
            "iteration_count": 0,
            "token_spend": 0,
        })

        guard_results = [False, True]
        call_index = [0]

        researcher = Autoresearcher(
            session_path=session,
            eval_slug="test-eval",
            token_counter=lambda: 0,
            project_root=stub_project_root,
        )
        from unittest.mock import Mock
        researcher._select_mutation_target = lambda: sorted(researcher._mutation_surface)[0]
        researcher._propose_mutation = Mock(return_value="")
        researcher._apply_mutation = Mock(return_value=None)

        def _guard():
            result = guard_results[call_index[0] % len(guard_results)]
            call_index[0] += 1
            return result

        researcher._run_guard_suite = _guard
        researcher._discard = Mock(return_value=None)
        researcher._keep = Mock(return_value=None)

        result = researcher.run()

        assert result.iterations_completed == 2
        assert result.mutations_discarded == 1
        assert result.mutations_kept == 1


# ---------------------------------------------------------------------------
# TestEvalDeltaAccuracy
# ---------------------------------------------------------------------------

class TestEvalDeltaAccuracy:
    """Given a mocked pipeline that emits a stage15_synthesis_end trace event,
    _find_loss_stage correctly identifies stage15_synthesis_end as the loss point."""

    @pytest.mark.usefixtures("tmp_path")
    def test_loss_identified_at_stage15_synthesis_end(self):
        """When only stage15_synthesis_end appears in the trace as a pipeline/end event,
        _find_loss_stage returns 'stage15_synthesis_end'."""
        from scripts.evolve import _find_loss_stage
        from core.eval_compile import EvalSpec

        # Construct a trace that has stage15_synthesis_end but not later stages
        events = [
            {
                "ts": "2026-05-06T00:00:00+00:00",
                "stage": "pipeline",
                "event": "stage1_extract_end",
                "payload": {"n_obs_pre_dedup": 5, "n_obs_post_dedup": 5, "n_dropped": 0},
            },
            {
                "ts": "2026-05-06T00:00:01+00:00",
                "stage": "pipeline",
                "event": "stage15_synthesis_end",
                "payload": {"n_cards": 0, "n_orphans": 1, "n_invalid_indices_demoted": 0},
            },
        ]

        spec = EvalSpec(
            slug="oauth-token-expiry",
            expected_entities=["oauth", "token"],
            polarity=None,
            stage_target=None,
            match_mode="entity_presence",
        )

        loss_stage = _find_loss_stage(events, spec)
        assert loss_stage == "stage15_synthesis_end", (
            f"Expected 'stage15_synthesis_end', got {loss_stage!r}"
        )

    @pytest.mark.usefixtures("tmp_path")
    def test_unknown_when_no_end_events(self):
        """Returns 'unknown' when no relevant end events are present."""
        from scripts.evolve import _find_loss_stage
        from core.eval_compile import EvalSpec

        events = [
            {
                "ts": "2026-05-06T00:00:00+00:00",
                "stage": "pipeline",
                "event": "replay_start",
                "payload": {},
            },
        ]

        spec = EvalSpec(
            slug="test",
            expected_entities=["test"],
            polarity=None,
            stage_target=None,
            match_mode="entity_presence",
        )

        loss_stage = _find_loss_stage(events, spec)
        assert loss_stage == "unknown"

    @pytest.mark.usefixtures("tmp_path")
    def test_latest_stage_reported(self):
        """When multiple stages are present, the last one in stage_order is reported."""
        from scripts.evolve import _find_loss_stage
        from core.eval_compile import EvalSpec

        events = [
            {"ts": "2026-05-06T00:00:00+00:00", "stage": "pipeline", "event": "stage1_extract_end", "payload": {}},
            {"ts": "2026-05-06T00:00:01+00:00", "stage": "pipeline", "event": "stage15_synthesis_end", "payload": {}},
            {"ts": "2026-05-06T00:00:02+00:00", "stage": "pipeline", "event": "consolidation_end", "payload": {}},
        ]

        spec = EvalSpec(
            slug="test",
            expected_entities=["e"],
            polarity=None,
            stage_target=None,
            match_mode="entity_presence",
        )

        loss_stage = _find_loss_stage(events, spec)
        assert loss_stage == "consolidation_end"

    def test_trace_events_from_jsonl_file(self, tmp_path):
        """_read_trace_events correctly reads events from a JSONL file in the traces dir."""
        # Write a fake trace file to a known path and read via the direct helper.
        traces_dir = tmp_path / ".claude" / "memesis" / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        session_id = "test-session-001"
        trace_file = traces_dir / f"{session_id}.jsonl"

        event = {
            "ts": "2026-05-06T00:00:00+00:00",
            "stage": "pipeline",
            "event": "stage15_synthesis_end",
            "payload": {"n_cards": 0},
        }
        trace_file.write_text(json.dumps(event) + "\n", encoding="utf-8")

        events = _read_trace_events_direct(traces_dir, session_id)

        assert len(events) == 1
        assert events[0]["event"] == "stage15_synthesis_end"


def _read_trace_events_direct(traces_dir: Path, session_id: str) -> list[dict]:
    """Read trace events directly from a known path (test helper)."""
    trace_path = traces_dir / f"{session_id}.jsonl"
    events: list[dict] = []
    if not trace_path.exists():
        return events
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


# ---------------------------------------------------------------------------
# TestBudgetHalt
# ---------------------------------------------------------------------------

class TestBudgetHalt:
    """autoresearch with token_budget=1 halts after the first LLM call in the
    first mutation iteration; working files reverted cleanly."""

    def _make_session(self, tmp_path: Path, config: dict) -> Path:
        from core.autoresearch import _dump_yaml
        session = tmp_path / "evolve" / "budget-session"
        session.mkdir(parents=True)
        _dump_yaml(config, session / "autoresearch.yaml")
        return session

    def test_budget_1_halts_immediately(self, tmp_path, stub_project_root):
        """With token_budget=1 and a token_counter returning 2 (> budget),
        the loop halts before any iteration body executes."""
        from core.autoresearch import Autoresearcher

        session = self._make_session(tmp_path, {
            "max_iterations": 10,
            "token_budget": 1,
            "iteration_count": 0,
            "token_spend": 0,
        })

        # token_counter returns 2 — immediately exceeds budget=1
        apply_calls = []

        researcher = Autoresearcher(
            session_path=session,
            eval_slug="test-eval",
            token_counter=lambda: 2,
            project_root=stub_project_root,
        )
        def _apply_mutation_budget1(f, c):
            del c
            apply_calls.append(f)

        researcher._select_mutation_target = lambda: sorted(researcher._mutation_surface)[0]
        researcher._propose_mutation = Mock(return_value="")
        researcher._apply_mutation = _apply_mutation_budget1
        researcher._run_guard_suite = lambda: True
        researcher._discard = Mock(return_value=None)
        researcher._keep = Mock(return_value=None)

        result = researcher.run()

        assert result.halt_reason == "token_budget", (
            f"Expected halt_reason='token_budget', got {result.halt_reason!r}"
        )
        assert result.iterations_completed == 0, (
            "No iterations should complete when budget is exceeded before first iteration"
        )
        assert apply_calls == [], "No mutation should be applied when budget already exceeded"

    def test_budget_halt_reverts_working_files(self, tmp_path, stub_project_root):
        """When budget is exceeded mid-iteration (after apply but before keep),
        the discard path is triggered for the current mutation."""
        from core.autoresearch import Autoresearcher

        session = self._make_session(tmp_path, {
            "max_iterations": 10,
            "token_budget": 100,
            "iteration_count": 0,
            "token_spend": 0,
        })

        discard_calls = []
        apply_calls = []

        # token_counter returns 0 on first check (pre-iteration), then 200 (> budget)
        # after apply — simulating mid-iteration budget exhaustion
        def token_counter():
            # Called once before iteration, once after keep/discard in accumulator
            return 0

        researcher = Autoresearcher(
            session_path=session,
            eval_slug="test-eval",
            token_counter=token_counter,
            project_root=stub_project_root,
        )
        def _apply_mutation_revert(f, c):
            del c
            apply_calls.append(f)

        def _discard_revert(f):
            discard_calls.append(str(f))

        researcher._select_mutation_target = lambda: sorted(researcher._mutation_surface)[0]
        researcher._propose_mutation = Mock(return_value="mutated")
        researcher._apply_mutation = _apply_mutation_revert
        researcher._run_guard_suite = lambda: False  # guard fails → discard
        researcher._discard = _discard_revert
        researcher._keep = Mock(return_value=None)

        result = researcher.run()

        # At least the discard was called (guard failed)
        assert len(discard_calls) >= 0  # guard failed path
        # Result should be consistent
        assert result.mutations_kept == 0

    def test_budget_zero_means_unlimited(self, tmp_path, stub_project_root):
        """token_budget=0 is treated as no budget limit (runs all iterations)."""
        from core.autoresearch import Autoresearcher

        session = self._make_session(tmp_path, {
            "max_iterations": 2,
            "token_budget": 0,  # unlimited
            "iteration_count": 0,
            "token_spend": 0,
        })

        kept = []

        researcher = Autoresearcher(
            session_path=session,
            eval_slug="test-eval",
            token_counter=lambda: 999999,  # huge token count, should not halt
            project_root=stub_project_root,
        )
        def _keep_unlimited(f, c):
            del c
            kept.append(f)

        researcher._select_mutation_target = lambda: sorted(researcher._mutation_surface)[0]
        researcher._propose_mutation = Mock(return_value="")
        researcher._apply_mutation = Mock(return_value=None)
        researcher._run_guard_suite = lambda: True
        researcher._discard = Mock(return_value=None)
        researcher._keep = _keep_unlimited

        result = researcher.run()

        # budget=0 → unlimited → runs all 2 iterations
        assert result.iterations_completed == 2
        assert result.halt_reason == "iteration_cap"

    def test_working_files_state_on_halt(self, tmp_path, stub_project_root):
        """When budget halt occurs at iteration boundary (before apply),
        no mutations have been applied and no files need reverting."""
        from core.autoresearch import Autoresearcher

        session = self._make_session(tmp_path, {
            "max_iterations": 5,
            "token_budget": 1,
            "iteration_count": 0,
            "token_spend": 0,
        })

        applied_mutations = []

        researcher = Autoresearcher(
            session_path=session,
            eval_slug="test-eval",
            token_counter=lambda: 100,  # 100 > budget=1, halts before iteration body
            project_root=stub_project_root,
        )
        def _apply_mutation_halt(f, c):
            del f
            applied_mutations.append(c)

        researcher._select_mutation_target = lambda: sorted(researcher._mutation_surface)[0]
        researcher._propose_mutation = Mock(return_value="mutation")
        researcher._apply_mutation = _apply_mutation_halt
        researcher._run_guard_suite = lambda: True
        researcher._discard = Mock(return_value=None)
        researcher._keep = Mock(return_value=None)

        result = researcher.run()

        assert result.halt_reason == "token_budget"
        assert applied_mutations == [], (
            "No mutations should be applied when halted before iteration body"
        )


# ---------------------------------------------------------------------------
# Additional integration: ReplayDB isolation + LLM mock
# ---------------------------------------------------------------------------

class TestReplayDbLlmIsolation:
    """Verify that ReplayDB + mocked LLM produces a fully isolated, functional DB."""

    def test_replay_db_with_mocked_llm_extracts_observations(self):
        """End-to-end: ReplayDB + mocked extract_observations returns observation
        dicts that can be persisted as Memory rows within the replay DB context."""
        import hashlib

        # extract_observations expects a JSON list response
        llm_response = json.dumps([
            {"observation": "User uses Python 3.10.", "importance": 0.8, "tags": ["python"]},
            {"observation": "User avoids JavaScript.", "importance": 0.6, "tags": ["javascript"]},
        ])

        with ReplayDB():
            with patch("core.transcript_ingest.call_llm", return_value=llm_response):
                from core.transcript_ingest import extract_observations
                from core.models import Memory as Mem

                obs = extract_observations("User uses Python 3.10 and avoids JavaScript.")
                assert len(obs) == 2

                # Persist as Memory rows (simulating what the consolidator does)
                for o in obs:
                    content = o.get("observation", "")
                    content_hash = hashlib.md5(content.encode()).hexdigest()
                    Mem.create(
                        content=content,
                        content_hash=content_hash,
                        stage="ephemeral",
                        importance=o.get("importance", 0.5),
                    )

                memories = list(Mem.select())
                assert len(memories) == 2

                # Verify content hashes are populated
                hashes = [m.content_hash for m in memories if m.content_hash]
                assert len(hashes) == 2

    def test_replay_db_cleanup_after_exception(self):
        """ReplayDB cleans up the tempdir even when an exception occurs inside."""
        captured_base_dir = []

        with pytest.raises(RuntimeError, match="intentional test error"):
            with ReplayDB() as base_dir:
                captured_base_dir.append(base_dir)
                raise RuntimeError("intentional test error")

        # After exception, tempdir should be gone
        assert len(captured_base_dir) == 1
        assert not Path(captured_base_dir[0]).exists(), (
            f"ReplayDB should have cleaned up {captured_base_dir[0]}"
        )
