"""Tests for core/code_refs.py — task #18.

Coverage:
  - regex parser on Python / TypeScript / Ruby / C# samples
  - LLM-override merge logic (mocked call_llm — not used directly here,
    but merge_code_refs is the public seam that callers use after calling
    the LLM)
  - end-to-end ingest assertion that the code_refs column is populated
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.code_refs import extract_code_refs, merge_code_refs
from core.database import init_db, close_db
from core.models import Memory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base(tmp_path):
    """Initialise a temp database; never touch the real ~/.claude/memory."""
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path
    close_db()


# ---------------------------------------------------------------------------
# 1. Regex parser — Python samples
# ---------------------------------------------------------------------------

class TestExtractCodeRefsPython:

    def test_dotted_module_symbol(self):
        content = "core.llm.call_llm is the canonical LLM entry point."
        refs = extract_code_refs(content)
        symbols = [r["symbol"] for r in refs]
        assert "core.llm.call_llm" in symbols

    def test_dotted_file_inferred(self):
        content = "Use core.models.Memory to access the DB."
        refs = extract_code_refs(content)
        entry = next((r for r in refs if r["symbol"] == "core.models.Memory"), None)
        assert entry is not None
        assert entry["file"] == "core/models.py"
        assert entry["lang"] == "py"

    def test_file_path_py(self):
        content = "See core/consolidator.py for the write path."
        refs = extract_code_refs(content)
        files = [r["file"] for r in refs]
        assert "core/consolidator.py" in files

    def test_file_path_with_line(self):
        content = "lifecycle.py:221 mints unresolved contradicts edges."
        refs = extract_code_refs(content)
        entry = next((r for r in refs if r.get("line") == 221), None)
        assert entry is not None
        assert entry["lang"] == "py"

    def test_backtick_symbol(self):
        content = "Call `Memory.create` with code_refs populated."
        refs = extract_code_refs(content)
        symbols = [r["symbol"] for r in refs]
        assert "Memory.create" in symbols

    def test_pascal_case_class(self):
        content = "ConsolidationDecision validates the LLM output."
        refs = extract_code_refs(content)
        symbols = [r["symbol"] for r in refs]
        assert "ConsolidationDecision" in symbols

    def test_empty_content(self):
        assert extract_code_refs("") == []

    def test_none_content(self):
        assert extract_code_refs(None) == []  # type: ignore[arg-type]

    def test_no_code_refs_in_prose(self):
        content = "Emma prefers direct communication over elaborate delegation patterns."
        refs = extract_code_refs(content)
        # Prose should not produce code refs (no dotted symbols, no file paths)
        assert refs == []

    def test_deduplication(self):
        content = "core.llm.call_llm is called here; core.llm.call_llm is also called there."
        refs = extract_code_refs(content)
        symbols = [r["symbol"] for r in refs]
        assert symbols.count("core.llm.call_llm") == 1


# ---------------------------------------------------------------------------
# 2. Regex parser — TypeScript samples
# ---------------------------------------------------------------------------

class TestExtractCodeRefsTypeScript:

    def test_ts_file_path(self):
        content = "src/components/App.tsx renders the root component."
        refs = extract_code_refs(content)
        entry = next((r for r in refs if "App.tsx" in (r.get("file") or "")), None)
        assert entry is not None
        assert entry["lang"] == "ts"

    def test_js_file_path(self):
        content = "lib/index.js exports the public API."
        refs = extract_code_refs(content)
        entry = next((r for r in refs if "index.js" in (r.get("file") or "")), None)
        assert entry is not None
        assert entry["lang"] == "js"

    def test_ts_with_line(self):
        content = "Error occurs at src/client.ts:88 during initialisation."
        refs = extract_code_refs(content)
        entry = next((r for r in refs if r.get("line") == 88), None)
        assert entry is not None
        assert entry["lang"] == "ts"


# ---------------------------------------------------------------------------
# 3. Regex parser — Ruby / C# samples
# ---------------------------------------------------------------------------

class TestExtractCodeRefsOtherLangs:

    def test_ruby_file(self):
        content = "lib/client.rb wraps the HTTP session."
        refs = extract_code_refs(content)
        entry = next((r for r in refs if "client.rb" in (r.get("file") or "")), None)
        assert entry is not None
        assert entry["lang"] == "rb"

    def test_csharp_file(self):
        content = "Services/MemoryService.cs implements IMemoryService."
        refs = extract_code_refs(content)
        entry = next((r for r in refs if "MemoryService.cs" in (r.get("file") or "")), None)
        assert entry is not None
        assert entry["lang"] == "cs"

    def test_csharp_with_line(self):
        content = "See Models/Memory.cs:42 for the field definition."
        refs = extract_code_refs(content)
        entry = next((r for r in refs if r.get("line") == 42), None)
        assert entry is not None
        assert entry["lang"] == "cs"


# ---------------------------------------------------------------------------
# 4. LLM-override merge logic
# ---------------------------------------------------------------------------

class TestMergeCodeRefs:

    def test_llm_wins_when_valid(self):
        regex_refs = [{"symbol": "core.models.Memory", "file": "core/models.py", "lang": "py", "line": None}]
        llm_refs = [{"symbol": "Memory.save", "file": "core/models.py", "lang": "py", "line": 120}]
        result = merge_code_refs(regex_refs, llm_refs)
        assert result == llm_refs

    def test_regex_fallback_on_none_llm(self):
        regex_refs = [{"symbol": "core.llm.call_llm", "file": "core/llm.py", "lang": "py", "line": None}]
        result = merge_code_refs(regex_refs, None)
        assert result == regex_refs

    def test_regex_fallback_on_empty_llm(self):
        regex_refs = [{"symbol": "core.llm.call_llm", "file": "core/llm.py", "lang": "py", "line": None}]
        result = merge_code_refs(regex_refs, [])
        assert result == regex_refs

    def test_regex_fallback_on_invalid_llm(self):
        """LLM output missing 'symbol' key falls back to regex."""
        regex_refs = [{"symbol": "core.llm.call_llm", "file": "core/llm.py", "lang": "py", "line": None}]
        bad_llm_refs = [{"file": "core/models.py"}]  # no 'symbol' key
        result = merge_code_refs(regex_refs, bad_llm_refs)
        assert result == regex_refs

    def test_regex_fallback_on_non_list_llm(self):
        regex_refs = [{"symbol": "Foo.bar", "file": None, "lang": None, "line": None}]
        result = merge_code_refs(regex_refs, "not a list")  # type: ignore[arg-type]
        assert result == regex_refs

    def test_both_empty(self):
        result = merge_code_refs([], None)
        assert result == []

    def test_llm_partial_fields_accepted(self):
        """LLM only needs 'symbol'; other fields are optional."""
        regex_refs = []
        llm_refs = [{"symbol": "MyClass"}]
        result = merge_code_refs(regex_refs, llm_refs)
        assert result == llm_refs


# ---------------------------------------------------------------------------
# 5. End-to-end ingest: code_refs column populated
# ---------------------------------------------------------------------------

class TestIngestCodeRefsColumn:
    """Verify that ingest write path populates code_refs on Memory rows."""

    def test_memory_create_populates_code_refs(self, base):
        """Direct Memory.create with code_refs stores JSON in DB."""
        from core.code_refs import extract_code_refs
        content = "core.llm.call_llm is the canonical LLM entry point in core/llm.py."
        refs = extract_code_refs(content)

        mem = Memory.create(
            stage="consolidated",
            title="call_llm entry point",
            content=content,
            importance=0.6,
            code_refs=json.dumps(refs),
            project="test-project",
        )

        # Re-fetch from DB to confirm persistence
        fetched = Memory.get_by_id(mem.id)
        stored = json.loads(fetched.code_refs)
        assert isinstance(stored, list)
        assert len(stored) > 0
        symbols = [r["symbol"] for r in stored]
        assert "core.llm.call_llm" in symbols

    def test_memory_create_empty_code_refs(self, base):
        """code_refs=[] (no matches) stores as valid JSON empty array."""
        content = "Emma prefers direct communication."
        refs = extract_code_refs(content)  # expects []

        mem = Memory.create(
            stage="consolidated",
            title="communication preference",
            content=content,
            importance=0.5,
            code_refs=json.dumps(refs),
            project="test-project",
        )

        fetched = Memory.get_by_id(mem.id)
        stored = json.loads(fetched.code_refs)
        assert stored == []

    def test_memory_create_null_code_refs(self, base):
        """code_refs=NULL is allowed (old rows before migration)."""
        mem = Memory.create(
            stage="consolidated",
            title="old-style row",
            content="some content",
            importance=0.4,
            project="test-project",
        )
        fetched = Memory.get_by_id(mem.id)
        assert fetched.code_refs is None

    def test_ingest_one_populates_code_refs(self, base, tmp_path):
        """NativeMemoryIngestor.ingest() populates code_refs on ingested Memory rows."""
        from core.ingest import NativeMemoryIngestor, find_native_memory_dir

        mem_dir = tmp_path / "native"
        mem_dir.mkdir()
        (mem_dir / "MEMORY.md").write_text(
            "- [LLM Entry](llm_entry.md) — call_llm is the canonical path\n",
            encoding="utf-8",
        )
        (mem_dir / "llm_entry.md").write_text(
            "---\nname: LLM Entry\ndescription: call_llm is the canonical path\ntype: project\n---\n\n"
            "core.llm.call_llm is the entry point for all LLM calls in core/llm.py.\n",
            encoding="utf-8",
        )

        ingestor = NativeMemoryIngestor()
        # Patch find_native_memory_dir so ingest() picks up our temp dir
        with patch("core.ingest.find_native_memory_dir", return_value=mem_dir):
            result = ingestor.ingest()

        assert result["ingested"], "Expected at least one memory to be ingested"

        mem = Memory.select().where(Memory.title == "LLM Entry").get()
        assert mem.code_refs is not None
        stored = json.loads(mem.code_refs)
        assert isinstance(stored, list)
        symbols = [r["symbol"] for r in stored]
        # The content references core.llm.call_llm and core/llm.py
        assert any("call_llm" in s for s in symbols) or any(
            "llm" in (r.get("file") or "") for r in stored
        )
