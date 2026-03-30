"""Tests for Phase 1 causal edges — schema, reconsolidation, crystallization, graph, relevance."""

import json
import struct
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from core.database import init_db, close_db
from core.models import Memory, MemoryEdge, NarrativeThread, ThreadMember, ConsolidationLog
from core.graph import compute_edges, expand_neighbors
from core.reconsolidation import reconsolidate, _rank_by_similarity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path / "memory"
    close_db()


def _make_memory(stage="crystallized", title="Test", **kwargs):
    now = datetime.now().isoformat()
    return Memory.create(
        stage=stage,
        title=title,
        summary=kwargs.get("summary", title),
        content=kwargs.get("content", f"Content for {title}"),
        tags=json.dumps(kwargs.get("tags", [])),
        importance=kwargs.get("importance", 0.5),
        reinforcement_count=kwargs.get("reinforcement_count", 0),
        created_at=now,
        updated_at=now,
    )


def _fake_embedding(values):
    """Create a raw float32 bytes embedding from a list of floats."""
    return struct.pack(f"{len(values)}f", *values)


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

class TestSchemaMigration:

    def test_memory_edge_has_metadata_column(self, store):
        edge = MemoryEdge.create(
            source_id="a", target_id="b", edge_type="caused_by",
            weight=0.9, metadata=json.dumps({"evidence": "test"}),
        )
        fresh = MemoryEdge.get_by_id(edge.id)
        assert fresh.metadata is not None
        data = json.loads(fresh.metadata)
        assert data["evidence"] == "test"

    def test_memory_has_echo_count(self, store):
        mem = _make_memory()
        assert mem.echo_count == 0
        Memory.update(echo_count=3).where(Memory.id == mem.id).execute()
        fresh = Memory.get_by_id(mem.id)
        assert fresh.echo_count == 3

    def test_narrative_thread_has_arc_affect(self, store):
        thread = NarrativeThread.create(
            title="Test thread",
            arc_affect=json.dumps({"trajectory": "frustration_to_mastery"}),
        )
        fresh = NarrativeThread.get_by_id(thread.id)
        data = json.loads(fresh.arc_affect)
        assert data["trajectory"] == "frustration_to_mastery"


# ---------------------------------------------------------------------------
# Reconsolidation causal edges
# ---------------------------------------------------------------------------

