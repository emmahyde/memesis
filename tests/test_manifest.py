"""Tests for ManifestGenerator."""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, get_base_dir
from core.manifest import ManifestGenerator
from core.models import Memory


@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def generator(base):
    return ManifestGenerator()


def add_memory(stage, title, summary, importance=0.5, content=None):
    now = datetime.now().isoformat()
    return Memory.create(
        stage=stage, title=title, summary=summary,
        content=content or f"Content for {title}",
        tags="[]", importance=importance,
        created_at=now, updated_at=now,
    ).id


class TestManifestEmpty:
    def test_generate_empty_store(self, generator):
        result = generator.generate()
        assert result.startswith("# Memory")
        assert "## Instinctive" not in result

    def test_token_budget_empty_store(self, generator):
        count, pct = generator.estimate_token_budget()
        assert count == 0
        assert pct == 0.0

    def test_write_manifest_creates_file(self, generator, base):
        generator.write_manifest()
        assert (get_base_dir() / "MEMORY.md").exists()

    def test_write_manifest_is_atomic(self, generator, base, monkeypatch):
        manifest_path = get_base_dir() / "MEMORY.md"
        generator.write_manifest()
        original_content = manifest_path.read_text()
        def failing_move(src, dst):
            raise OSError("simulated disk full")
        monkeypatch.setattr(shutil, "move", failing_move)
        with pytest.raises(OSError):
            generator.write_manifest()
        assert manifest_path.read_text() == original_content


class TestManifestFormat:
    def test_instinctive_section_present(self, base, generator):
        add_memory("instinctive", "Ruby Style", "Idiomatic Ruby tips")
        result = generator.generate()
        assert "## Instinctive (always loaded)" in result
        assert "Ruby Style" in result

    def test_crystallized_section_with_importance(self, base, generator):
        add_memory("crystallized", "Payment Pipeline", "Check lock ordering", importance=0.78)
        result = generator.generate()
        assert "## Crystallized (context-matched)" in result
        assert "[importance: 0.78]" in result

    def test_consolidated_section_present(self, base, generator):
        add_memory("consolidated", "Memory Architecture", "Chose lifecycle approach")
        result = generator.generate()
        assert "## Consolidated (available via search)" in result
        assert "Memory Architecture" in result

    def test_ephemeral_excluded(self, base, generator):
        add_memory("ephemeral", "Scratch Note", "Ephemeral content")
        result = generator.generate()
        assert "## Ephemeral" not in result
        assert "Scratch Note" not in result

    def test_metadata_comment_includes_total_and_budget(self, base, generator):
        add_memory("instinctive", "Ruby", "Ruby style")
        add_memory("crystallized", "Crystal", "Crystal hint", importance=0.6)
        result = generator.generate()
        assert "Total: 2" in result
        assert "Token budget:" in result


class TestManifestOrdering:
    def test_crystallized_sorted_by_importance_descending(self, base, generator):
        add_memory("crystallized", "Low", "Low importance", importance=0.3)
        add_memory("crystallized", "High", "High importance", importance=0.9)
        add_memory("crystallized", "Mid", "Mid importance", importance=0.6)
        result = generator.generate()
        assert result.index("High") < result.index("Mid") < result.index("Low")

    def test_stage_section_order(self, base, generator):
        add_memory("instinctive", "Inst", "instinctive memory")
        add_memory("crystallized", "Crys", "crystallized memory", importance=0.7)
        add_memory("consolidated", "Cons", "consolidated memory")
        result = generator.generate()
        assert result.index("## Instinctive") < result.index("## Crystallized") < result.index("## Consolidated")


class TestTokenBudget:
    def test_budget_percentage_is_fraction_of_200k(self, base, generator):
        count, pct = generator.estimate_token_budget()
        if count > 0:
            assert abs(pct - count / 200_000) < 1e-9
        else:
            assert pct == 0.0


class TestManifestIdempotency:
    def test_generate_is_idempotent(self, base, generator):
        add_memory("instinctive", "Ruby", "Ruby style")
        add_memory("crystallized", "Crystal", "Crystal hint", importance=0.7)
        r1 = generator.generate()
        r2 = generator.generate()
        def strip_ts(text):
            return "\n".join(l for l in text.splitlines() if "Last updated:" not in l)
        assert strip_ts(r1) == strip_ts(r2)


class TestWriteManifest:
    def test_writes_to_correct_location(self, base, generator):
        generator.write_manifest()
        assert (get_base_dir() / "MEMORY.md").exists()
