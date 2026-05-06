"""Tests for core/trace.py — JSONL trace writer."""

import json
from pathlib import Path

import pytest

from core.trace import (
    TraceWriter,
    _MAX_SESSIONS,
    _read_index,
    get_active_writer,
    set_active_writer,
)


@pytest.fixture(autouse=True)
def isolate_default_base(tmp_path, monkeypatch):
    """Redirect the module-level default base dir to tmp_path so tests never
    touch the real ~/.claude/memesis/traces/."""
    import core.trace as _trace_mod

    monkeypatch.setattr(_trace_mod, "_DEFAULT_BASE", tmp_path / "traces")
    yield
    # Reset active writer between tests.
    set_active_writer(None)


@pytest.fixture
def traces_dir(tmp_path) -> Path:
    d = tmp_path / "traces"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# TestEmitCreatesFile
# ---------------------------------------------------------------------------


class TestEmitCreatesFile:
    def test_file_created_on_first_emit(self, traces_dir):
        writer = TraceWriter("sess-001", base_dir=traces_dir)
        assert writer.trace_path is None
        writer.emit("extract", "stage_boundary", {"name": "extract", "direction": "start"})
        assert writer.trace_path is not None
        assert writer.trace_path.exists()

    def test_file_named_after_session_id(self, traces_dir):
        writer = TraceWriter("my-session-42", base_dir=traces_dir)
        writer.emit("extract", "stage_boundary", {"name": "extract", "direction": "start"})
        assert writer.trace_path.name == "my-session-42.jsonl"

    def test_base_dir_created_if_missing(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "traces"
        writer = TraceWriter("sess-new", base_dir=nested)
        writer.emit("consolidate", "stage_boundary", {"name": "consolidate", "direction": "start"})
        assert nested.exists()
        assert (nested / "sess-new.jsonl").exists()


# ---------------------------------------------------------------------------
# TestPerEventFlush
# ---------------------------------------------------------------------------


class TestPerEventFlush:
    def test_each_emit_immediately_readable(self, traces_dir):
        """Each emit must be readable without closing the writer."""
        writer = TraceWriter("flush-test", base_dir=traces_dir)
        writer.emit("extract", "stage_boundary", {"direction": "start"})

        # Read back without going through the writer.
        lines = writer.trace_path.read_text().splitlines()
        assert len(lines) == 1

        writer.emit("extract", "card_synth", {"card_id": "c1", "importance": 0.7})
        lines = writer.trace_path.read_text().splitlines()
        assert len(lines) == 2

    def test_multiple_emits_append_lines(self, traces_dir):
        writer = TraceWriter("multi-emit", base_dir=traces_dir)
        for i in range(5):
            writer.emit("consolidate", "keep", {"importance": 0.5 + i * 0.1})
        lines = writer.trace_path.read_text().splitlines()
        assert len(lines) == 5


# ---------------------------------------------------------------------------
# TestPayloadRoundtrip
# ---------------------------------------------------------------------------


class TestPayloadRoundtrip:
    def test_stage_boundary_roundtrip(self, traces_dir):
        writer = TraceWriter("rt-stage", base_dir=traces_dir)
        payload = {"name": "extract", "direction": "end", "extra": {"n_obs": 12}}
        writer.emit("extract", "stage_boundary", payload)

        record = json.loads(writer.trace_path.read_text().strip())
        assert record["stage"] == "extract"
        assert record["event"] == "stage_boundary"
        assert record["payload"] == payload

    def test_card_synth_roundtrip(self, traces_dir):
        writer = TraceWriter("rt-card", base_dir=traces_dir)
        payload = {
            "card_id": "card-7",
            "importance": 0.82,
            "affect_valence": "friction",
            "evidence_obs_indices": [0, 3, 5],
            "n_evidence_quotes": 3,
        }
        writer.emit("issue_cards", "card_synth", payload)
        record = json.loads(writer.trace_path.read_text().strip())
        assert record["payload"] == payload

    def test_keep_roundtrip(self, traces_dir):
        writer = TraceWriter("rt-keep", base_dir=traces_dir)
        payload = {
            "memory_id": "mem-abc",
            "importance": 0.85,
            "affect_valence": "friction",
            "kensinger_applied": True,
        }
        writer.emit("consolidate", "keep", payload)
        record = json.loads(writer.trace_path.read_text().strip())
        assert record["payload"]["kensinger_applied"] is True

    def test_llm_envelope_roundtrip(self, traces_dir):
        writer = TraceWriter("rt-llm", base_dir=traces_dir)
        payload = {
            "prompt_hash": "abc123def456",
            "model": "claude-sonnet-4-6",
            "input_tokens": 512,
            "output_tokens": 128,
            "response_chars": 480,
        }
        writer.emit("llm", "llm_envelope", payload)
        record = json.loads(writer.trace_path.read_text().strip())
        assert record["payload"] == payload

    def test_ts_is_utc_aware(self, traces_dir):
        writer = TraceWriter("rt-ts", base_dir=traces_dir)
        writer.emit("extract", "stage_boundary", {"direction": "start"})
        record = json.loads(writer.trace_path.read_text().strip())
        # UTC-aware isoformat contains '+00:00' or ends with 'Z'.
        ts = record["ts"]
        assert "+00:00" in ts or ts.endswith("Z"), f"timestamp not UTC-aware: {ts!r}"

    def test_validator_outcome_roundtrip(self, traces_dir):
        writer = TraceWriter("rt-validator", base_dir=traces_dir)
        payload = {
            "validator": "_card_evidence_indices_valid",
            "result": False,
            "card_id": "card-3",
            "detail": "all indices out of range",
        }
        writer.emit("issue_cards", "validator_outcome", payload)
        record = json.loads(writer.trace_path.read_text().strip())
        assert record["payload"] == payload

    def test_prune_roundtrip(self, traces_dir):
        writer = TraceWriter("rt-prune", base_dir=traces_dir)
        payload = {"observation": "user said hello", "reason": "low importance"}
        writer.emit("consolidate", "prune", payload)
        record = json.loads(writer.trace_path.read_text().strip())
        assert record["payload"] == payload

    def test_promote_roundtrip(self, traces_dir):
        writer = TraceWriter("rt-promote", base_dir=traces_dir)
        payload = {
            "memory_id": "mem-xyz",
            "from_stage": "consolidated",
            "to_stage": "crystallized",
        }
        writer.emit("crystallizer", "promote", payload)
        record = json.loads(writer.trace_path.read_text().strip())
        assert record["payload"] == payload

    def test_kensinger_bump_roundtrip(self, traces_dir):
        writer = TraceWriter("rt-kensinger", base_dir=traces_dir)
        payload = {
            "memory_id": "mem-k1",
            "base_importance": 0.80,
            "bumped_importance": 0.85,
        }
        writer.emit("consolidate", "kensinger_bump", payload)
        record = json.loads(writer.trace_path.read_text().strip())
        assert record["payload"] == payload


# ---------------------------------------------------------------------------
# TestRetentionEviction
# ---------------------------------------------------------------------------


class TestRetentionEviction:
    def test_51st_session_evicts_oldest(self, traces_dir):
        """When the 51st unique session is registered, the first is removed."""
        writers = []
        for i in range(_MAX_SESSIONS):
            w = TraceWriter(f"sess-{i:04d}", base_dir=traces_dir)
            w.emit("extract", "stage_boundary", {"direction": "start"})
            writers.append(w)

        # All 50 JSONL files should exist.
        for i in range(_MAX_SESSIONS):
            assert (traces_dir / f"sess-{i:04d}.jsonl").exists(), f"sess-{i:04d} missing before cap"

        # Emit the 51st session — should evict sess-0000.
        w51 = TraceWriter("sess-overflow", base_dir=traces_dir)
        w51.emit("extract", "stage_boundary", {"direction": "start"})

        assert not (traces_dir / "sess-0000.jsonl").exists(), "oldest session was not evicted"
        assert (traces_dir / "sess-overflow.jsonl").exists(), "new session file missing"

        # Index should contain exactly 50 entries.
        index = _read_index(traces_dir)
        assert len(index) == _MAX_SESSIONS

    def test_eviction_fifo_order(self, traces_dir):
        """Eviction removes the *oldest* session, not a random one."""
        for i in range(_MAX_SESSIONS):
            w = TraceWriter(f"s{i}", base_dir=traces_dir)
            w.emit("x", "y", {})

        # 51st session: s0 should be evicted, s1..s49 + s-new retained.
        TraceWriter("s-new", base_dir=traces_dir).emit("x", "y", {})

        assert not (traces_dir / "s0.jsonl").exists()
        assert (traces_dir / "s1.jsonl").exists()
        assert (traces_dir / f"s{_MAX_SESSIONS - 1}.jsonl").exists()

    def test_49_sessions_no_eviction(self, traces_dir):
        """Under the cap, nothing is evicted."""
        for i in range(_MAX_SESSIONS - 1):
            w = TraceWriter(f"t{i}", base_dir=traces_dir)
            w.emit("x", "y", {})

        for i in range(_MAX_SESSIONS - 1):
            assert (traces_dir / f"t{i}.jsonl").exists()


# ---------------------------------------------------------------------------
# TestReplaySessionBudget
# ---------------------------------------------------------------------------


class TestReplaySessionBudget:
    def test_replay_session_counted_in_budget(self, traces_dir):
        """replay-<orig>-<n> sessions count toward the 50-session cap."""
        # Fill to 49 with normal sessions.
        for i in range(_MAX_SESSIONS - 1):
            w = TraceWriter(f"normal-{i:04d}", base_dir=traces_dir)
            w.emit("x", "y", {})

        # Add one replay session — reaches the cap of 50.
        replay_w = TraceWriter("replay-normal-0000-1", base_dir=traces_dir)
        replay_w.emit("extract", "stage_boundary", {"direction": "start"})

        index = _read_index(traces_dir)
        assert len(index) == _MAX_SESSIONS

        # Add one more normal session — oldest normal session should be evicted.
        TraceWriter("normal-extra", base_dir=traces_dir).emit("x", "y", {})

        index_after = _read_index(traces_dir)
        assert len(index_after) == _MAX_SESSIONS
        assert "normal-0000" not in index_after

    def test_replay_session_file_created(self, traces_dir):
        writer = TraceWriter("replay-abc-1", base_dir=traces_dir)
        writer.emit("extract", "stage_boundary", {"direction": "start"})
        assert (traces_dir / "replay-abc-1.jsonl").exists()


# ---------------------------------------------------------------------------
# TestContextManager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_context_manager_emits_normally(self, traces_dir):
        with TraceWriter("ctx-test", base_dir=traces_dir) as w:
            w.emit("extract", "stage_boundary", {"direction": "start"})
        assert (traces_dir / "ctx-test.jsonl").exists()

    def test_close_is_idempotent(self, traces_dir):
        w = TraceWriter("close-test", base_dir=traces_dir)
        w.emit("x", "y", {})
        w.close()
        w.close()  # should not raise


# ---------------------------------------------------------------------------
# TestActiveWriter
# ---------------------------------------------------------------------------


class TestActiveWriter:
    def test_set_and_get_active_writer(self, traces_dir):
        writer = TraceWriter("active-test", base_dir=traces_dir)
        set_active_writer(writer)
        assert get_active_writer() is writer

    def test_clear_active_writer(self, traces_dir):
        writer = TraceWriter("clear-test", base_dir=traces_dir)
        set_active_writer(writer)
        set_active_writer(None)
        assert get_active_writer() is None

    def test_default_active_writer_is_none(self):
        assert get_active_writer() is None


# ---------------------------------------------------------------------------
# TestNoLoggingChannel
# ---------------------------------------------------------------------------


class TestNoLoggingChannel:
    def test_emit_does_not_use_root_logger(self, traces_dir, caplog):
        """Trace events must NOT be routed through logging."""
        import logging

        with caplog.at_level(logging.DEBUG, logger="root"):
            writer = TraceWriter("no-log", base_dir=traces_dir)
            writer.emit("extract", "stage_boundary", {"direction": "start"})

        # No log records whose message contains "stage_boundary"
        messages = [r.message for r in caplog.records]
        assert not any("stage_boundary" in m for m in messages)
