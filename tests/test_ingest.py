"""
Tests for native Claude Code memory ingestion.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ingest import (
    NativeMemoryIngestor,
    parse_frontmatter,
    scan_native_memories,
    find_native_memory_dir,
)
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    return MemoryStore(base_dir=str(tmp_path / "memory"))


@pytest.fixture
def native_dir(tmp_path):
    """Create a mock native Claude Code memory directory."""
    mem_dir = tmp_path / "native_memory"
    mem_dir.mkdir()

    # MEMORY.md index
    (mem_dir / "MEMORY.md").write_text(
        "- [User Role](user_role.md) — User is a senior engineer\n"
        "- [Testing Pref](feedback_testing.md) — Don't mock the database\n"
        "- [Project Goal](project_auth.md) — Auth rewrite for compliance\n",
        encoding="utf-8",
    )

    # Individual memory files
    (mem_dir / "user_role.md").write_text(
        "---\nname: User Role\ndescription: User is a senior engineer focused on backend\ntype: user\n---\n\n"
        "Emma is a senior engineer who thinks like a designer who codes.\n",
        encoding="utf-8",
    )
    (mem_dir / "feedback_testing.md").write_text(
        "---\nname: Testing Preference\ndescription: Don't mock the database in integration tests\ntype: feedback\n---\n\n"
        "Integration tests must hit a real database, not mocks.\n"
        "**Why:** Prior incident where mock/prod divergence masked a broken migration.\n"
        "**How to apply:** Any test touching the DB should use the test database fixture.\n",
        encoding="utf-8",
    )
    (mem_dir / "project_auth.md").write_text(
        "---\nname: Auth Rewrite\ndescription: Auth middleware rewrite driven by legal compliance\ntype: project\n---\n\n"
        "Auth middleware rewrite is driven by legal/compliance requirements.\n"
        "**Why:** Legal flagged session token storage as non-compliant.\n",
        encoding="utf-8",
    )

    return mem_dir


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_extracts_metadata(self):
        text = "---\nname: Test\ndescription: A test\ntype: user\n---\n\nBody here."
        meta, body = parse_frontmatter(text)
        assert meta["name"] == "Test"
        assert meta["type"] == "user"
        assert body == "Body here."

    def test_no_frontmatter(self):
        text = "Just some text."
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_incomplete_frontmatter(self):
        text = "---\nname: Test\nno closing fence"
        meta, body = parse_frontmatter(text)
        assert meta == {}


# ---------------------------------------------------------------------------
# scan_native_memories
# ---------------------------------------------------------------------------

class TestScanNativeMemories:
    def test_finds_linked_files(self, native_dir):
        memories = scan_native_memories(native_dir)
        names = {m["name"] for m in memories}
        assert "User Role" in names
        assert "Testing Preference" in names
        assert "Auth Rewrite" in names

    def test_includes_body(self, native_dir):
        memories = scan_native_memories(native_dir)
        feedback = next(m for m in memories if m["name"] == "Testing Preference")
        assert "real database" in feedback["body"]

    def test_includes_type(self, native_dir):
        memories = scan_native_memories(native_dir)
        user = next(m for m in memories if m["name"] == "User Role")
        assert user["type"] == "user"

    def test_skips_files_without_frontmatter(self, native_dir):
        (native_dir / "no_frontmatter.md").write_text("Just plain text.\n")
        memories = scan_native_memories(native_dir)
        names = {m["name"] for m in memories}
        assert "" not in names  # no name = skipped

    def test_skips_memesis_subdirectories(self, native_dir):
        # Create a file that looks like a memesis stage file
        consolidated = native_dir / "consolidated"
        consolidated.mkdir()
        (consolidated / "something.md").write_text(
            "---\nname: Should Skip\ntype: user\n---\nContent\n"
        )
        memories = scan_native_memories(native_dir)
        names = {m["name"] for m in memories}
        assert "Should Skip" not in names

    def test_empty_directory(self, tmp_path):
        mem_dir = tmp_path / "empty"
        mem_dir.mkdir()
        (mem_dir / "MEMORY.md").write_text("")
        memories = scan_native_memories(mem_dir)
        assert memories == []

    def test_picks_up_unlinked_root_files(self, native_dir):
        """Files at root not in MEMORY.md should still be found."""
        (native_dir / "orphan.md").write_text(
            "---\nname: Orphan Memory\ndescription: Found by scan\ntype: reference\n---\n\nOrphan body.\n"
        )
        memories = scan_native_memories(native_dir)
        names = {m["name"] for m in memories}
        assert "Orphan Memory" in names


# ---------------------------------------------------------------------------
# NativeMemoryIngestor
# ---------------------------------------------------------------------------

class TestIngestor:
    def test_ingests_all_native_memories(self, tmp_store, native_dir, monkeypatch):
        monkeypatch.setattr(
            "core.ingest.find_native_memory_dir",
            lambda ctx: native_dir,
        )
        ingestor = NativeMemoryIngestor(tmp_store)
        result = ingestor.ingest()

        assert len(result["ingested"]) == 3
        assert "User Role" in result["ingested"]
        assert "Testing Preference" in result["ingested"]
        assert result["skipped"] == 0

    def test_ingested_memories_are_consolidated(self, tmp_store, native_dir, monkeypatch):
        monkeypatch.setattr(
            "core.ingest.find_native_memory_dir",
            lambda ctx: native_dir,
        )
        ingestor = NativeMemoryIngestor(tmp_store)
        ingestor.ingest()

        consolidated = tmp_store.list_by_stage("consolidated")
        assert len(consolidated) == 3
        assert all(m["stage"] == "consolidated" for m in consolidated)

    def test_tags_include_observation_type(self, tmp_store, native_dir, monkeypatch):
        monkeypatch.setattr(
            "core.ingest.find_native_memory_dir",
            lambda ctx: native_dir,
        )
        ingestor = NativeMemoryIngestor(tmp_store)
        ingestor.ingest()

        consolidated = tmp_store.list_by_stage("consolidated")
        feedback = next(m for m in consolidated if m["title"] == "Testing Preference")
        assert "type:correction" in feedback["tags"]
        assert "source:native-claude-code" in feedback["tags"]
        assert "native-type:feedback" in feedback["tags"]

    def test_importance_mapped_by_type(self, tmp_store, native_dir, monkeypatch):
        monkeypatch.setattr(
            "core.ingest.find_native_memory_dir",
            lambda ctx: native_dir,
        )
        ingestor = NativeMemoryIngestor(tmp_store)
        ingestor.ingest()

        consolidated = tmp_store.list_by_stage("consolidated")
        feedback = next(m for m in consolidated if m["title"] == "Testing Preference")
        user = next(m for m in consolidated if m["title"] == "User Role")
        assert feedback["importance"] == 0.75  # feedback gets highest
        assert user["importance"] == 0.65

    def test_deduplication_on_second_ingest(self, tmp_store, native_dir, monkeypatch):
        monkeypatch.setattr(
            "core.ingest.find_native_memory_dir",
            lambda ctx: native_dir,
        )
        ingestor = NativeMemoryIngestor(tmp_store)

        first = ingestor.ingest()
        second = ingestor.ingest()

        assert len(first["ingested"]) == 3
        assert len(second["ingested"]) == 0
        assert second["skipped"] == 3

        # Only 3 in store, not 6
        assert len(tmp_store.list_by_stage("consolidated")) == 3

    def test_no_native_dir_returns_empty(self, tmp_store, monkeypatch):
        monkeypatch.setattr(
            "core.ingest.find_native_memory_dir",
            lambda ctx: None,
        )
        ingestor = NativeMemoryIngestor(tmp_store)
        result = ingestor.ingest()

        assert result["ingested"] == []
        assert result["source"] is None

    def test_content_includes_description(self, tmp_store, native_dir, monkeypatch):
        monkeypatch.setattr(
            "core.ingest.find_native_memory_dir",
            lambda ctx: native_dir,
        )
        ingestor = NativeMemoryIngestor(tmp_store)
        ingestor.ingest()

        consolidated = tmp_store.list_by_stage("consolidated")
        auth = next(m for m in consolidated if m["title"] == "Auth Rewrite")
        full = tmp_store.get(auth["id"])
        # Description should be included as italic prefix
        assert "Auth middleware rewrite driven by legal compliance" in full["content"]
