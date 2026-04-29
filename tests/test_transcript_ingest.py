import sys
from pathlib import Path
from datetime import date
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cursors import CursorStore
from core.transcript_ingest import (  # type: ignore[import]
    tick,
    extract_observations,
    _dedupe_observations,
    _merge_card_affect,
    _parse_extract_response,
    _refine_observations,
)


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
         patch("core.transcript_ingest.read_transcript_from", return_value=(fake_entries, transcript.stat().st_size, None)), \
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
         patch("core.transcript_ingest.read_transcript_from", return_value=(fake_entries, transcript.stat().st_size, None)), \
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
         patch("core.transcript_ingest.read_transcript_from", return_value=(fake_entries, transcript.stat().st_size, None)), \
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


# ---------------------------------------------------------------------------
# Merge card affect into session affect — Task 2.1
# ---------------------------------------------------------------------------


class TestMergeCardAffect:
    def test_friction_card_overrides_neutral_base_valence(self):
        """A card with user_affect_valence='friction' should override a neutral base."""
        base = {"dominant_valence": "neutral", "max_intensity": 0.0}
        cards = [{"user_affect_valence": "friction", "user_reaction": "gave up"}]
        result = _merge_card_affect(cards, base)
        assert result["dominant_valence"] == "friction"

    def test_mixed_when_positive_base_and_friction_cards(self):
        """Positive base valence + friction card → 'mixed'."""
        base = {"dominant_valence": "delight", "max_intensity": 0.5}
        cards = [{"user_affect_valence": "friction", "user_reaction": "retried three times"}]
        result = _merge_card_affect(cards, base)
        assert result["dominant_valence"] == "mixed"

    def test_reactions_accumulate_deduped_order_preserved_cap_at_8(self):
        """user_reaction strings accumulate in card_reactions; deduped, order preserved, max 8."""
        base = {"dominant_valence": "neutral", "max_intensity": 0.0}
        reactions = [f"reaction_{i}" for i in range(12)]
        cards = [{"user_affect_valence": "friction", "user_reaction": r} for r in reactions]
        # Add a duplicate to verify dedup
        cards.append({"user_affect_valence": "neutral", "user_reaction": "reaction_0"})
        result = _merge_card_affect(cards, base)
        assert len(result["card_reactions"]) == 8
        assert result["card_reactions"][0] == "reaction_0"
        assert result["card_reactions"][1] == "reaction_1"
        # No duplicates
        assert len(set(result["card_reactions"])) == len(result["card_reactions"])

    def test_empty_cards_returns_base_unchanged(self):
        """Empty card list returns base dict unchanged."""
        base = {"dominant_valence": "friction", "max_intensity": 0.8}
        result = _merge_card_affect([], base)
        assert result == base

    def test_cards_without_affect_fields_return_base_unchanged(self):
        """Cards missing user_reaction and user_affect_valence do not alter base."""
        base = {"dominant_valence": "neutral", "max_intensity": 0.0}
        cards = [{"title": "Some issue", "summary": "no affect fields"}]
        result = _merge_card_affect(cards, base)
        assert result["dominant_valence"] == "neutral"
        assert result.get("card_reactions", []) == []

    def test_does_not_mutate_base(self):
        """_merge_card_affect returns a copy; base dict is not mutated."""
        base = {"dominant_valence": "neutral", "max_intensity": 0.0}
        cards = [{"user_affect_valence": "friction", "user_reaction": "x"}]
        result = _merge_card_affect(cards, base)
        assert base["dominant_valence"] == "neutral"
        assert result["dominant_valence"] == "friction"


# ---------------------------------------------------------------------------
# JSON repair + skip-reason persistence — Task 2.2
# ---------------------------------------------------------------------------


