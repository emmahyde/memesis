"""Tests for scripts/trace_query.py."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = str(Path(__file__).parent.parent / "scripts" / "trace_query.py")


def run(args: list[str], traces_dir: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, SCRIPT]
    if traces_dir is not None:
        cmd += ["--traces-dir", str(traces_dir)]
    cmd += args
    return subprocess.run(cmd, capture_output=True, text=True)


def write_trace(traces_dir: Path, session_id: str, events: list[dict]) -> Path:
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"{session_id}.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )
    return path


SAMPLE_EVENTS = [
    {"ts": "2024-01-01T00:00:00", "stage": "ingest", "event": "record_stored", "payload": {"id": "a1"}},
    {"ts": "2024-01-01T00:00:01", "stage": "consolidate", "event": "kensinger_bump", "payload": {"delta": 0.1}},
    {"ts": "2024-01-01T00:00:02", "stage": "ingest", "event": "record_stored", "payload": {"id": "a2"}},
]


# ---------------------------------------------------------------------------
# --list-sessions
# ---------------------------------------------------------------------------


def test_list_sessions_empty_dir(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    # Directory does not exist yet
    result = run(["--list-sessions"], traces_dir=traces_dir)
    assert result.returncode == 0
    assert "No traces found" in result.stdout


def test_list_sessions_empty_existing_dir(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    result = run(["--list-sessions"], traces_dir=traces_dir)
    assert result.returncode == 0
    assert "No traces found" in result.stdout


def test_list_sessions_shows_both_sorted(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    p1 = write_trace(traces_dir, "session-alpha", SAMPLE_EVENTS[:1])
    time.sleep(0.02)  # ensure distinct mtime
    p2 = write_trace(traces_dir, "session-beta", SAMPLE_EVENTS[:2])

    result = run(["--list-sessions"], traces_dir=traces_dir)
    assert result.returncode == 0
    out = result.stdout

    # Both sessions appear
    assert "session-alpha" in out
    assert "session-beta" in out

    # Sorted by mtime: alpha (older) before beta (newer)
    assert out.index("session-alpha") < out.index("session-beta")


# ---------------------------------------------------------------------------
# --session (valid)
# ---------------------------------------------------------------------------


def test_session_prints_all_events_chronologically(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    write_trace(traces_dir, "sess1", SAMPLE_EVENTS)
    result = run(["--session", "sess1"], traces_dir=traces_dir)
    assert result.returncode == 0
    out = result.stdout
    # All three events appear in order
    positions = [out.find("record_stored"), out.find("kensinger_bump")]
    assert positions[0] < positions[1], "events not in chronological order"
    assert out.count("record_stored") == 2


# ---------------------------------------------------------------------------
# --session (missing)
# ---------------------------------------------------------------------------


def test_session_missing_exits_gracefully(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    result = run(["--session", "nonexistent-xyz"], traces_dir=traces_dir)
    assert result.returncode != 0  # exits with error code
    assert "nonexistent-xyz" in result.stderr or "No trace" in result.stderr


# ---------------------------------------------------------------------------
# --event filter
# ---------------------------------------------------------------------------


def test_event_filter(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    write_trace(traces_dir, "sess2", SAMPLE_EVENTS)
    result = run(["--session", "sess2", "--event", "kensinger_bump"], traces_dir=traces_dir)
    assert result.returncode == 0
    out = result.stdout
    assert "kensinger_bump" in out
    assert "record_stored" not in out


# ---------------------------------------------------------------------------
# --stage filter
# ---------------------------------------------------------------------------


def test_stage_filter(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    write_trace(traces_dir, "sess3", SAMPLE_EVENTS)
    result = run(["--session", "sess3", "--stage", "consolidate"], traces_dir=traces_dir)
    assert result.returncode == 0
    out = result.stdout
    assert "consolidate" in out
    # ingest-stage events should not appear
    assert "ingest" not in out


# ---------------------------------------------------------------------------
# --json flag
# ---------------------------------------------------------------------------


def test_json_flag_emits_raw_jsonl(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    write_trace(traces_dir, "sess4", SAMPLE_EVENTS)
    result = run(["--session", "sess4", "--json"], traces_dir=traces_dir)
    assert result.returncode == 0
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == len(SAMPLE_EVENTS)
    for line in lines:
        obj = json.loads(line)  # must be valid JSON
        assert "ts" in obj and "event" in obj


# ---------------------------------------------------------------------------
# Malformed line resilience
# ---------------------------------------------------------------------------


def test_malformed_line_skipped(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    path = traces_dir / "sess5.jsonl"
    path.write_text(
        json.dumps(SAMPLE_EVENTS[0]) + "\n"
        "NOT VALID JSON <<<\n"
        + json.dumps(SAMPLE_EVENTS[1]) + "\n",
        encoding="utf-8",
    )
    result = run(["--session", "sess5", "--json"], traces_dir=traces_dir)
    # Must not crash
    assert result.returncode == 0
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    # Two valid lines, one bad → two parsed events
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


# ---------------------------------------------------------------------------
# Missing base dir → created on first run
# ---------------------------------------------------------------------------


def test_missing_base_dir_created(tmp_path: Path) -> None:
    traces_dir = tmp_path / "deep" / "nested" / "traces"
    assert not traces_dir.exists()
    result = run(["--list-sessions"], traces_dir=traces_dir)
    assert result.returncode == 0
    assert traces_dir.exists()
    assert "No traces found" in result.stdout
