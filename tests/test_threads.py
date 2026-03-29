"""
Tests for narrative thread detection, synthesis, and retrieval integration.
"""

import json
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import apsw
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.storage import MemoryStore
from core.threads import ThreadDetector, ThreadNarrator, build_threads
from core.retrieval import RetrievalEngine


@pytest.fixture
def store(tmp_path):
    """Create a temporary MemoryStore."""
    s = MemoryStore(base_dir=str(tmp_path / "memory"))
    yield s
    s.close()


def _create_memory(store, title, content, stage="consolidated", tags=None, created_at=None):
    """Helper to create a memory with optional backdated timestamp."""
    mem_id = store.create(
        path=f"test/{title.lower().replace(' ', '_')}.md",
        content=content,
        metadata={
            "stage": stage,
            "title": title,
            "summary": content[:100],
            "tags": tags or [],
            "importance": 0.6,
        },
    )
    if created_at:
        conn = apsw.Connection(str(store.db_path))
        conn.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?",
            (created_at, mem_id),
        )
        conn.close()
    return mem_id


# ---------------------------------------------------------------------------
# Storage: thread CRUD
# ---------------------------------------------------------------------------


class TestThreadCRUD:

    def test_create_thread(self, store):
        m1 = _create_memory(store, "First", "Content one")
        m2 = _create_memory(store, "Second", "Content two")

        tid = store.create_thread(
            title="My Thread",
            summary="A test thread",
            narrative="First this, then that.",
            member_ids=[m1, m2],
        )
        assert tid is not None

        thread = store.get_thread(tid)
        assert thread["title"] == "My Thread"
        assert thread["narrative"] == "First this, then that."
        assert thread["member_ids"] == [m1, m2]

    def test_create_thread_empty_members_raises(self, store):
        with pytest.raises(ValueError, match="at least one member"):
            store.create_thread("T", "S", "N", member_ids=[])

    def test_create_thread_invalid_member_raises(self, store):
        with pytest.raises(ValueError, match="Memory not found"):
            store.create_thread("T", "S", "N", member_ids=["nonexistent-id"])

    def test_get_thread_not_found_raises(self, store):
        with pytest.raises(ValueError, match="Thread not found"):
            store.get_thread("nonexistent-id")

    def test_get_threads_for_memory(self, store):
        m1 = _create_memory(store, "Shared", "Content")
        m2 = _create_memory(store, "Other", "Content two")

        t1 = store.create_thread("Thread A", "S", "N", member_ids=[m1, m2])
        t2 = store.create_thread("Thread B", "S", "N2", member_ids=[m1])

        threads = store.get_threads_for_memory(m1)
        assert len(threads) == 2
        thread_ids = {t["id"] for t in threads}
        assert t1 in thread_ids
        assert t2 in thread_ids

        # m2 only in one thread
        threads_m2 = store.get_threads_for_memory(m2)
        assert len(threads_m2) == 1

    def test_get_threads_for_unthreaded_memory(self, store):
        m1 = _create_memory(store, "Lonely", "No threads")
        threads = store.get_threads_for_memory(m1)
        assert threads == []

    def test_list_threads(self, store):
        m1 = _create_memory(store, "A", "Content")
        m2 = _create_memory(store, "B", "Content")

        store.create_thread("T1", "S1", "N1", member_ids=[m1])
        store.create_thread("T2", "S2", "N2", member_ids=[m1, m2])

        threads = store.list_threads()
        assert len(threads) == 2
        # Most recent first
        assert threads[0]["title"] == "T2"
        assert len(threads[0]["member_ids"]) == 2

    def test_delete_thread(self, store):
        m1 = _create_memory(store, "Mem", "Content")
        tid = store.create_thread("Doomed", "S", "N", member_ids=[m1])

        store.delete_thread(tid)

        with pytest.raises(ValueError, match="Thread not found"):
            store.get_thread(tid)

        # Memory itself still exists
        mem = store.get(m1)
        assert mem["title"] == "Mem"

    def test_delete_nonexistent_thread_raises(self, store):
        with pytest.raises(ValueError, match="Thread not found"):
            store.delete_thread("nonexistent")

    def test_member_order_preserved(self, store):
        ids = [_create_memory(store, f"M{i}", f"Content {i}") for i in range(5)]
        tid = store.create_thread("Ordered", "S", "N", member_ids=ids)

        thread = store.get_thread(tid)
        assert thread["member_ids"] == ids


