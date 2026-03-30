"""Tests for graph expansion — edge computation and 1-hop neighbor discovery."""

import json

import pytest

from core.database import init_db, close_db
from core.models import Memory, MemoryEdge, NarrativeThread, ThreadMember
from core.graph import compute_edges, expand_neighbors


@pytest.fixture
def store(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path / "memory"
    close_db()


class TestComputeEdges:

    def test_thread_creates_edges(self, store):
        m1 = Memory.create(stage="crystallized", title="A", tags="[]")
        m2 = Memory.create(stage="crystallized", title="B", tags="[]")
        thread = NarrativeThread.create(title="Test thread")
        ThreadMember.create(thread_id=thread.id, memory_id=m1.id, position=0)
        ThreadMember.create(thread_id=thread.id, memory_id=m2.id, position=1)

        count = compute_edges()
        assert count == 2  # bidirectional
        assert MemoryEdge.select().count() == 2

    def test_tag_cooccurrence_creates_edges(self, store):
        Memory.create(stage="crystallized", title="A", tags=json.dumps(["python", "testing"]))
        Memory.create(stage="crystallized", title="B", tags=json.dumps(["python", "deployment"]))

        count = compute_edges()
        assert count >= 2  # at least the python tag co-occurrence

    def test_meta_tags_excluded(self, store):
        Memory.create(stage="crystallized", title="A", tags=json.dumps(["type:correction", "valence:friction"]))
        Memory.create(stage="crystallized", title="B", tags=json.dumps(["type:correction", "valence:neutral"]))

        count = compute_edges()
        assert count == 0  # meta tags don't create edges

    def test_clears_existing_edges(self, store):
        m1 = Memory.create(stage="crystallized", title="A", tags=json.dumps(["shared"]))
        m2 = Memory.create(stage="crystallized", title="B", tags=json.dumps(["shared"]))
        compute_edges()
        first_count = MemoryEdge.select().count()

        # Recompute — should clear and rebuild
        compute_edges()
        assert MemoryEdge.select().count() == first_count

    def test_no_self_edges(self, store):
        Memory.create(stage="crystallized", title="Solo", tags=json.dumps(["unique"]))
        compute_edges()
        assert MemoryEdge.select().count() == 0


class TestExpandNeighbors:

    def test_returns_neighbors(self, store):
        m1 = Memory.create(stage="crystallized", title="Seed", tags="[]")
        m2 = Memory.create(stage="crystallized", title="Neighbor", tags="[]")
        MemoryEdge.create(source_id=m1.id, target_id=m2.id, edge_type="thread_neighbor")

        neighbors = expand_neighbors([m1.id])
        assert m2.id in neighbors

    def test_excludes_seeds(self, store):
        m1 = Memory.create(stage="crystallized", title="A", tags="[]")
        m2 = Memory.create(stage="crystallized", title="B", tags="[]")
        MemoryEdge.create(source_id=m1.id, target_id=m2.id, edge_type="thread_neighbor")
        MemoryEdge.create(source_id=m2.id, target_id=m1.id, edge_type="thread_neighbor")

        neighbors = expand_neighbors([m1.id, m2.id])
        assert len(neighbors) == 0

    def test_respects_max_expansion(self, store):
        seed = Memory.create(stage="crystallized", title="Seed", tags="[]")
        for i in range(20):
            n = Memory.create(stage="crystallized", title=f"N{i}", tags="[]")
            MemoryEdge.create(source_id=seed.id, target_id=n.id, edge_type="tag_cooccurrence")

        neighbors = expand_neighbors([seed.id], max_expansion=5)
        assert len(neighbors) <= 5

    def test_empty_seeds_returns_empty(self, store):
        assert expand_neighbors([]) == []

    def test_flag_disabled_returns_empty(self, store, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"graph_expansion": False})
        m1 = Memory.create(stage="crystallized", title="A", tags="[]")
        m2 = Memory.create(stage="crystallized", title="B", tags="[]")
        MemoryEdge.create(source_id=m1.id, target_id=m2.id, edge_type="thread_neighbor")

        assert expand_neighbors([m1.id]) == []