class TestReconsolidationCausalEdges:

    def test_refined_creates_edges(self, store):
        m1 = _make_memory(title="Prefers snake_case", reinforcement_count=3)
        m2 = _make_memory(title="Uses pytest", reinforcement_count=1)

        llm_response = json.dumps([
            {"memory_id": m1.id, "action": "refined", "evidence": "Also uses camelCase for JS"},
            {"memory_id": m2.id, "action": "confirmed", "evidence": "Yes uses pytest"},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            result = reconsolidate([m1.id, m2.id], "session content", "sess-1")

        assert m1.id in result["refined"]

        # Should have created a refined_from edge from m1 to m2
        edges = list(MemoryEdge.select().where(
            MemoryEdge.source_id == m1.id,
            MemoryEdge.edge_type == "refined_from",
        ))
        assert len(edges) >= 1
        assert edges[0].target_id == m2.id
        meta = json.loads(edges[0].metadata)
        assert "camelCase" in meta["evidence"]

    def test_contradicted_creates_edges(self, store):
        m1 = _make_memory(title="Deploy on Fridays")
        m2 = _make_memory(title="CI/CD pipeline")

        llm_response = json.dumps([
            {"memory_id": m1.id, "action": "contradicted", "evidence": "Moved to Tuesday"},
            {"memory_id": m2.id, "action": "confirmed", "evidence": "yes"},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            reconsolidate([m1.id, m2.id], "deploy changed", "sess-2")

        edges = list(MemoryEdge.select().where(
            MemoryEdge.source_id == m1.id,
            MemoryEdge.edge_type == "caused_by",
        ))
        assert len(edges) >= 1

    def test_no_edges_when_flag_disabled(self, store, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {
            "reconsolidation": True,
            "causal_edges": False,
        })

        m1 = _make_memory(title="A")
        m2 = _make_memory(title="B")

        llm_response = json.dumps([
            {"memory_id": m1.id, "action": "refined", "evidence": "updated"},
            {"memory_id": m2.id, "action": "confirmed", "evidence": "ok"},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            reconsolidate([m1.id, m2.id], "content", "sess-3")

        assert MemoryEdge.select().count() == 0

    def test_no_duplicate_edges(self, store):
        m1 = _make_memory(title="A")
        m2 = _make_memory(title="B")

        llm_response = json.dumps([
            {"memory_id": m1.id, "action": "refined", "evidence": "updated"},
            {"memory_id": m2.id, "action": "confirmed", "evidence": "ok"},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            reconsolidate([m1.id, m2.id], "content", "sess-4")
            reconsolidate([m1.id, m2.id], "content again", "sess-5")

        edges = list(MemoryEdge.select().where(
            MemoryEdge.source_id == m1.id,
            MemoryEdge.edge_type == "refined_from",
        ))
        assert len(edges) == 1  # not duplicated

    def test_unmentioned_creates_no_edges(self, store):
        m1 = _make_memory(title="A")
        m2 = _make_memory(title="B")

        llm_response = json.dumps([
            {"memory_id": m1.id, "action": "unmentioned", "evidence": ""},
            {"memory_id": m2.id, "action": "unmentioned", "evidence": ""},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            reconsolidate([m1.id, m2.id], "unrelated", "sess-6")

        assert MemoryEdge.select().count() == 0


# ---------------------------------------------------------------------------
# sqlite-vec similarity ranking
# ---------------------------------------------------------------------------

class TestRankBySimilarity:

    def test_fallback_when_no_vec_store(self, store):
        """Without vec_store, returns candidates with default weight."""
        with patch("core.database.get_vec_store", return_value=None):
            result = _rank_by_similarity("src", ["a", "b", "c"], limit=2)
        assert len(result) == 2
        assert all(w == 0.5 for _, w in result)

    def test_ranks_by_cosine_similarity(self, store):
        """With embeddings, ranks candidates by cosine similarity."""
        mock_vs = MagicMock()
        mock_vs.available = True

        # Source embedding: [1, 0, 0]
        # Candidate A: [0.9, 0.1, 0] — very similar
        # Candidate B: [0, 1, 0] — orthogonal
        mock_vs.get_embedding.side_effect = lambda mid: {
            "src": _fake_embedding([1.0, 0.0, 0.0]),
            "a": _fake_embedding([0.9, 0.1, 0.0]),
            "b": _fake_embedding([0.0, 1.0, 0.0]),
        }.get(mid)

        with patch("core.database.get_vec_store", return_value=mock_vs):
            result = _rank_by_similarity("src", ["a", "b"], limit=2)

        assert result[0][0] == "a"  # more similar
        assert result[0][1] > result[1][1]

    def test_missing_embedding_gets_default(self, store):
        mock_vs = MagicMock()
        mock_vs.available = True
        mock_vs.get_embedding.side_effect = lambda mid: {
            "src": _fake_embedding([1.0, 0.0]),
        }.get(mid)  # candidate has no embedding

        with patch("core.database.get_vec_store", return_value=mock_vs):
            result = _rank_by_similarity("src", ["missing"], limit=1)

        assert len(result) == 1
        assert result[0][1] == 0.5  # fallback weight


# ---------------------------------------------------------------------------
# Crystallization subsumed_into edges
# ---------------------------------------------------------------------------

class TestCrystallizationEdges:

    def test_crystallize_creates_subsumed_edges(self, store):
        from core.crystallizer import Crystallizer
        from core.lifecycle import LifecycleManager

        # Create candidates with enough reinforcement
        m1 = _make_memory(stage="consolidated", title="Obs A", reinforcement_count=3,
                          tags=["testing"])
        m2 = _make_memory(stage="consolidated", title="Obs B", reinforcement_count=3,
                          tags=["testing"])

        lifecycle = LifecycleManager()
        crystallizer = Crystallizer(lifecycle)

        mock_llm_result = json.dumps({
            "title": "Testing principle",
            "insight": "Always test thoroughly",
            "observation_type": "workflow_pattern",
            "tags": ["testing"],
            "source_pattern": "Both observations about testing",
        })

        with patch("core.crystallizer.call_llm", return_value=mock_llm_result):
            results = crystallizer._crystallize_group([m1, m2])

        assert results is not None
        crystal_id = results["crystallized_id"]

        # Check subsumed_into edges
        edges = list(MemoryEdge.select().where(
            MemoryEdge.target_id == crystal_id,
            MemoryEdge.edge_type == "subsumed_into",
        ))
        assert len(edges) == 2
        source_ids = {e.source_id for e in edges}
        assert m1.id in source_ids
        assert m2.id in source_ids

        for edge in edges:
            meta = json.loads(edge.metadata)
            assert "crystal_title" in meta
            assert meta["crystal_title"] == "Testing principle"

    def test_no_edges_when_flag_disabled(self, store, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"causal_edges": False})

        from core.crystallizer import Crystallizer
        from core.lifecycle import LifecycleManager

        m1 = _make_memory(stage="consolidated", title="Obs A", reinforcement_count=3)
        lifecycle = LifecycleManager()
        crystallizer = Crystallizer(lifecycle)

        mock_llm_result = json.dumps({
            "title": "Principle",
            "insight": "An insight",
            "observation_type": "workflow_pattern",
            "tags": [],
            "source_pattern": "pattern",
        })

        with patch("core.crystallizer.call_llm", return_value=mock_llm_result):
            crystallizer._crystallize_group([m1])

        edges = list(MemoryEdge.select().where(MemoryEdge.edge_type == "subsumed_into"))
        assert len(edges) == 0


# ---------------------------------------------------------------------------
# compute_edges preserves incremental edges
# ---------------------------------------------------------------------------

class TestComputeEdgesPreservation:

    def test_preserves_causal_edges(self, store):
        m1 = _make_memory(title="A", tags=["shared"])
        m2 = _make_memory(title="B", tags=["shared"])

        # Create an incremental causal edge
        MemoryEdge.create(
            source_id=m1.id, target_id=m2.id,
            edge_type="caused_by", weight=0.8,
            metadata=json.dumps({"evidence": "test"}),
        )

        # Recompute structural edges
        compute_edges()

        # Causal edge should still exist
        causal = list(MemoryEdge.select().where(MemoryEdge.edge_type == "caused_by"))
        assert len(causal) == 1
        assert causal[0].source_id == m1.id

        # Tag co-occurrence edges should also exist
        tag_edges = list(MemoryEdge.select().where(MemoryEdge.edge_type == "tag_cooccurrence"))
        assert len(tag_edges) >= 2

    def test_preserves_subsumed_edges(self, store):
        m1 = _make_memory(title="Source")
        m2 = _make_memory(title="Crystal")

        MemoryEdge.create(
            source_id=m1.id, target_id=m2.id,
            edge_type="subsumed_into", weight=1.0,
        )

        compute_edges()

        subsumed = list(MemoryEdge.select().where(MemoryEdge.edge_type == "subsumed_into"))
        assert len(subsumed) == 1

    def test_clears_thread_and_tag_edges(self, store):
        m1 = _make_memory(title="A", tags=["x"])
        m2 = _make_memory(title="B", tags=["x"])

        # First compute
        compute_edges()
        count1 = MemoryEdge.select().where(
            MemoryEdge.edge_type == "tag_cooccurrence"
        ).count()
        assert count1 > 0

        # Second compute — should not accumulate
        compute_edges()
        count2 = MemoryEdge.select().where(
            MemoryEdge.edge_type == "tag_cooccurrence"
        ).count()
        assert count2 == count1


# ---------------------------------------------------------------------------
# expand_neighbors with priority ordering
# ---------------------------------------------------------------------------

class TestExpandNeighborsPriority:

    def test_causal_edges_prioritised(self, store):
        seed = _make_memory(title="Seed")
        causal_neighbor = _make_memory(title="Causal")
        tag_neighbor = _make_memory(title="Tag")

        MemoryEdge.create(
            source_id=seed.id, target_id=causal_neighbor.id,
            edge_type="caused_by", weight=0.9,
        )
        MemoryEdge.create(
            source_id=seed.id, target_id=tag_neighbor.id,
            edge_type="tag_cooccurrence", weight=1.0,
        )

        neighbors = expand_neighbors([seed.id], max_expansion=1)
        assert len(neighbors) == 1
        assert neighbors[0] == causal_neighbor.id  # causal wins

    def test_mixed_edge_types_ordered(self, store):
        seed = _make_memory(title="Seed")
        n_thread = _make_memory(title="Thread neighbor")
        n_refined = _make_memory(title="Refined from")
        n_tag = _make_memory(title="Tag co-occur")

        MemoryEdge.create(source_id=seed.id, target_id=n_tag.id,
                          edge_type="tag_cooccurrence")
        MemoryEdge.create(source_id=seed.id, target_id=n_thread.id,
                          edge_type="thread_neighbor")
        MemoryEdge.create(source_id=seed.id, target_id=n_refined.id,
                          edge_type="refined_from", weight=0.8)

        neighbors = expand_neighbors([seed.id], max_expansion=10)
        assert len(neighbors) == 3
        # refined_from (prio 1) should come before thread_neighbor (prio 4)
        assert neighbors[0] == n_refined.id
        assert neighbors[1] == n_thread.id
        assert neighbors[2] == n_tag.id

    def test_vec_store_tiebreaker(self, store):
        """Within same priority tier, sqlite-vec similarity breaks ties."""
        seed = _make_memory(title="Seed")
        n1 = _make_memory(title="Close")
        n2 = _make_memory(title="Far")

        MemoryEdge.create(source_id=seed.id, target_id=n1.id,
                          edge_type="tag_cooccurrence")
        MemoryEdge.create(source_id=seed.id, target_id=n2.id,
                          edge_type="tag_cooccurrence")

        mock_vs = MagicMock()
        mock_vs.available = True
        # n1 is more similar to seed than n2
        mock_vs.get_embedding.side_effect = lambda mid: {
            seed.id: _fake_embedding([1.0, 0.0]),
            n1.id: _fake_embedding([0.95, 0.05]),
            n2.id: _fake_embedding([0.1, 0.9]),
        }.get(mid)

        neighbors = expand_neighbors([seed.id], max_expansion=2, vec_store=mock_vs)
        assert len(neighbors) == 2
        assert neighbors[0] == n1.id  # closer in embedding space


# ---------------------------------------------------------------------------
# Relevance integration factor with causal edges
# ---------------------------------------------------------------------------

class TestRelevanceCausalIntegration:

    def test_causal_edge_prevents_isolation_penalty(self, store):
        from core.relevance import RelevanceEngine

        # Memory with causal edge but no thread or tag overlap
        m = _make_memory(
            title="Isolated but causal",
            importance=0.5,
            reinforcement_count=0,
        )
        MemoryEdge.create(
            source_id=m.id, target_id="other-id",
            edge_type="caused_by", weight=0.8,
        )

        # Memory with no connections at all
        m_isolated = _make_memory(
            title="Truly isolated",
            importance=0.5,
            reinforcement_count=0,
        )

        engine = RelevanceEngine()
        score_causal = engine.compute_relevance(m)
        score_isolated = engine.compute_relevance(m_isolated)

        # Causal connection should prevent the isolation penalty
        assert score_causal > score_isolated

    def test_subsumed_into_counts_as_causal(self, store):
        from core.relevance import RelevanceEngine

        m = _make_memory(importance=0.5, reinforcement_count=0)
        MemoryEdge.create(
            source_id=m.id, target_id="crystal-id",
            edge_type="subsumed_into", weight=1.0,
        )

        engine = RelevanceEngine()
        assert engine._has_causal_edges(m) is True

    def test_no_causal_edges_returns_false(self, store):
        from core.relevance import RelevanceEngine

        m = _make_memory()
        assert RelevanceEngine._has_causal_edges(m) is False

    def test_flag_disabled_ignores_causal(self, store, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {
            "integration_factor": True,
            "causal_edges": False,
            "saturation_decay": True,
        })

        from core.relevance import RelevanceEngine

        m = _make_memory(importance=0.5, reinforcement_count=0)
        MemoryEdge.create(
            source_id=m.id, target_id="other",
            edge_type="caused_by", weight=0.8,
        )

        engine = RelevanceEngine()
        # With causal_edges disabled, should still get isolation penalty
        score = engine.compute_relevance(m)

        # Compare against a memory that IS connected via tags
        m2 = _make_memory(title="Connected", importance=0.5,
                          reinforcement_count=0, tags=["shared"])
        _make_memory(title="Other", importance=0.5, tags=["shared"])

        score_connected = engine.compute_relevance(m2)
        assert score_connected > score


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:

    def test_causal_edges_flag_exists(self, store):
        from core.flags import get_flag
        assert get_flag("causal_edges") is True


# ---------------------------------------------------------------------------
# Relevance integration factor with contradiction edges
# ---------------------------------------------------------------------------

class TestRelevanceContradictionIntegration:

    def test_contradiction_flags_exist(self, store):
        from core.flags import get_flag
        assert get_flag("contradiction_tensors") is True
        assert get_flag("affect_signatures") is True
        assert get_flag("adversarial_surfacing") is True

    def test_contradiction_edge_prevents_isolation_penalty(self, store):
        from core.relevance import RelevanceEngine

        # Memory with contradiction edge but no thread, tag overlap, or causal edge
        m = _make_memory(
            title="Has contradiction",
            importance=0.5,
            reinforcement_count=0,
        )
        MemoryEdge.create(
            source_id=m.id, target_id="other-id",
            edge_type="contradicts", weight=0.9,
        )

        # Memory with no connections at all
        m_isolated = _make_memory(
            title="Truly isolated",
            importance=0.5,
            reinforcement_count=0,
        )

        engine = RelevanceEngine()
        score_contradiction = engine.compute_relevance(m)
        score_isolated = engine.compute_relevance(m_isolated)

        # Contradiction connection should prevent the isolation penalty
        assert score_contradiction > score_isolated

    def test_contradiction_edge_as_target_also_counts(self, store):
        """Memory that is the target of a contradicts edge is also considered connected."""
        from core.relevance import RelevanceEngine

        m = _make_memory(title="Contradicted target", importance=0.5, reinforcement_count=0)
        MemoryEdge.create(
            source_id="other-id", target_id=m.id,
            edge_type="contradicts", weight=0.9,
        )

        engine = RelevanceEngine()
        assert engine._has_contradiction_edges(m) is True

    def test_no_contradiction_edges_returns_false(self, store):
        from core.relevance import RelevanceEngine

        m = _make_memory()
        assert RelevanceEngine._has_contradiction_edges(m) is False

    def test_flag_disabled_skips_contradiction_check(self, store, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {
            "integration_factor": True,
            "causal_edges": False,
            "contradiction_tensors": False,
            "saturation_decay": True,
        })

        from core.relevance import RelevanceEngine

        m = _make_memory(importance=0.5, reinforcement_count=0)
        MemoryEdge.create(
            source_id=m.id, target_id="other",
            edge_type="contradicts", weight=0.9,
        )

        engine = RelevanceEngine()
        # Both causal_edges and contradiction_tensors are disabled —
        # the contradiction edge must not rescue m from isolation penalty.
        score = engine.compute_relevance(m)

        # A memory with a tag-shared neighbor (truly connected) should score higher
        m2 = _make_memory(title="Tag-connected", importance=0.5,
                          reinforcement_count=0, tags=["shared"])
        _make_memory(title="Other tag holder", importance=0.5, tags=["shared"])

        score_connected = engine.compute_relevance(m2)
        assert score_connected > score
