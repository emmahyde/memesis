"""Tests for core/transcript_picker.py."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _user_record(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant_record(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": text}}


def _make_transcript(path: Path, n_user: int, n_assistant: int, user_text: str = "hello") -> None:
    records: list[dict] = []
    for _ in range(n_user):
        records.append(_user_record(user_text))
    for _ in range(n_assistant):
        records.append(_assistant_record("ok"))
    _write_jsonl(path, records)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestDiscover:
    def test_returns_empty_when_dir_missing(self, tmp_path):
        from core.transcript_picker import discover

        missing = tmp_path / "nope"
        assert discover(missing) == []

    def test_finds_jsonl_under_project_subdirs(self, tmp_path):
        from core.transcript_picker import discover

        (tmp_path / "proj-a").mkdir()
        (tmp_path / "proj-b").mkdir()
        a = tmp_path / "proj-a" / "sess-1.jsonl"
        b = tmp_path / "proj-b" / "sess-2.jsonl"
        a.write_text("{}\n")
        b.write_text("{}\n")

        out = discover(tmp_path)
        assert a in out
        assert b in out

    def test_skips_non_jsonl(self, tmp_path):
        from core.transcript_picker import discover

        (tmp_path / "proj").mkdir()
        good = tmp_path / "proj" / "ok.jsonl"
        bad = tmp_path / "proj" / "ignore.txt"
        good.write_text("{}\n")
        bad.write_text("noise")

        out = discover(tmp_path)
        assert good in out
        assert bad not in out


# ---------------------------------------------------------------------------
# Prefilter
# ---------------------------------------------------------------------------

class TestPrefilter:
    def test_rejects_short_files(self, tmp_path):
        from core.transcript_picker import prefilter

        short = tmp_path / "short.jsonl"
        _make_transcript(short, n_user=2, n_assistant=2)  # 4 lines < 20
        assert prefilter([short], min_lines=20) == []

    def test_keeps_long_files(self, tmp_path):
        from core.transcript_picker import prefilter

        long_p = tmp_path / "long.jsonl"
        _make_transcript(long_p, n_user=20, n_assistant=20)
        assert prefilter([long_p], min_lines=20) == [long_p]

    def test_rejects_old_files(self, tmp_path):
        from core.transcript_picker import prefilter

        old = tmp_path / "old.jsonl"
        _make_transcript(old, n_user=20, n_assistant=20)
        # Backdate to 60 days ago
        ago = time.time() - (60 * 86400)
        os.utime(str(old), (ago, ago))
        assert prefilter([old], max_age_days=30) == []

    def test_handles_unreadable_path(self, tmp_path):
        from core.transcript_picker import prefilter

        ghost = tmp_path / "does_not_exist.jsonl"
        # Should silently skip rather than crash
        assert prefilter([ghost]) == []


# ---------------------------------------------------------------------------
# User-message extraction
# ---------------------------------------------------------------------------

class TestExtractUserMessages:
    def test_extracts_string_content(self, tmp_path):
        from core.transcript_picker import extract_user_messages

        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            _user_record("hello world"),
            _assistant_record("hi"),
            _user_record("more text"),
        ])
        out = extract_user_messages(p)
        assert "hello world" in out
        assert "more text" in out
        assert "hi" not in out

    def test_extracts_block_array_content(self, tmp_path):
        from core.transcript_picker import extract_user_messages

        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [{
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "block one"},
                    {"type": "image", "source": {"type": "base64"}},
                    {"type": "text", "text": "block two"},
                ],
            },
        }])
        out = extract_user_messages(p)
        assert "block one" in out
        assert "block two" in out

    def test_skips_malformed_lines(self, tmp_path):
        from core.transcript_picker import extract_user_messages

        p = tmp_path / "t.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write(json.dumps(_user_record("good")) + "\n")
            fh.write("{also bad\n")
        assert "good" in extract_user_messages(p)

    def test_truncates_at_max_chars(self, tmp_path):
        from core.transcript_picker import extract_user_messages

        p = tmp_path / "t.jsonl"
        long_text = "x" * 20000
        _write_jsonl(p, [_user_record(long_text)])
        out = extract_user_messages(p, max_chars=1000)
        assert len(out) <= 1000


# ---------------------------------------------------------------------------
# Deterministic scoring
# ---------------------------------------------------------------------------

class TestDeterministicScore:
    def test_friction_density_detected(self, tmp_path):
        from core.transcript_picker import deterministic_score

        p = tmp_path / "frictional.jsonl"
        _make_transcript(
            p, n_user=10, n_assistant=10,
            user_text="this is broken and the test failed and I'm frustrated",
        )
        cand = deterministic_score(p, traces_dir=tmp_path / "no_traces")
        assert cand.friction_density > 0

    def test_decision_density_detected_for_lets(self, tmp_path):
        from core.transcript_picker import deterministic_score

        p = tmp_path / "lets.jsonl"
        _make_transcript(
            p, n_user=10, n_assistant=10,
            user_text="let's go with the second approach instead of the first",
        )
        cand = deterministic_score(p, traces_dir=tmp_path / "no_traces")
        assert cand.decision_density > 0

    def test_decision_density_detected_for_decided(self, tmp_path):
        from core.transcript_picker import deterministic_score

        p = tmp_path / "decided.jsonl"
        _make_transcript(
            p, n_user=10, n_assistant=10,
            user_text="we decided to switch to the new schema. plan is done.",
        )
        cand = deterministic_score(p, traces_dir=tmp_path / "no_traces")
        assert cand.decision_density > 0

    def test_already_traced_penalty(self, tmp_path):
        from core.transcript_picker import deterministic_score

        traces = tmp_path / "traces"
        traces.mkdir()
        # Trace name should match sanitized stem of transcript
        name = "abc123"
        (traces / f"replay-{name}-1.jsonl").write_text("")

        p = tmp_path / f"{name}.jsonl"
        _make_transcript(p, n_user=20, n_assistant=20, user_text="let's decide x")
        cand = deterministic_score(p, traces_dir=traces)
        assert cand.already_traced is True

    def test_empty_user_text_yields_zero_density(self, tmp_path):
        from core.transcript_picker import deterministic_score

        p = tmp_path / "asst-only.jsonl"
        _write_jsonl(p, [_assistant_record("nothing user said")] * 25)
        cand = deterministic_score(p, traces_dir=tmp_path / "no_traces")
        assert cand.friction_density == 0.0
        assert cand.decision_density == 0.0

    def test_score_is_in_unit_interval(self, tmp_path):
        from core.transcript_picker import deterministic_score

        p = tmp_path / "any.jsonl"
        _make_transcript(p, n_user=5, n_assistant=5)
        cand = deterministic_score(p, traces_dir=tmp_path / "no_traces")
        assert 0.0 <= cand.det_score <= 1.0


# ---------------------------------------------------------------------------
# LLM scoring
# ---------------------------------------------------------------------------

class TestLlmScore:
    def test_empty_excerpt_returns_zero(self):
        from core.transcript_picker import llm_score

        out = llm_score("")
        assert out["score"] == 0.0

    def test_parses_clean_json(self):
        from core.transcript_picker import llm_score

        resp = json.dumps({
            "score": 0.85,
            "rationale": "rich technical decisions",
            "themes": ["oauth", "schema-migration"],
            "expected_capture_density": "high",
        })
        with patch("core.transcript_picker.cached_call_llm", return_value=resp):
            out = llm_score("user said: let's switch to oauth flow")
        assert out["score"] == 0.85
        assert "oauth" in out["themes"]
        assert out["expected_capture_density"] == "high"

    def test_strips_markdown_fences(self):
        from core.transcript_picker import llm_score

        resp = "```json\n" + json.dumps({"score": 0.4, "rationale": "ok"}) + "\n```"
        with patch("core.transcript_picker.cached_call_llm", return_value=resp):
            out = llm_score("anything")
        assert out["score"] == 0.4

    def test_clamps_out_of_range_score(self):
        from core.transcript_picker import llm_score

        resp = json.dumps({"score": 5.0, "rationale": "x"})
        with patch("core.transcript_picker.cached_call_llm", return_value=resp):
            out = llm_score("anything")
        assert out["score"] == 1.0

    def test_unparseable_response_falls_back_safely(self):
        from core.transcript_picker import llm_score

        with patch("core.transcript_picker.cached_call_llm", return_value="not json at all"):
            out = llm_score("anything")
        assert out["score"] == 0.0
        assert "unparseable" in out["rationale"].lower()


# ---------------------------------------------------------------------------
# Combined ranking
# ---------------------------------------------------------------------------

class TestRank:
    def test_empty_input_returns_empty(self):
        from core.transcript_picker import rank
        assert rank([]) == []

    def test_combined_score_uses_both_signals(self, tmp_path):
        from core.transcript_picker import rank

        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        _make_transcript(a, n_user=20, n_assistant=20, user_text="let's decide and ship")
        _make_transcript(b, n_user=20, n_assistant=20, user_text="generic chitchat about weather")

        # Mock LLM to clearly prefer "a"
        def fake_llm(prompt, **_):
            if "let's" in prompt:
                return json.dumps({"score": 0.9, "rationale": "decision-rich", "themes": ["ship"], "expected_capture_density": "high"})
            return json.dumps({"score": 0.1, "rationale": "small talk", "themes": [], "expected_capture_density": "low"})

        with patch("core.transcript_picker.cached_call_llm", side_effect=fake_llm):
            out = rank([a, b], top_k=2, traces_dir=tmp_path / "no_traces")

        assert out[0].path == a
        assert out[0].combined_score > out[1].combined_score

    def test_only_top_k_get_llm_score(self, tmp_path):
        from core.transcript_picker import rank

        paths = []
        for i in range(5):
            p = tmp_path / f"t{i}.jsonl"
            _make_transcript(p, n_user=20, n_assistant=20)
            paths.append(p)

        call_count = {"n": 0}

        def counting(prompt, **_):
            call_count["n"] += 1
            return json.dumps({"score": 0.5, "rationale": "ok", "themes": [], "expected_capture_density": "med"})

        with patch("core.transcript_picker.cached_call_llm", side_effect=counting):
            rank(paths, top_k=2, traces_dir=tmp_path / "no_traces")

        assert call_count["n"] == 2

    def test_llm_failure_does_not_crash_rank(self, tmp_path):
        from core.transcript_picker import rank

        p = tmp_path / "t.jsonl"
        _make_transcript(p, n_user=20, n_assistant=20)

        with patch("core.transcript_picker.cached_call_llm", side_effect=RuntimeError("boom")):
            out = rank([p], top_k=1, traces_dir=tmp_path / "no_traces")
        assert len(out) == 1
        assert out[0].llm_score == 0.0


# ---------------------------------------------------------------------------
# End-to-end pick()
# ---------------------------------------------------------------------------

class TestPickEndToEnd:
    def test_pick_runs_full_pipeline(self, tmp_path):
        from core.transcript_picker import pick

        (tmp_path / "proj").mkdir()
        good = tmp_path / "proj" / "good.jsonl"
        _make_transcript(good, n_user=20, n_assistant=20, user_text="let's decide x")

        too_short = tmp_path / "proj" / "short.jsonl"
        _make_transcript(too_short, n_user=2, n_assistant=2)

        with patch(
            "core.transcript_picker.cached_call_llm",
            return_value=json.dumps({"score": 0.8, "rationale": "ok", "themes": [], "expected_capture_density": "high"}),
        ):
            out = pick(base_dir=tmp_path, top_k=5, traces_dir=tmp_path / "no_traces")

        paths = [c.path for c in out]
        assert good in paths
        assert too_short not in paths
