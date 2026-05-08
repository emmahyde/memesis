"""Tests for native Claude Code memory ingestion."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.ingest import NativeMemoryIngestor, parse_frontmatter, scan_native_memories, find_native_memory_dir
from core.models import Memory


@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def native_dir(tmp_path):
    mem_dir = tmp_path / "native_memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(
        "- [User Role](user_role.md) — User is a senior engineer\n"
        "- [Testing Pref](feedback_testing.md) — Don't mock the database\n"
        "- [Project Goal](project_auth.md) — Auth rewrite for compliance\n",
        encoding="utf-8",
    )
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


class TestParseFrontmatter:
    def test_extracts_metadata(self):
        text = "---\nname: Test\ndescription: A test\ntype: user\n---\n\nBody here."
        meta, body = parse_frontmatter(text)
        assert meta["name"] == "Test"
        assert meta["type"] == "user"
        assert body == "Body here."

    def test_no_frontmatter(self):
        meta, body = parse_frontmatter("Just some text.")
        assert meta == {}
        assert body == "Just some text."

    def test_incomplete_frontmatter(self):
        meta, body = parse_frontmatter("---\nname: Test\nno closing fence")
        assert meta == {}


class TestScanNativeMemories:
    def test_finds_linked_files(self, native_dir):
        names = {m["name"] for m in scan_native_memories(native_dir)}
        assert {"User Role", "Testing Preference", "Auth Rewrite"} <= names

    def test_includes_body(self, native_dir):
        feedback = next(m for m in scan_native_memories(native_dir) if m["name"] == "Testing Preference")
        assert "real database" in feedback["body"]

    def test_includes_type(self, native_dir):
        user = next(m for m in scan_native_memories(native_dir) if m["name"] == "User Role")
        assert user["type"] == "user"

    def test_skips_files_without_frontmatter(self, native_dir):
        (native_dir / "no_frontmatter.md").write_text("Just plain text.\n")
        names = {m["name"] for m in scan_native_memories(native_dir)}
        assert "" not in names

    def test_skips_memesis_subdirectories(self, native_dir):
        consolidated = native_dir / "consolidated"
        consolidated.mkdir()
        (consolidated / "something.md").write_text("---\nname: Should Skip\ntype: user\n---\nContent\n")
        assert "Should Skip" not in {m["name"] for m in scan_native_memories(native_dir)}

    def test_empty_directory(self, tmp_path):
        mem_dir = tmp_path / "empty"
        mem_dir.mkdir()
        (mem_dir / "MEMORY.md").write_text("")
        assert scan_native_memories(mem_dir) == []

    def test_picks_up_unlinked_root_files(self, native_dir):
        (native_dir / "orphan.md").write_text(
            "---\nname: Orphan Memory\ndescription: Found by scan\ntype: reference\n---\n\nOrphan body.\n"
        )
        assert "Orphan Memory" in {m["name"] for m in scan_native_memories(native_dir)}


class TestIngestor:
    def test_ingests_all_native_memories(self, base, native_dir, monkeypatch):
        monkeypatch.setattr("core.ingest.find_native_memory_dir", lambda ctx: native_dir)
        result = NativeMemoryIngestor().ingest()
        assert len(result["ingested"]) == 3
        assert result["skipped"] == 0

    def test_ingested_memories_are_consolidated(self, base, native_dir, monkeypatch):
        monkeypatch.setattr("core.ingest.find_native_memory_dir", lambda ctx: native_dir)
        NativeMemoryIngestor().ingest()
        assert len(list(Memory.by_stage("consolidated"))) == 3

    def test_tags_include_observation_type(self, base, native_dir, monkeypatch):
        monkeypatch.setattr("core.ingest.find_native_memory_dir", lambda ctx: native_dir)
        NativeMemoryIngestor().ingest()
        feedback = next(m for m in Memory.by_stage("consolidated") if m.title == "Testing Preference")
        tags = feedback.tag_list
        assert "type:correction" in tags
        assert "source:native-claude-code" in tags

    def test_importance_mapped_by_type(self, base, native_dir, monkeypatch):
        monkeypatch.setattr("core.ingest.find_native_memory_dir", lambda ctx: native_dir)
        NativeMemoryIngestor().ingest()
        consolidated = list(Memory.by_stage("consolidated"))
        feedback = next(m for m in consolidated if m.title == "Testing Preference")
        user = next(m for m in consolidated if m.title == "User Role")
        assert feedback.importance == 0.75
        assert user.importance == 0.65

    def test_deduplication_on_second_ingest(self, base, native_dir, monkeypatch):
        monkeypatch.setattr("core.ingest.find_native_memory_dir", lambda ctx: native_dir)
        ingestor = NativeMemoryIngestor()
        first = ingestor.ingest()
        second = ingestor.ingest()
        assert len(first["ingested"]) == 3
        assert len(second["ingested"]) == 0
        assert second["skipped"] == 3

    def test_no_native_dir_returns_empty(self, base, monkeypatch):
        monkeypatch.setattr("core.ingest.find_native_memory_dir", lambda ctx: None)
        result = NativeMemoryIngestor().ingest()
        assert result["ingested"] == []
        assert result["source"] is None

    def test_content_includes_description(self, base, native_dir, monkeypatch):
        monkeypatch.setattr("core.ingest.find_native_memory_dir", lambda ctx: native_dir)
        NativeMemoryIngestor().ingest()
        auth = next(m for m in Memory.by_stage("consolidated") if m.title == "Auth Rewrite")
        assert "Auth middleware rewrite driven by legal compliance" in auth.content

    def test_ingested_memory_has_null_card_fields(self, base, native_dir, monkeypatch):
        # D3: non-card write path must leave criterion_weights, rejected_options, affect_valence as NULL
        monkeypatch.setattr("core.ingest.find_native_memory_dir", lambda ctx: native_dir)
        NativeMemoryIngestor().ingest()
        mem = next(m for m in Memory.by_stage("consolidated"))
        assert mem.criterion_weights is None
        assert mem.rejected_options is None
        assert mem.affect_valence is None