# ---------------------------------------------------------------------------
# ThreadDetector
# ---------------------------------------------------------------------------


class TestThreadDetector:

    def _spread_timestamps(self, store, mem_ids, start=None, gap_hours=48):
        """Set created_at timestamps with a gap between each memory."""
        if start is None:
            start = datetime(2026, 1, 1)
        for i, mid in enumerate(mem_ids):
            ts = (start + timedelta(hours=i * gap_hours)).isoformat()
            conn = apsw.Connection(str(store.db_path))
            conn.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                (ts, mid),
            )
            conn.close()

    def test_no_candidates_returns_empty(self, store):
        detector = ThreadDetector(store)
        assert detector.detect_threads() == []

    def test_single_memory_no_thread(self, store):
        _create_memory(store, "Solo", "Content", tags=["python"])
        detector = ThreadDetector(store)
        assert detector.detect_threads() == []

    def test_related_memories_with_temporal_spread(self, store):
        ids = [
            _create_memory(store, f"Async Fix {i}", f"Fixed async issue #{i}",
                          tags=["type:correction", "async"])
            for i in range(3)
        ]
        self._spread_timestamps(store, ids)

        detector = ThreadDetector(store)
        clusters = detector.detect_threads()

        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_unrelated_memories_no_thread(self, store):
        _create_memory(store, "Python garbage collection internals", "CPython reference counting and generational GC", tags=["python"])
        _create_memory(store, "Rust borrow checker rules", "Ownership model and lifetime annotations", tags=["rust"])
        ids = [
            _create_memory(store, "Kubernetes pod scheduling", "Node affinity and tolerations", tags=["k8s_unique"]),
            _create_memory(store, "SQL query optimization", "Index selection and explain plans", tags=["sql_unique"]),
            _create_memory(store, "CSS grid layout patterns", "Responsive design with minmax and auto-fill", tags=["css_unique"]),
        ]
        self._spread_timestamps(store, ids)

        detector = ThreadDetector(store)
        # Force tag-overlap path — this test validates tag-based behavior
        with patch.object(store, "get_embedding", return_value=None):
            clusters = detector.detect_threads()
        # No cluster should form — tags don't overlap
        assert all(len(c) < 2 for c in clusters) or len(clusters) == 0

    def test_memories_too_close_in_time_rejected(self, store):
        """Memories all created in the same minute don't form a thread."""
        now = datetime.now().isoformat()
        ids = [
            _create_memory(store, f"Burst {i}", f"Content {i}",
                          tags=["type:correction", "burst"],
                          created_at=now)
            for i in range(3)
        ]

        detector = ThreadDetector(store)
        clusters = detector.detect_threads()
        assert clusters == []

    def test_exclude_already_threaded(self, store):
        ids = [
            _create_memory(store, f"Threaded {i}", f"Content {i}",
                          tags=["type:correction", "threading"])
            for i in range(3)
        ]
        self._spread_timestamps(store, ids)

        # Create a thread with the first two
        store.create_thread("Existing", "S", "N", member_ids=ids[:2])

        detector = ThreadDetector(store)
        clusters = detector.detect_threads(exclude_threaded=True)
        # Only ids[2] is unthreaded, not enough for a cluster
        assert clusters == []

    def test_include_already_threaded(self, store):
        ids = [
            _create_memory(store, f"Rethreadable {i}", f"Content {i}",
                          tags=["type:correction", "rethreading"])
            for i in range(3)
        ]
        self._spread_timestamps(store, ids)

        store.create_thread("Existing", "S", "N", member_ids=ids[:2])

        detector = ThreadDetector(store)
        clusters = detector.detect_threads(exclude_threaded=False)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_chronological_ordering(self, store):
        """Clusters are returned in chronological order."""
        ids = [
            _create_memory(store, f"Ordered {i}", f"Content {i}",
                          tags=["type:correction", "ordering"])
            for i in range(3)
        ]
        # Set timestamps in reverse order of creation
        start = datetime(2026, 1, 1)
        for i, mid in enumerate(reversed(ids)):
            ts = (start + timedelta(hours=i * 48)).isoformat()
            conn = apsw.Connection(str(store.db_path))
            conn.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                (ts, mid),
            )
            conn.close()

        detector = ThreadDetector(store)
        clusters = detector.detect_threads()
        assert len(clusters) == 1
        # Should be sorted chronologically
        timestamps = [c["created_at"] for c in clusters[0]]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# ThreadNarrator (with mocked LLM)