class TestJsonRepair:
    def test_truncated_array_repaired_incomplete_obs_dropped(self):
        """Truncated array missing closing ] → repairs to valid obs; incomplete second obs dropped."""
        drop_stats: dict = {}
        raw = '[{"content": "first obs", "importance": 0.5}, {"content": "trunc'
        obs, reason = _parse_extract_response(raw, drop_stats=drop_stats)
        # The complete first obs should be recovered
        assert len(obs) == 1
        assert obs[0]["content"] == "first obs"
        assert reason is None
        assert drop_stats.get("parse_errors_repaired", 0) == 1

    def test_well_formed_array_unchanged_no_repair_counter(self):
        """Well-formed array parses normally; no repair counter incremented."""
        import json
        drop_stats: dict = {}
        raw = json.dumps([{"content": "clean obs", "importance": 0.6}])
        obs, reason = _parse_extract_response(raw, drop_stats=drop_stats)
        assert len(obs) == 1
        assert obs[0]["content"] == "clean obs"
        assert drop_stats.get("parse_errors_repaired", 0) == 0

    def test_unrecoverable_garbage_returns_empty_no_counter(self):
        """Completely unparseable input → [], no exception, no repair counter increment."""
        drop_stats: dict = {}
        raw = "totally not json at all }{]["
        obs, reason = _parse_extract_response(raw, drop_stats=drop_stats)
        assert obs == []
        assert reason is None
        # Repair counter should NOT increment for garbage that can't be repaired
        assert drop_stats.get("parse_errors_repaired", 0) == 0

    def test_skip_reason_returned_on_intentional_skip(self):
        """Intentional skip dict returns the reason string as second element,
        prefixed with the failed gate (or [unspecified] if absent)."""
        import json
        raw = json.dumps({
            "skipped": True,
            "failed_gate": "novel",
            "reason": "no meaningful signal",
        })
        obs, reason = _parse_extract_response(raw)
        assert obs == []
        assert reason == "[novel] no meaningful signal"

        # Backwards-compat: missing failed_gate marked unspecified
        raw2 = json.dumps({"skipped": True, "reason": "no meaningful signal"})
        _, reason2 = _parse_extract_response(raw2)
        assert reason2 == "[unspecified] no meaningful signal"

    def test_skip_reason_included_in_skips_list(self):
        """extract_observations_hierarchical includes reason in skip records when present."""
        import json
        from unittest.mock import patch, MagicMock
        from core.transcript_ingest import extract_observations_hierarchical
        from core.extraction_affect import WindowAffect

        skip_response = json.dumps({"skipped": True, "reason": "window too short"})
        fake_affect = MagicMock(spec=WindowAffect)
        fake_affect.max_boost = 0.0
        fake_affect.valence = "neutral"
        fake_affect.has_repetition = False
        fake_affect.has_pushback = False
        fake_affect.evidence_quotes = []
        fake_affect.to_dict.return_value = {"valence": "neutral", "max_boost": 0.0}

        with patch("core.transcript_ingest.iter_windows", return_value=["window text"]), \
             patch("core.transcript_ingest.aggregate_window_affect", return_value=fake_affect), \
             patch("core.transcript_ingest.call_llm_batch", return_value=[skip_response]), \
             patch("core.transcript_ingest.summarize", return_value="synopsis"), \
             patch("core.transcript_ingest.synthesize_issue_cards",
                   return_value=([], [], {"outcome": "skipped"})):
            result = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                refine=False,
            )

        skips = result["skips"]
        assert len(skips) == 1
        assert skips[0].get("reason") == "[unspecified] window too short"


# ---------------------------------------------------------------------------
# _refine_observations — Wave 3 Wu-2021 refine pass
# ---------------------------------------------------------------------------


