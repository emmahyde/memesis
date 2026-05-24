"""Tests for core.native_memory — bidirectional sync at the crystallized boundary."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.models import Memory
from core.native_memory import (
    NATIVE_TYPES,
    _parse_frontmatter,
    export_memory_to_native,
    ingest_native_memories,
    kind_to_native_type,
    mark_native_archived,
    rebuild_memory_index,
    slugify_title,
)


# ---------------------------------------------------------------------------
# kind → native type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,content,expected",
    [
        ("decision", None, "project"),
        ("goal", None, "project"),
        ("lesson", None, "feedback"),
        ("correction", None, "feedback"),
        ("directive", None, "feedback"),
        ("preference", None, "user"),
        ("fact", "plain prose", "project"),
        ("fact", "see https://example.com for details", "reference"),
        ("hypothesis", None, "project"),
        ("open_question", None, "project"),
        (None, None, "project"),
        ("unknown_kind", None, "project"),
    ],
)
def test_kind_to_native_type(kind, content, expected):
    assert kind_to_native_type(kind, content) == expected


# ---------------------------------------------------------------------------
# slug
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert slugify_title("My Cool Memory", set()) == "my-cool-memory"


def test_slugify_collision_suffix():
    assert slugify_title("Foo Bar", {"foo-bar"}) == "foo-bar-2"
    assert slugify_title("Foo Bar", {"foo-bar", "foo-bar-2"}) == "foo-bar-3"


def test_slugify_empty_title_falls_back():
    assert slugify_title("", set()) == "untitled"
    assert slugify_title("!!!", set()) == "untitled"


# ---------------------------------------------------------------------------
# round-trip
# ---------------------------------------------------------------------------


def _make_memory(memory_store, **overrides) -> Memory:
    defaults = dict(
        stage="crystallized",
        kind="lesson",
        title="Cron python path",
        summary="Cron used wrong python.",
        content="The hourly consolidation cron used /usr/bin/python3 which lacks the anthropic package. Fixed to /usr/local/bin/python3.",
        importance=0.8,
        reinforcement_count=3,
        project="-Users-test-my-project",
    )
    defaults.update(overrides)
    return Memory.create(**defaults)


def test_export_round_trip(memory_store):
    base = Path(memory_store) / "native"
    mem = _make_memory(memory_store)

    path = export_memory_to_native(mem, base)
    assert path is not None
    assert path.exists()
    assert path.parent.name == "feedback"  # lesson → feedback

    text = path.read_text()
    meta, body = _parse_frontmatter(text)
    assert meta["metadata"]["memesis_id"] == mem.id
    assert meta["metadata"]["type"] == "feedback"
    assert "anthropic" in body

    stats = ingest_native_memories(base, project="-Users-test-my-project")
    # No file change → skipped, not refreshed.
    assert stats["refreshed"] == 0
    assert stats["minted"] == 0


def test_ingest_refresh_after_user_edit(memory_store):
    base = Path(memory_store) / "native"
    mem = _make_memory(memory_store)
    path = export_memory_to_native(mem, base)
    assert path is not None

    rc_before = mem.reinforcement_count or 0
    updated_before = mem.updated_at or ""

    # Simulate user edit: bump mtime + change body.
    time.sleep(0.02)
    text = path.read_text()
    meta, body = _parse_frontmatter(text)
    new_body = body + "\n\nUpdated note from user."
    new_text = path.read_text().split("\n---", 1)[0] + "\n---\n\n" + new_body
    path.write_text(new_text)
    # Force mtime forward beyond the recorded updated_at.
    future = time.time() + 5
    import os as _os
    _os.utime(path, (future, future))

    stats = ingest_native_memories(base, project="-Users-test-my-project")
    assert stats["refreshed"] == 1

    refreshed = Memory.get_by_id(mem.id)
    assert (refreshed.reinforcement_count or 0) == rc_before + 1
    assert "Updated note from user" in (refreshed.content or "")
    assert (refreshed.updated_at or "") > updated_before


def test_ingest_mints_new_memory_for_handauthored_file(memory_store):
    base = Path(memory_store) / "native"
    fb = base / "feedback"
    fb.mkdir(parents=True)
    (fb / "manual-note.md").write_text(
        "---\nname: manual-note\ndescription: hand-rolled note\n"
        "metadata:\n  type: feedback\n---\n\n"
        "Body of the manual note.\n"
    )

    stats = ingest_native_memories(base, project="-Users-test-my-project")
    assert stats["minted"] == 1

    candidates = [m for m in Memory.select() if (m.title or "") == "manual-note"]
    assert len(candidates) == 1
    minted = candidates[0]
    assert minted.stage == "crystallized"
    assert "Body of the manual note" in (minted.content or "")

    # The file should now have memesis_id stamped — second ingest is a no-op.
    text = (fb / "manual-note.md").read_text()
    meta, _ = _parse_frontmatter(text)
    assert meta["metadata"]["memesis_id"] == minted.id

    stats2 = ingest_native_memories(base, project="-Users-test-my-project")
    assert stats2["minted"] == 0


def test_demotion_marks_archived_and_leaves_file_on_disk(memory_store):
    base = Path(memory_store) / "native"
    mem = _make_memory(memory_store)
    path = export_memory_to_native(mem, base)
    assert path is not None

    result = mark_native_archived(mem, base)
    assert result == path
    assert path.exists()
    meta, _ = _parse_frontmatter(path.read_text())
    assert meta.get("archived") is True

    # Ingest should skip archived files.
    stats = ingest_native_memories(base, project="-Users-test-my-project")
    assert stats["archived_skipped"] == 1


def test_slug_collision_across_two_memories(memory_store):
    base = Path(memory_store) / "native"
    m1 = _make_memory(memory_store, title="Same Title")
    m2 = _make_memory(memory_store, title="Same Title")

    p1 = export_memory_to_native(m1, base)
    p2 = export_memory_to_native(m2, base)
    assert p1 is not None and p2 is not None
    assert p1.stem == "same-title"
    assert p2.stem == "same-title-2"


def test_archived_memory_is_not_exported(memory_store):
    base = Path(memory_store) / "native"
    mem = _make_memory(memory_store)
    mem.archived_at = "2026-01-01T00:00:00"
    mem.save()
    assert export_memory_to_native(mem, base) is None


def test_non_crystallized_memory_is_not_exported(memory_store):
    base = Path(memory_store) / "native"
    mem = _make_memory(memory_store, stage="consolidated")
    assert export_memory_to_native(mem, base) is None


# ---------------------------------------------------------------------------
# MEMORY.md index
# ---------------------------------------------------------------------------


def test_rebuild_memory_index_groups_by_type_and_skips_archived(memory_store):
    base = Path(memory_store) / "native"
    lesson = _make_memory(memory_store, kind="lesson", title="Lesson A")
    pref = _make_memory(memory_store, kind="preference", title="Pref A")
    archived = _make_memory(memory_store, kind="lesson", title="Old Lesson")

    export_memory_to_native(lesson, base)
    export_memory_to_native(pref, base)
    p = export_memory_to_native(archived, base)
    assert p is not None
    mark_native_archived(archived, base)

    idx = rebuild_memory_index(base)
    assert idx is not None and idx.name == "MEMORY.md"
    text = idx.read_text()
    assert "## Feedback" in text
    assert "## User" in text
    assert "lesson-a" in text
    assert "pref-a" in text
    assert "old-lesson" not in text


def test_native_types_constant_is_canonical():
    assert set(NATIVE_TYPES) == {"user", "feedback", "project", "reference"}