# ---------------------------------------------------------------------------


MOCK_NARRATIVE_RESPONSE = {
    "title": "Learning async patterns in this codebase",
    "narrative": "Started by reaching for threading. Got corrected: this codebase uses asyncio. Third session: used pure async/await correctly.",
    "current_understanding": "Check the runtime model before choosing concurrency primitives.",
    "arc_type": "correction_chain",
    "confidence": 0.85,
}


class TestThreadNarrator:

    def _make_cluster(self, store, n=3):
        """Create a cluster of related memories."""
        ids = []
        start = datetime(2026, 1, 1)
        for i in range(n):
            ts = (start + timedelta(days=i * 3)).isoformat()
            mid = _create_memory(
                store, f"Async Fix {i}", f"Fixed async issue #{i}",
                tags=["type:correction", "async"],
                created_at=ts,
            )
            ids.append(mid)
        return [store.get(mid) for mid in ids]

    @patch("core.threads.call_llm")
    def test_narrate_produces_thread(self, mock_llm, store):
        mock_llm.return_value = json.dumps(MOCK_NARRATIVE_RESPONSE)
        cluster = self._make_cluster(store)

        narrator = ThreadNarrator(store)
        result = narrator.narrate_cluster(cluster)

        assert result is not None
        assert result["title"] == "Learning async patterns in this codebase"
        assert "async/await" in result["narrative"]
        assert "Current understanding:" in result["narrative"]
        assert result["arc_type"] == "correction_chain"
        assert result["confidence"] == 0.85
        assert len(result["member_ids"]) == 3

    @patch("core.threads.call_llm")
    def test_low_confidence_rejected(self, mock_llm, store):
        """LLM says these don't form a real arc → None returned."""
        mock_llm.return_value = json.dumps({
            **MOCK_NARRATIVE_RESPONSE,
            "confidence": 0.2,
        })
        cluster = self._make_cluster(store)

        narrator = ThreadNarrator(store)
        result = narrator.narrate_cluster(cluster)
        assert result is None

    @patch("core.llm.call_llm", side_effect=Exception("LLM down"))
    def test_llm_failure_returns_none(self, mock_llm, store):
        cluster = self._make_cluster(store)

        narrator = ThreadNarrator(store)
        result = narrator.narrate_cluster(cluster)
        assert result is None

    def test_empty_cluster_returns_none(self, store):
        narrator = ThreadNarrator(store)
        assert narrator.narrate_cluster([]) is None

    def test_strip_frontmatter(self, store):
        narrator = ThreadNarrator(store)
        content = "---\nname: Test\ndescription: A test\n---\n\nActual content here."
        assert narrator._strip_frontmatter(content) == "Actual content here."

    def test_strip_frontmatter_no_frontmatter(self, store):
        narrator = ThreadNarrator(store)
        content = "Just plain content."
        assert narrator._strip_frontmatter(content) == "Just plain content."