class TestRefineObservations:
    def _make_obs(self, n: int) -> list[dict]:
        return [
            {"content": f"observation {i}", "facts": [f"fact {i}"], "importance": 0.5 + i * 0.05}
            for i in range(n)
        ]

    def test_empty_obs_returns_empty_no_llm_call(self):
        """Empty input → ([], {"outcome": "empty"}), call_llm NOT called."""
        with patch("core.transcript_ingest.call_llm") as mock_llm:
            result, stats = _refine_observations([], "synopsis", {})
        mock_llm.assert_not_called()
        assert result == []
        assert stats["outcome"] == "empty"
        assert stats["merges"] == 0
        assert stats["rescores"] == 0

    def test_three_or_fewer_obs_skipped_no_llm_call(self):
        """≤3 obs → returns input unchanged, outcome=skipped_too_few, no LLM call."""
        obs = self._make_obs(3)
        with patch("core.transcript_ingest.call_llm") as mock_llm:
            result, stats = _refine_observations(obs, "synopsis", {})
        mock_llm.assert_not_called()
        assert result is obs
        assert stats["outcome"] == "skipped_too_few"
        assert stats["merges"] == 0
        assert stats["rescores"] == 0

    def test_happy_path_merge_and_rescore(self):
        """5 obs in, LLM returns 4 after merge + 1 rescore → correct result and stats."""
        import json
        obs = self._make_obs(5)
        merged_obs = self._make_obs(4)  # one fewer after merge
        llm_response = json.dumps({
            "refined": merged_obs,
            "merges": [{"merged_into_index": 0, "from_indices": [4], "reason": "paraphrase"}],
            "rescores": [{"index": 2, "old": 0.6, "new": 0.75, "reason": "session-wide pattern"}],
        })
        with patch("core.transcript_ingest.call_llm", return_value=llm_response):
            result, stats = _refine_observations(obs, "synopsis", {"dominant_valence": "friction"})
        assert len(result) == 4
        assert stats["outcome"] == "ok"
        assert stats["merges"] == 1
        assert stats["rescores"] == 1
        assert len(stats["merges_detail"]) == 1
        assert len(stats["rescores_detail"]) == 1

    def test_llm_exception_returns_pre_refine_list(self):
        """LLM raises Exception → returns original obs, outcome=llm_error, error in stats."""
        obs = self._make_obs(5)
        with patch("core.transcript_ingest.call_llm", side_effect=Exception("network timeout")):
            result, stats = _refine_observations(obs, "synopsis", {})
        assert result is obs
        assert stats["outcome"] == "llm_error"
        assert "network timeout" in stats["error"]
        assert stats["merges"] == 0
        assert stats["rescores"] == 0

    def test_json_parse_error_returns_pre_refine_list(self):
        """LLM returns garbage JSON → returns original obs, outcome=parse_error."""
        obs = self._make_obs(5)
        with patch("core.transcript_ingest.call_llm", return_value="not valid json }{"):
            result, stats = _refine_observations(obs, "synopsis", {})
        assert result is obs
        assert stats["outcome"] == "parse_error"
        assert stats["merges"] == 0
        assert stats["rescores"] == 0

    def test_missing_refined_key_returns_pre_refine_list(self):
        """Valid JSON but missing 'refined' key → returns original obs, outcome=missing_refined."""
        import json
        obs = self._make_obs(5)
        llm_response = json.dumps({"merges": [], "rescores": []})
        with patch("core.transcript_ingest.call_llm", return_value=llm_response):
            result, stats = _refine_observations(obs, "synopsis", {})
        assert result is obs
        assert stats["outcome"] == "missing_refined"
        assert stats["merges"] == 0
        assert stats["rescores"] == 0

    def test_integration_refine_true_populates_report(self):
        """extract_observations_hierarchical with refine=True populates report['refine']."""
        import json
        from unittest.mock import MagicMock
        from core.transcript_ingest import extract_observations_hierarchical
        from core.extraction_affect import WindowAffect

        # 5 obs with distinct content so dedup keeps all (>3 triggers refine)
        topics = ["authentication", "database", "caching", "networking", "logging"]
        obs_list = [
            {"content": f"{t} configuration uses tls", "facts": [f"{t} requires ssl cert"], "importance": 0.5}
            for t in topics
        ]
        batch_response = json.dumps(obs_list)

        fake_affect = MagicMock(spec=WindowAffect)
        fake_affect.max_boost = 0.1
        fake_affect.valence = "neutral"
        fake_affect.has_repetition = False
        fake_affect.has_pushback = False
        fake_affect.evidence_quotes = []
        fake_affect.importance_prior = 0.0
        fake_affect.to_dict.return_value = {"valence": "neutral", "max_boost": 0.1}

        refined_obs = obs_list[:4]
        refined_response = json.dumps({
            "refined": refined_obs,
            "merges": [{"merged_into_index": 0, "from_indices": [4], "reason": "dup"}],
            "rescores": [],
        })

        with patch("core.transcript_ingest.iter_windows", return_value=["window text"]), \
             patch("core.transcript_ingest.aggregate_window_affect", return_value=fake_affect), \
             patch("core.transcript_ingest.call_llm_batch", return_value=[batch_response]), \
             patch("core.transcript_ingest.call_llm", return_value=refined_response), \
             patch("core.transcript_ingest.summarize", return_value="synopsis text"), \
             patch("core.transcript_ingest.synthesize_issue_cards",
                   return_value=([], refined_obs, {"outcome": "ok", "card_count": 0, "orphan_count": 4})):
            result = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                refine=True,
            )

        assert "refine" in result
        assert result["refine"]["outcome"] == "ok"

    def test_integration_refine_false_returns_skipped(self):
        """extract_observations_hierarchical with refine=False sets report['refine']['outcome'] == 'skipped'."""
        import json
        from unittest.mock import MagicMock
        from core.transcript_ingest import extract_observations_hierarchical
        from core.extraction_affect import WindowAffect

        obs_list = [{"content": "obs", "facts": ["fact"], "importance": 0.5}]
        batch_response = json.dumps(obs_list)

        fake_affect = MagicMock(spec=WindowAffect)
        fake_affect.max_boost = 0.0
        fake_affect.valence = "neutral"
        fake_affect.has_repetition = False
        fake_affect.has_pushback = False
        fake_affect.evidence_quotes = []
        fake_affect.importance_prior = 0.0
        fake_affect.to_dict.return_value = {"valence": "neutral", "max_boost": 0.0}

        with patch("core.transcript_ingest.iter_windows", return_value=["window text"]), \
             patch("core.transcript_ingest.aggregate_window_affect", return_value=fake_affect), \
             patch("core.transcript_ingest.call_llm_batch", return_value=[batch_response]), \
             patch("core.transcript_ingest.summarize", return_value="synopsis text"):
            result = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                refine=False,
            )

        assert "refine" in result
        assert result["refine"]["outcome"] == "skipped"


