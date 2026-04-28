"""
Tests for core/session_detector.py — Sprint B WS-G / LLME-F9.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.session_detector import (
    detect_session_type,
    detect_session_type_from_cwd,
    detect_session_type_from_tools,
)


# ---------------------------------------------------------------------------
# detect_session_type_from_cwd
# ---------------------------------------------------------------------------


def test_cwd_sector_project_is_code():
    assert detect_session_type_from_cwd("/Users/emmahyde/projects/sector") == "code"


def test_cwd_memesis_project_is_code():
    assert detect_session_type_from_cwd("/Users/emmahyde/projects/memesis") == "code"


def test_cwd_generic_projects_dir_is_code():
    assert detect_session_type_from_cwd("/Users/emmahyde/projects/myapp") == "code"


def test_cwd_manuscript_is_writing():
    assert detect_session_type_from_cwd("/Users/emmahyde/manuscript/chapter-01") == "writing"


def test_cwd_chapter_is_writing():
    assert detect_session_type_from_cwd("/Users/emmahyde/novel/chapter-02") == "writing"


def test_cwd_prose_dir_is_writing():
    assert detect_session_type_from_cwd("/home/user/prose/draft-v3") == "writing"


def test_cwd_draft_is_writing():
    assert detect_session_type_from_cwd("/Users/emmahyde/drafts/story") == "writing"


def test_cwd_external_references_is_research():
    assert detect_session_type_from_cwd("/Users/emmahyde/projects/external_references") == "research"


def test_cwd_papers_dir_is_research():
    assert detect_session_type_from_cwd("/Users/emmahyde/papers/ml-survey") == "research"


def test_cwd_research_dir_is_research():
    assert detect_session_type_from_cwd("/Users/emmahyde/research/notes") == "research"


def test_cwd_none_returns_none():
    assert detect_session_type_from_cwd(None) is None


def test_cwd_empty_string_returns_none():
    assert detect_session_type_from_cwd("") is None


def test_cwd_unknown_returns_none():
    assert detect_session_type_from_cwd("/var/log/syslog") is None


# ---------------------------------------------------------------------------
# detect_session_type_from_tools
# ---------------------------------------------------------------------------


def test_tools_edit_bash_py_files_is_code():
    tool_uses = [
        {"tool_name": "Edit", "file_path": "/project/main.py"},
        {"tool_name": "Bash", "file_path": ""},
        {"tool_name": "Edit", "file_path": "/project/core/utils.py"},
    ]
    assert detect_session_type_from_tools(tool_uses) == "code"


def test_tools_edit_cs_files_is_code():
    tool_uses = [
        {"tool_name": "Edit", "file_path": "/project/Game.cs"},
        {"tool_name": "Write", "file_path": "/project/Player.cs"},
    ]
    assert detect_session_type_from_tools(tool_uses) == "code"


def test_tools_read_md_and_webfetch_is_research():
    tool_uses = [
        {"tool_name": "Read", "file_path": "/notes/paper-summary.md"},
        {"tool_name": "WebFetch", "file_path": ""},
    ]
    assert detect_session_type_from_tools(tool_uses) == "research"


def test_tools_websearch_and_md_reads_is_research():
    tool_uses = [
        {"tool_name": "WebSearch", "file_path": ""},
        {"tool_name": "Read", "file_path": "/docs/overview.md"},
        {"tool_name": "Read", "file_path": "/docs/details.md"},
    ]
    assert detect_session_type_from_tools(tool_uses) == "research"


def test_tools_many_md_reads_no_code_is_research():
    tool_uses = [
        {"tool_name": "Read", "file_path": "/notes/a.md"},
        {"tool_name": "Read", "file_path": "/notes/b.md"},
        {"tool_name": "Read", "file_path": "/notes/c.md"},
    ]
    assert detect_session_type_from_tools(tool_uses) == "research"


def test_tools_edit_txt_prose_no_code_is_writing():
    tool_uses = [
        {"tool_name": "Edit", "file_path": "/manuscript/chapter01.txt"},
        {"tool_name": "Write", "file_path": "/manuscript/chapter02.txt"},
    ]
    assert detect_session_type_from_tools(tool_uses) == "writing"


def test_tools_empty_returns_none():
    assert detect_session_type_from_tools([]) is None


def test_tools_ambiguous_returns_none():
    # Only Read on non-md, non-code, non-prose — no strong signal
    tool_uses = [
        {"tool_name": "Read", "file_path": "/some/file.json"},
    ]
    # .json is not in _CODE_EXTENSIONS, not .md, not prose — expect None
    result = detect_session_type_from_tools(tool_uses)
    assert result is None


# ---------------------------------------------------------------------------
# detect_session_type (combined)
# ---------------------------------------------------------------------------


def test_combined_code_cwd_wins():
    tool_uses = [{"tool_name": "WebFetch", "file_path": ""}]
    # cwd signals code; tool mix would say research — cwd wins
    result = detect_session_type("/Users/emmahyde/projects/sector", tool_uses)
    assert result == "code"


def test_combined_no_cwd_tool_mix_code():
    tool_uses = [
        {"tool_name": "Edit", "file_path": "/app/main.py"},
        {"tool_name": "Bash", "file_path": ""},
    ]
    result = detect_session_type(None, tool_uses)
    assert result == "code"


def test_combined_no_cwd_tool_mix_research():
    tool_uses = [
        {"tool_name": "WebFetch", "file_path": ""},
        {"tool_name": "Read", "file_path": "/notes/ref.md"},
    ]
    result = detect_session_type(None, tool_uses)
    assert result == "research"


def test_combined_writing_cwd():
    result = detect_session_type("/Users/emmahyde/manuscript/chapter-01", None)
    assert result == "writing"


def test_combined_ambiguous_falls_back_to_default():
    result = detect_session_type(None, None)
    assert result == "code"


def test_combined_ambiguous_tools_falls_back_to_default():
    tool_uses = [{"tool_name": "Read", "file_path": "/some/file.json"}]
    result = detect_session_type(None, tool_uses)
    assert result == "code"


def test_combined_custom_default():
    result = detect_session_type(None, None, default="research")
    assert result == "research"