# ---------------------------------------------------------------------------
# build_threads end-to-end (mocked LLM)
# ---------------------------------------------------------------------------


class TestBuildThreads:

    @patch("core.threads.call_llm")
    def test_end_to_end(self, mock_llm, store):
        mock_llm.return_value = json.dumps(MOCK_NARRATIVE_RESPONSE)

        start = datetime(2026, 1, 1)
        ids = []
        for i in range(3):
            ts = (start + timedelta(days=i * 3)).isoformat()
            mid = _create_memory(
                store, f"Async {i}", f"Content {i}",
                tags=["type:correction", "async"],
                created_at=ts,
            )
            ids.append(mid)

        created = build_threads(store)
        assert len(created) == 1
        assert created[0]["title"] == "Learning async patterns in this codebase"
        assert created[0]["id"] is not None

        # Thread is persisted
        thread = store.get_thread(created[0]["id"])
        assert len(thread["member_ids"]) == 3

    @patch("core.threads.call_llm")
    def test_no_clusters_no_threads(self, mock_llm, store):
        """No related memories → no threads created."""
        _create_memory(store, "Lonely", "Content", tags=["unique_tag"])
        created = build_threads(store)
        assert created == []
        mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# RetrievalEngine thread injection
# ---------------------------------------------------------------------------


class TestRetrievalThreadInjection:

    def test_thread_narratives_injected(self, store):
        """Tier 2 memories with threads get narrative section in output."""
        m1 = _create_memory(store, "Crystal A", "Content A", stage="crystallized")
        m2 = _create_memory(store, "Crystal B", "Content B", stage="crystallized")

        store.create_thread(
            title="How we learned X",
            summary="A story",
            narrative="First we thought A, then learned B.",
            member_ids=[m1, m2],
        )

        engine = RetrievalEngine(store)
        output = engine.inject_for_session("test-session")

        assert "Narrative Threads" in output
        assert "How we learned X" in output
        assert "First we thought A, then learned B." in output

    def test_no_threads_no_section(self, store):
        """Without threads, no narrative section appears."""
        _create_memory(store, "Crystal", "Content", stage="crystallized")

        engine = RetrievalEngine(store)
        output = engine.inject_for_session("test-session")

        assert "Narrative Threads" not in output

    def test_thread_deduplication(self, store):
        """A thread appears once even if multiple of its members are injected."""
        m1 = _create_memory(store, "C1", "Content 1", stage="crystallized")
        m2 = _create_memory(store, "C2", "Content 2", stage="crystallized")

        store.create_thread(
            title="Shared Thread",
            summary="S",
            narrative="The narrative.",
            member_ids=[m1, m2],
        )

        engine = RetrievalEngine(store)
        output = engine.inject_for_session("test-session")

        # Should appear exactly once
        assert output.count("Shared Thread") == 1


# ---------------------------------------------------------------------------
# last_surfaced_at schema and migration
# ---------------------------------------------------------------------------


class TestLastSurfacedAtMigration:

    def test_new_store_has_last_surfaced_at_column(self, tmp_path):
        """A freshly initialised MemoryStore must include last_surfaced_at in narrative_threads."""
        s = MemoryStore(base_dir=str(tmp_path / "memory"))
        try:
            conn = apsw.Connection(str(s.db_path))
            columns = [row[1] for row in conn.execute("PRAGMA table_info(narrative_threads)")]
            conn.close()
            assert "last_surfaced_at" in columns
        finally:
            s.close()

    def test_migration_is_idempotent(self, tmp_path):
        """Opening a MemoryStore twice on the same path must not raise."""
        base = str(tmp_path / "memory")
        s1 = MemoryStore(base_dir=base)
        s1.close()
        # Second open triggers migration guard; must be silent
        s2 = MemoryStore(base_dir=base)
        s2.close()

    def test_last_surfaced_at_defaults_null(self, store):
        """A newly created thread has last_surfaced_at == None."""
        m1 = _create_memory(store, "SurfaceMe", "Content")
        tid = store.create_thread("T", "S", "N", member_ids=[m1])
        thread = store.get_thread(tid)
        assert thread["last_surfaced_at"] is None


