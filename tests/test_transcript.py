"""Tests for core/transcript.py — _detect_cwd and read_transcript_from 3-tuple API."""

import json
from pathlib import Path

import pytest

from core.transcript import _detect_cwd, read_transcript_from


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    """Write a list of dicts as JSONL to path."""
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class TestDetectCwd:
    def test_returns_cwd_from_first_entry(self, tmp_path):
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            {"type": "system", "cwd": "/home/user/myproject"},
            {"type": "user", "message": {"content": "hello world here"}},
        ])
        assert _detect_cwd(p) == "/home/user/myproject"

    def test_returns_cwd_from_non_user_assistant_entry(self, tmp_path):
        """cwd is on attachment/system entries that read_transcript_from filters out."""
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            {"type": "attachment", "cwd": "/projects/sector"},
            {"type": "user", "message": {"content": "hello world here"}},
        ])
        assert _detect_cwd(p) == "/projects/sector"

    def test_returns_none_when_no_cwd(self, tmp_path):
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "hello world here"}},
            {"type": "assistant", "message": {"content": "response text here"}},
        ])
        assert _detect_cwd(p) is None

    def test_returns_none_for_empty_file(self, tmp_path):
        p = tmp_path / "session.jsonl"
        p.write_text("")
        assert _detect_cwd(p) is None

    def test_skips_malformed_lines(self, tmp_path):
        p = tmp_path / "session.jsonl"
        with open(p, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"type": "system", "cwd": "/found/it"}) + "\n")
        assert _detect_cwd(p) == "/found/it"

    def test_stops_at_200_lines(self, tmp_path):
        """cwd after line 200 should NOT be found."""
        p = tmp_path / "session.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            for i in range(201):
                f.write(json.dumps({"type": "user", "index": i}) + "\n")
            f.write(json.dumps({"type": "system", "cwd": "/late/cwd"}) + "\n")
        assert _detect_cwd(p) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        p = tmp_path / "nonexistent.jsonl"
        assert _detect_cwd(p) is None


class TestReadTranscriptFrom:
    def _user_entry(self, text: str) -> dict:
        return {"type": "user", "message": {"content": text}}

    def _assistant_entry(self, text: str) -> dict:
        return {"type": "assistant", "message": {"content": text}}

    def _cwd_entry(self, cwd: str) -> dict:
        return {"type": "attachment", "cwd": cwd}

    def test_returns_3_tuple(self, tmp_path):
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            self._user_entry("What is the capital of France?"),
        ])
        result = read_transcript_from(p, 0)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_cwd_present_returns_cwd_as_third_element(self, tmp_path):
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            self._cwd_entry("/projects/myapp"),
            self._user_entry("Can you help me refactor this function please?"),
            self._assistant_entry("Sure, here is a refactored version of the function."),
        ])
        messages, new_offset, cwd = read_transcript_from(p, 0)
        assert cwd == "/projects/myapp"

    def test_no_cwd_returns_none_as_third_element(self, tmp_path):
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            self._user_entry("Can you help me refactor this function please?"),
            self._assistant_entry("Sure, here is a refactored version of the function."),
        ])
        messages, new_offset, cwd = read_transcript_from(p, 0)
        assert cwd is None

    def test_cwd_detected_when_byte_offset_greater_than_zero(self, tmp_path):
        """cwd is found even when byte_offset skips past the cwd-bearing line."""
        p = tmp_path / "session.jsonl"
        first_line = json.dumps(self._cwd_entry("/projects/skipped")) + "\n"
        second_line = json.dumps(self._user_entry("Can you help me refactor this function please?")) + "\n"
        third_line = json.dumps(self._assistant_entry("Sure, here is a refactored version of the function.")) + "\n"
        p.write_text(first_line + second_line + third_line, encoding="utf-8")

        # byte_offset past the first line — cwd entry is now before the read window
        offset = len(first_line.encode("utf-8"))
        messages, new_offset, cwd = read_transcript_from(p, offset)
        assert cwd == "/projects/skipped"
        # messages only covers lines after the offset
        assert len(messages) >= 1

    def test_offset_logic_unchanged(self, tmp_path):
        """Confirm (messages, new_offset) semantics are unchanged from pre-3-tuple."""
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            self._user_entry("This is the first user message to be read."),
            self._assistant_entry("This is the first assistant response here."),
        ])
        messages, new_offset, cwd = read_transcript_from(p, 0)
        assert len(messages) == 2
        assert new_offset == p.stat().st_size

    def test_at_eof_returns_empty_messages_and_cwd(self, tmp_path):
        """When byte_offset >= file_size, returns ([], file_size, cwd)."""
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            self._cwd_entry("/projects/eof"),
            self._user_entry("Some content that we skip entirely here."),
        ])
        file_size = p.stat().st_size
        messages, new_offset, cwd = read_transcript_from(p, file_size)
        assert messages == []
        assert new_offset == file_size
        assert cwd == "/projects/eof"

    def test_messages_not_affected_by_cwd_detection(self, tmp_path):
        """cwd-bearing entries (non user/assistant) are not included in messages."""
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [
            self._cwd_entry("/projects/clean"),
            self._user_entry("This is a substantial user message for the test."),
            self._assistant_entry("This is a substantial assistant response here."),
        ])
        messages, new_offset, cwd = read_transcript_from(p, 0)
        assert cwd == "/projects/clean"
        assert all(m["role"] in ("user", "assistant") for m in messages)
        assert len(messages) == 2
