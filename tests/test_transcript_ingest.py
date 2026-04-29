import sys
from pathlib import Path
from datetime import date
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cursors import CursorStore
from core.transcript_ingest import tick, extract_observations, _dedupe_observations  # type: ignore[import]


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


# ---------------------------------------------------------------------------
# session_type detection wiring — Sprint B WS-G / LLME-F9
# ---------------------------------------------------------------------------


def test_extract_observations_passes_session_type_to_prompt():
    """session_type kwarg is forwarded into the prompt format call."""
    import json
    obs = [{"content": "finding", "mode": "finding", "importance": 0.6, "tags": []}]
    captured_prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        captured_prompts.append(prompt)
        return json.dumps(obs)

    with patch("core.transcript_ingest.call_llm", side_effect=fake_llm):
        extract_observations("transcript text", session_type="writing")

    assert len(captured_prompts) == 1
    assert "writing" in captured_prompts[0]


def test_tick_attaches_session_type_to_observations(tmp_path):
    """tick() attaches session_type to each observation from extract_observations."""
    transcript = tmp_path / "projects" / "proj-hash" / "session-st1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type": "user", "message": {"role": "user", "content": "hi"}}\n')

    cursors_db = tmp_path / "cursors.db"
    with CursorStore(cursors_db) as store:
        store.upsert("session-st1", str(transcript), 0)

    fake_entries = [{"type": "user", "cwd": "/Users/emmahyde/projects/sector",
                     "message": {"role": "user", "content": "hello"}}]
    # Observation without session_type — tick should add it
    fake_obs = [{"content": "some finding", "mode": "finding", "importance": 0.7, "tags": []}]
    captured_obs: list[list[dict]] = []

    def fake_append(mem_dir, observations, dry_run=False):
        captured_obs.append(list(observations))
        return len(observations)

    with patch("core.transcript_ingest.discover_transcripts", return_value=[transcript]), \
         patch("core.transcript_ingest.CursorStore", lambda: CursorStore(cursors_db)), \
         patch("core.transcript_ingest.read_transcript_from", return_value=(fake_entries, transcript.stat().st_size)), \
         patch("core.transcript_ingest.summarize", return_value="summarized text"), \
         patch("core.transcript_ingest.extract_observations", return_value=fake_obs), \
         patch("core.transcript_ingest.append_to_ephemeral", side_effect=fake_append):
        tick(dry_run=False)

    assert len(captured_obs) == 1
    obs = captured_obs[0][0]
    assert "session_type" in obs
    assert obs["session_type"] in {"code", "writing", "research"}


def test_tick_code_cwd_produces_code_session_type(tmp_path):
    """Entries with a code-like cwd produce session_type='code' on observations."""
    transcript = tmp_path / "projects" / "proj-hash" / "session-st2.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type": "user", "message": {"role": "user", "content": "hi"}}\n')

    cursors_db = tmp_path / "cursors.db"
    with CursorStore(cursors_db) as store:
        store.upsert("session-st2", str(transcript), 0)

    fake_entries = [{"type": "user", "cwd": "/Users/emmahyde/projects/sector",
                     "message": {"role": "user", "content": "hello"}}]
    fake_obs = [{"content": "code finding", "mode": "finding", "importance": 0.7, "tags": []}]
    captured_obs: list[list[dict]] = []

    def fake_append(mem_dir, observations, dry_run=False):
        captured_obs.append(list(observations))
        return len(observations)

    with patch("core.transcript_ingest.discover_transcripts", return_value=[transcript]), \
         patch("core.transcript_ingest.CursorStore", lambda: CursorStore(cursors_db)), \
         patch("core.transcript_ingest.read_transcript_from", return_value=(fake_entries, transcript.stat().st_size)), \
         patch("core.transcript_ingest.summarize", return_value="summarized text"), \
         patch("core.transcript_ingest.extract_observations", return_value=fake_obs), \
         patch("core.transcript_ingest.append_to_ephemeral", side_effect=fake_append):
        tick(dry_run=False)

    assert len(captured_obs) == 1
    assert captured_obs[0][0]["session_type"] == "code"


def test_tick_writing_cwd_produces_writing_session_type(tmp_path):
    """Entries with a writing-like cwd produce session_type='writing' on observations."""
    transcript = tmp_path / "projects" / "proj-hash" / "session-st3.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type": "user", "message": {"role": "user", "content": "hi"}}\n')

    cursors_db = tmp_path / "cursors.db"
    with CursorStore(cursors_db) as store:
        store.upsert("session-st3", str(transcript), 0)

    fake_entries = [{"type": "user", "cwd": "/Users/emmahyde/manuscript/chapter-01",
                     "message": {"role": "user", "content": "hello"}}]
    fake_obs = [{"content": "writing finding", "mode": "finding", "importance": 0.7, "tags": []}]
    captured_obs: list[list[dict]] = []

    def fake_append(mem_dir, observations, dry_run=False):
        captured_obs.append(list(observations))
        return len(observations)

    with patch("core.transcript_ingest.discover_transcripts", return_value=[transcript]), \
         patch("core.transcript_ingest.CursorStore", lambda: CursorStore(cursors_db)), \
         patch("core.transcript_ingest.read_transcript_from", return_value=(fake_entries, transcript.stat().st_size)), \
         patch("core.transcript_ingest.summarize", return_value="summarized text"), \
         patch("core.transcript_ingest.extract_observations", return_value=fake_obs), \
         patch("core.transcript_ingest.append_to_ephemeral", side_effect=fake_append):
        tick(dry_run=False)

    assert len(captured_obs) == 1
    assert captured_obs[0][0]["session_type"] == "writing"


# ---------------------------------------------------------------------------
# Content-hash dedup — Task 1.2
# ---------------------------------------------------------------------------


class TestContentHashDedup:
    def test_exact_duplicate_dropped_highest_importance_retained(self):
        """Identical content+facts → second copy dropped; highest-importance copy kept."""
        obs_a = {"content": "Auth uses JWT", "facts": ["JWT", "24h TTL"], "importance": 0.6}
        obs_b = {"content": "Auth uses JWT", "facts": ["JWT", "24h TTL"], "importance": 0.9}
        deduped, n_dropped = _dedupe_observations([obs_a, obs_b])
        assert n_dropped == 1
        assert len(deduped) == 1
        assert deduped[0]["importance"] == 0.9

    def test_paraphrase_both_kept(self):
        """Different wording for the same meaning → both kept (no false-positive collapse)."""
        obs_a = {"content": "Auth uses JWT with 24h TTL", "facts": [], "importance": 0.7}
        obs_b = {"content": "Authentication relies on JSON web tokens expiring after one day", "facts": [], "importance": 0.7}
        deduped, n_dropped = _dedupe_observations([obs_a, obs_b])
        assert n_dropped == 0
        assert len(deduped) == 2

    def test_empty_list_returns_empty(self):
        """Empty input → ([], 0)."""
        deduped, n_dropped = _dedupe_observations([])
        assert deduped == []
        assert n_dropped == 0

    def test_case_and_punctuation_difference_still_deduped(self):
        """Case and punctuation differences are normalized before hashing → still deduped."""
        obs_a = {"content": "Foo Bar.", "facts": [], "importance": 0.5}
        obs_b = {"content": "foo bar", "facts": [], "importance": 0.7}
        deduped, n_dropped = _dedupe_observations([obs_a, obs_b])
        assert n_dropped == 1
        assert len(deduped) == 1
        assert deduped[0]["importance"] == 0.7