# ---------------------------------------------------------------------------
# Pre-filter low-affect research windows — Task 2.1
# ---------------------------------------------------------------------------


class TestPrefilterResearchNeutral:
    """Gate: PREFILTER_RESEARCH_NEUTRAL skips LLM calls on research windows
    with max_boost == 0.0, logs them as pre_filtered_low_affect."""

    def _make_fake_affect(self, max_boost: float = 0.0, valence: str = "neutral"):
        from unittest.mock import MagicMock
        from core.extraction_affect import WindowAffect
        a = MagicMock(spec=WindowAffect)
        a.max_boost = max_boost
        a.valence = valence
        a.has_repetition = False
        a.has_pushback = False
        a.evidence_quotes = []
        a.importance_prior = 0.0
        a.to_dict.return_value = {"valence": valence, "max_boost": max_boost}
        return a

    def test_research_zero_affect_skipped(self):
        """Research session + max_boost==0.0 → windows skipped, call_llm_batch not called for them."""
        import json
        from unittest.mock import patch, call as mock_call
        from core.transcript_ingest import extract_observations_hierarchical
        import core.transcript_ingest as ti

        fake_affect = self._make_fake_affect(max_boost=0.0)

        with patch.object(ti, "PREFILTER_RESEARCH_NEUTRAL", True), \
             patch("core.transcript_ingest.iter_windows", return_value=["win1", "win2"]), \
             patch("core.transcript_ingest.aggregate_window_affect", return_value=fake_affect), \
             patch("core.transcript_ingest.call_llm_batch") as mock_batch, \
             patch("core.transcript_ingest.summarize", return_value="synopsis"), \
             patch("core.transcript_ingest.synthesize_issue_cards",
                   return_value=([], [], {"outcome": "skipped"})):
            result = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                session_type="research",
                refine=False,
            )

        # Both windows have max_boost=0.0 → both filtered, call_llm_batch called with empty list
        mock_batch.assert_called_once()
        batch_prompts = mock_batch.call_args[0][0]
        assert batch_prompts == [], f"Expected empty prompts list, got {batch_prompts!r}"

        skips = result["skips"]
        prefilter_skips = [s for s in skips if s["outcome"] == "pre_filtered_low_affect"]
        assert len(prefilter_skips) == 2
        assert all(s["affect_intensity"] == 0.0 for s in prefilter_skips)

    def test_research_nonzero_affect_not_skipped(self):
        """Research session + one window max_boost>0 → that window passes to LLM."""
        import json
        from unittest.mock import patch, MagicMock
        from core.extraction_affect import WindowAffect
        from core.transcript_ingest import extract_observations_hierarchical
        import core.transcript_ingest as ti

        affect_zero = self._make_fake_affect(max_boost=0.0)
        affect_signal = self._make_fake_affect(max_boost=0.3, valence="friction")

        call_count = []

        def fake_aggregate(window_text: str):
            # Return affect_zero for "win1", affect_signal for "win2"
            if window_text == "win1":
                return affect_zero
            return affect_signal

        obs_response = json.dumps([{"content": "finding", "importance": 0.6, "facts": []}])

        with patch.object(ti, "PREFILTER_RESEARCH_NEUTRAL", True), \
             patch("core.transcript_ingest.iter_windows", return_value=["win1", "win2"]), \
             patch("core.transcript_ingest.aggregate_window_affect", side_effect=fake_aggregate), \
             patch("core.transcript_ingest.call_llm_batch", return_value=[obs_response]) as mock_batch, \
             patch("core.transcript_ingest.summarize", return_value="synopsis"), \
             patch("core.transcript_ingest.synthesize_issue_cards",
                   return_value=([], [], {"outcome": "skipped"})):
            result = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                session_type="research",
                refine=False,
            )

        # Only "win2" (max_boost=0.3) passes → batch called with 1 prompt
        mock_batch.assert_called_once()
        batch_prompts = mock_batch.call_args[0][0]
        assert len(batch_prompts) == 1

        skips = result["skips"]
        prefilter_skips = [s for s in skips if s["outcome"] == "pre_filtered_low_affect"]
        assert len(prefilter_skips) == 1  # only win1 was prefiltered
        assert result["prefilter_skipped_count"] == 1

    def test_code_session_not_filtered(self):
        """Code session + max_boost==0.0 → prefilter gate does NOT fire."""
        import json
        from unittest.mock import patch
        from core.transcript_ingest import extract_observations_hierarchical
        import core.transcript_ingest as ti

        fake_affect = self._make_fake_affect(max_boost=0.0)
        obs_response = json.dumps([])

        with patch.object(ti, "PREFILTER_RESEARCH_NEUTRAL", True), \
             patch("core.transcript_ingest.iter_windows", return_value=["win1"]), \
             patch("core.transcript_ingest.aggregate_window_affect", return_value=fake_affect), \
             patch("core.transcript_ingest.call_llm_batch", return_value=[obs_response]) as mock_batch, \
             patch("core.transcript_ingest.summarize", return_value="synopsis"), \
             patch("core.transcript_ingest.synthesize_issue_cards",
                   return_value=([], [], {"outcome": "skipped"})):
            result = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                session_type="code",
                refine=False,
            )

        # Gate doesn't fire for code sessions — batch called with 1 prompt
        mock_batch.assert_called_once()
        batch_prompts = mock_batch.call_args[0][0]
        assert len(batch_prompts) == 1

        prefilter_skips = [s for s in result["skips"] if s["outcome"] == "pre_filtered_low_affect"]
        assert prefilter_skips == []
        assert result["prefilter_skipped_count"] == 0

    def test_constant_false_disables_gate(self, monkeypatch):
        """Setting PREFILTER_RESEARCH_NEUTRAL=False disables the gate entirely."""
        import json
        from unittest.mock import patch
        from core.transcript_ingest import extract_observations_hierarchical
        import core.transcript_ingest as ti

        monkeypatch.setattr(ti, "PREFILTER_RESEARCH_NEUTRAL", False)

        fake_affect = self._make_fake_affect(max_boost=0.0)
        obs_response = json.dumps([])

        with patch("core.transcript_ingest.iter_windows", return_value=["win1", "win2"]), \
             patch("core.transcript_ingest.aggregate_window_affect", return_value=fake_affect), \
             patch("core.transcript_ingest.call_llm_batch", return_value=[obs_response, obs_response]) as mock_batch, \
             patch("core.transcript_ingest.summarize", return_value="synopsis"), \
             patch("core.transcript_ingest.synthesize_issue_cards",
                   return_value=([], [], {"outcome": "skipped"})):
            result = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                session_type="research",
                refine=False,
            )

        # With gate disabled, both windows go to LLM
        batch_prompts = mock_batch.call_args[0][0]
        assert len(batch_prompts) == 2

        prefilter_skips = [s for s in result["skips"] if s["outcome"] == "pre_filtered_low_affect"]
        assert prefilter_skips == []
        assert result["prefilter_skipped_count"] == 0

    def test_prefilter_skipped_count_in_return(self):
        """Return dict always contains 'prefilter_skipped_count' key."""
        import json
        from unittest.mock import patch
        from core.transcript_ingest import extract_observations_hierarchical
        import core.transcript_ingest as ti

        fake_affect = self._make_fake_affect(max_boost=0.0)
        obs_response = json.dumps([])

        # Non-research session — key should still be present (value=0)
        with patch.object(ti, "PREFILTER_RESEARCH_NEUTRAL", True), \
             patch("core.transcript_ingest.iter_windows", return_value=["win1"]), \
             patch("core.transcript_ingest.aggregate_window_affect", return_value=fake_affect), \
             patch("core.transcript_ingest.call_llm_batch", return_value=[obs_response]), \
             patch("core.transcript_ingest.summarize", return_value="synopsis"), \
             patch("core.transcript_ingest.synthesize_issue_cards",
                   return_value=([], [], {"outcome": "skipped"})):
            result = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                session_type="code",
                refine=False,
            )

        assert "prefilter_skipped_count" in result
        assert result["prefilter_skipped_count"] == 0

        # Research session with zero affect — key present and correct count
        with patch.object(ti, "PREFILTER_RESEARCH_NEUTRAL", True), \
             patch("core.transcript_ingest.iter_windows", return_value=["win1", "win2"]), \
             patch("core.transcript_ingest.aggregate_window_affect", return_value=fake_affect), \
             patch("core.transcript_ingest.call_llm_batch", return_value=[]) as mock_batch, \
             patch("core.transcript_ingest.summarize", return_value="synopsis"), \
             patch("core.transcript_ingest.synthesize_issue_cards",
                   return_value=([], [], {"outcome": "skipped"})):
            result2 = extract_observations_hierarchical(
                [{"type": "user", "message": {"role": "user", "content": "hi"}}],
                session_type="research",
                refine=False,
            )

        assert "prefilter_skipped_count" in result2
        assert result2["prefilter_skipped_count"] == 2