# ---------------------------------------------------------------------------
# Batch thread query
# ---------------------------------------------------------------------------


class TestBatchThreadQuery:

    def test_empty_list_returns_empty(self, store):
        result = store.get_threads_for_memories_batch([])
        assert result == []

    def test_single_memory_matches_per_memory_method(self, store):
        """Batch result for one memory ID must equal get_threads_for_memory for that ID."""
        m1 = _create_memory(store, "Batch M1", "Content one")
        m2 = _create_memory(store, "Batch M2", "Content two")
        store.create_thread("Thread X", "S", "N", member_ids=[m1, m2])

        batch_result = store.get_threads_for_memories_batch([m1])
        per_memory_result = store.get_threads_for_memory(m1)

        assert len(batch_result) == len(per_memory_result)
        assert {t["id"] for t in batch_result} == {t["id"] for t in per_memory_result}

    def test_multiple_memories_deduplicates_shared_thread(self, store):
        """When m1 and m2 share one thread, batch([m1, m2]) returns exactly 1 thread."""
        m1 = _create_memory(store, "Dedup M1", "Content one")
        m2 = _create_memory(store, "Dedup M2", "Content two")
        store.create_thread("Shared", "S", "N", member_ids=[m1, m2])

        result = store.get_threads_for_memories_batch([m1, m2])
        assert len(result) == 1

    def test_returns_full_narrative(self, store):
        """Each returned thread dict must contain the narrative field with full text."""
        m1 = _create_memory(store, "Narrative M1", "Content")
        narrative_text = "The full and complete narrative story."
        store.create_thread("Narrated", "S", narrative_text, member_ids=[m1])

        result = store.get_threads_for_memories_batch([m1])
        assert len(result) == 1
        assert result[0]["narrative"] == narrative_text

    def test_no_threads_returns_empty(self, store):
        """A valid memory ID that belongs to no threads returns []."""
        m1 = _create_memory(store, "Unthreaded", "Content")

        result = store.get_threads_for_memories_batch([m1])
        assert result == []

    def test_cross_thread_union(self, store):
        """m1→threadA, m2→threadB, m3→both; batch([m1,m2,m3]) returns exactly 2 distinct threads."""
        m1 = _create_memory(store, "Cross M1", "Content one")
        m2 = _create_memory(store, "Cross M2", "Content two")
        m3 = _create_memory(store, "Cross M3", "Content three")

        tid_a = store.create_thread("Thread A", "SA", "NA", member_ids=[m1, m3])
        tid_b = store.create_thread("Thread B", "SB", "NB", member_ids=[m2, m3])

        result = store.get_threads_for_memories_batch([m1, m2, m3])
        assert len(result) == 2
        result_ids = {t["id"] for t in result}
        assert tid_a in result_ids
        assert tid_b in result_ids


# ---------------------------------------------------------------------------
# Embedding helpers (deterministic, no real model loaded)
# ---------------------------------------------------------------------------


def _fake_embeddings(n, dim=384, seed=42):
    """Return n unit-normed random embeddings seeded for determinism."""
    rng = np.random.default_rng(seed)
    raw = rng.standard_normal((n, dim))
    norms = np.linalg.norm(raw, axis=1, keepdims=True)
    return raw / np.maximum(norms, 1e-9)


