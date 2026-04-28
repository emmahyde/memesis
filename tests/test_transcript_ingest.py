import sys
from pathlib import Path
from datetime import date
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cursors import CursorStore
from core.transcript_ingest import tick, extract_observations  # type: ignore[import]


def test_new_session_seeds_cursor_at_eof(tmp_path):
    transcript = tmp_path / "projects" / "proj-hash" / "session-abc.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type": "user", "message": {"role": "user", "content": "hello"}}\n')

    cursors_db = tmp_path / "cursors.db"

    with patch("core.transcript_ingest.discover_transcripts", return_value=[transcript]), \
         patch("core.transcript_ingest.CursorStore", lambda: CursorStore(cursors_db)):
        results = tick(dry_run=False)

    assert results["skipped"] == 1
    assert results["processed"] == 0

    with CursorStore(cursors_db) as store:
        cursor = store.get("session-abc")
    assert cursor is not None
    assert cursor.last_byte_offset == transcript.stat().st_size


def test_known_session_with_delta_extracts_observations(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
    transcript = tmp_path / "projects" / "proj-hash" / "session-xyz.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(fixture.read_bytes())

    cursors_db = tmp_path / "cursors.db"
    with CursorStore(cursors_db) as store:
        store.upsert("session-xyz", str(transcript), 0)

    fake_obs = [{"content": "Auth uses JWT with 24h TTL", "mode": "finding", "importance": 0.7, "tags": ["auth"]}]

    with patch("core.transcript_ingest.discover_transcripts", return_value=[transcript]), \
         patch("core.transcript_ingest.CursorStore", lambda: CursorStore(cursors_db)), \
         patch("core.transcript_ingest.extract_observations", return_value=fake_obs):
        results = tick(dry_run=False)

    assert results["processed"] == 1
    assert results["observations_total"] == 1

    buffer = tmp_path / "projects" / "proj-hash" / "memory" / "ephemeral" / f"session-{date.today().isoformat()}.md"
    assert buffer.exists()
    assert "Auth uses JWT" in buffer.read_text()


def test_path_rotation_resets_cursor(tmp_path):
    old_path = tmp_path / "projects" / "proj-hash" / "session-rot.jsonl"
    new_path = tmp_path / "projects" / "proj-hash" / "session-rot-new.jsonl"
    old_path.parent.mkdir(parents=True)
    new_path.write_text('{"type": "user", "message": {"role": "user", "content": "hi"}}\n')

    cursors_db = tmp_path / "cursors.db"
    with CursorStore(cursors_db) as store:
        store.upsert("session-rot-new", str(old_path), 0)

    with patch("core.transcript_ingest.discover_transcripts", return_value=[new_path]), \
         patch("core.transcript_ingest.CursorStore", lambda: CursorStore(cursors_db)):
        results = tick(dry_run=False)

    assert results["skipped"] == 1
    with CursorStore(cursors_db) as store:
        cursor = store.get("session-rot-new")
    assert cursor is not None
    assert cursor.transcript_path == str(new_path)
    assert cursor.last_byte_offset == new_path.stat().st_size


def test_empty_delta_skips_llm(tmp_path):
    transcript = tmp_path / "projects" / "proj-hash" / "session-empty.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type": "user", "message": {"role": "user", "content": "hello"}}\n')
    size = transcript.stat().st_size

    cursors_db = tmp_path / "cursors.db"
    with CursorStore(cursors_db) as store:
        store.upsert("session-empty", str(transcript), size)

    with patch("core.transcript_ingest.discover_transcripts", return_value=[transcript]), \
         patch("core.transcript_ingest.CursorStore", lambda: CursorStore(cursors_db)), \
         patch("core.transcript_ingest.extract_observations") as mock_extract:
        results = tick(dry_run=False)

    mock_extract.assert_not_called()
    assert results["processed"] == 0
    assert results["observations_total"] == 0


# ---------------------------------------------------------------------------
# Skip-protocol tests — Sprint A WS-B (LLME-F5)
# Tests target extract_observations() directly for format-detection logic.
# ---------------------------------------------------------------------------


def test_extract_observations_empty_array_returns_empty_list():
    """[] response → empty observation list, no crash."""
    with patch("core.transcript_ingest.call_llm", return_value="[]"):
        result = extract_observations("some transcript")
    assert result == []


def test_extract_observations_array_with_obs_returns_filtered_list():
    """[{...}] response → parsed observations filtered by importance >= 0.3."""
    import json
    obs = [
        {"content": "Auth uses JWT", "mode": "finding", "importance": 0.7, "tags": []},
        {"content": "low signal", "mode": "finding", "importance": 0.1, "tags": []},
    ]
    with patch("core.transcript_ingest.call_llm", return_value=json.dumps(obs)):
        result = extract_observations("some transcript")
    assert len(result) == 1
    assert result[0]["content"] == "Auth uses JWT"


def test_extract_observations_skipped_dict_returns_empty_list(tmp_path, caplog):
    """{"skipped": true, "reason": "..."} → empty list, skip trace logged."""
    import logging
    import json
    skip_response = json.dumps({"skipped": True, "reason": "no signal in session"})
    with patch("core.transcript_ingest.call_llm", return_value=skip_response), \
         caplog.at_level(logging.INFO, logger="core.transcript_ingest"):
        result = extract_observations("boring transcript")
    assert result == []
    assert any("no signal" in r.message for r in caplog.records)


def test_extract_observations_malformed_dict_returns_empty_list(caplog):
    """{"foo": "bar"} dict without skipped key → empty list, rejection logged."""
    import logging
    import json
    with patch("core.transcript_ingest.call_llm", return_value=json.dumps({"foo": "bar"})), \
         caplog.at_level(logging.WARNING, logger="core.transcript_ingest"):
        result = extract_observations("transcript")
    assert result == []
    assert any("malformed" in r.message or "skipped" in r.message for r in caplog.records)


def test_extract_observations_invalid_json_returns_empty_list(caplog):
    """Non-JSON response → existing failure mode preserved (empty list, warning logged)."""
    import logging
    with patch("core.transcript_ingest.call_llm", return_value="not json at all"), \
         caplog.at_level(logging.WARNING, logger="core.transcript_ingest"):
        result = extract_observations("transcript")
    assert result == []
    assert any("parse" in r.message.lower() or "json" in r.message.lower() for r in caplog.records)