def _cluster_embeddings(n, cluster_size=2):
    """Return embeddings where the first cluster_size items are highly similar (cosine > 0.9).

    Uses noise scale 0.01 so the cluster pair cosine similarity ~0.97, which comfortably
    exceeds the thread clustering threshold of 0.70.
    """
    rng = np.random.default_rng(0)
    base = rng.standard_normal(384)
    base /= np.linalg.norm(base)
    similar = base + rng.standard_normal((cluster_size, 384)) * 0.01
    dissimilar = rng.standard_normal((n - cluster_size, 384))
    all_vecs = np.vstack([similar, dissimilar])
    norms = np.linalg.norm(all_vecs, axis=1, keepdims=True)
    return all_vecs / np.maximum(norms, 1e-9)


def _make_embedding_bytes(values):
    """Convert a list of floats to raw float32 bytes (what store.get_embedding returns)."""
    return struct.pack(f"{len(values)}f", *values)


# ---------------------------------------------------------------------------
# Embedding-based thread clustering (D-09, D-10)
# ---------------------------------------------------------------------------


class TestEmbeddingClustering:
    """Tests for embedding-based clustering in ThreadDetector.detect_threads."""

    def _spread_timestamps(self, store, mem_ids, start=None, gap_hours=48):
        """Set created_at timestamps with a gap between each memory."""
        if start is None:
            start = datetime(2026, 1, 1)
        for i, mid in enumerate(mem_ids):
            ts = (start + timedelta(hours=i * gap_hours)).isoformat()
            conn = apsw.Connection(str(store.db_path))
            conn.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                (ts, mid),
            )
            conn.close()

    def test_semantically_similar_memories_cluster(self, store):
        """Memories with highly similar embeddings form at least one cluster."""
        ids = [
            _create_memory(
                store, f"Async Fix {i}", f"Fixed async issue {i}",
                tags=["type:correction", "async"],
            )
            for i in range(3)
        ]
        self._spread_timestamps(store, ids)

        embeddings = _cluster_embeddings(3, cluster_size=3)
        detector = ThreadDetector(store)

        # Build a per-ID lookup so get_embedding returns the right bytes for each memory.
        id_to_bytes = {
            mid: _make_embedding_bytes(embeddings[i].tolist())
            for i, mid in enumerate(ids)
        }

        with patch.object(store, "get_embedding", side_effect=lambda mid: id_to_bytes.get(mid)):
            clusters = detector.detect_threads()

        assert len(clusters) >= 1

    def test_semantically_dissimilar_memories_do_not_cluster(self, store):
        """Memories with dissimilar embeddings and no tag overlap do not cluster."""
        ids = [
            _create_memory(
                store, f"Unrelated Topic {i}", f"Content about unrelated subject {i}",
                tags=[f"unique_embed_tag_{i}"],
            )
            for i in range(3)
        ]
        self._spread_timestamps(store, ids)

        embeddings = _fake_embeddings(3)
        detector = ThreadDetector(store)

        # Build a per-ID lookup so get_embedding returns the right bytes for each memory.
        id_to_bytes = {
            mid: _make_embedding_bytes(embeddings[i].tolist())
            for i, mid in enumerate(ids)
        }

        with patch.object(store, "get_embedding", side_effect=lambda mid: id_to_bytes.get(mid)):
            clusters = detector.detect_threads()

        # Random unit vectors are far below the 0.70 clustering threshold.
        assert all(len(c) < 2 for c in clusters) or len(clusters) == 0

    def test_clustering_fallback_when_unavailable(self, store):
        """When get_embedding returns None, tag-overlap fallback still forms clusters."""
        ids = [
            _create_memory(
                store, f"Tag Cluster {i}", f"Content {i}",
                tags=["type:correction", "fallback-topic"],
            )
            for i in range(3)
        ]
        self._spread_timestamps(store, ids)

        detector = ThreadDetector(store)

        with patch.object(store, "get_embedding", return_value=None):
            clusters = detector.detect_threads()

        # All three share "fallback-topic" → tag-overlap fallback groups them.
        assert len(clusters) >= 1
        all_member_ids = {m["id"] for c in clusters for m in c}
        assert all(mid in all_member_ids for mid in ids)
