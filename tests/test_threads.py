"""
Tests for narrative thread detection, synthesis, and retrieval integration.
"""

import json
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, get_base_dir, get_db_path, get_vec_store
from core.models import Memory, ConsolidationLog, RetrievalLog, NarrativeThread, ThreadMember, db
from core.threads import ThreadDetector, ThreadNarrator, build_threads
from core.retrieval import RetrievalEngine


@pytest.fixture
def base(tmp_path):
    """Initialize DB in a throwaway temp directory."""
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _create_memory(title, content, stage="consolidated", tags=None, created_at=None):
    """Helper to create a memory with optional backdated timestamp."""
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage=stage,
        title=title,
        summary=content[:100],
        content=content,
        tags=json.dumps(tags or []),
        importance=0.6,
        created_at=created_at or now,
        updated_at=now,
    )
    if created_at:
        Memory.update(created_at=created_at).where(Memory.id == mem.id).execute()
    return mem.id


def _create_thread(title, summary, narrative, member_ids):
    """Helper to create a narrative thread with members."""
    if not member_ids:
        raise ValueError("Thread must have at least one member")
    # Validate all member IDs exist
    for mid in member_ids:
        try:
            Memory.get_by_id(mid)
        except Memory.DoesNotExist:
            raise ValueError(f"Memory not found: {mid}")

    now = datetime.now().isoformat()
    thread = NarrativeThread.create(
        title=title,
        summary=summary,
        narrative=narrative,
        created_at=now,
        updated_at=now,
    )
    for pos, mid in enumerate(member_ids):
        ThreadMember.create(thread_id=thread.id, memory_id=mid, position=pos)
    return thread.id


def _get_thread(thread_id):
    """Helper to get a thread as a dict."""
    try:
        thread = NarrativeThread.get_by_id(thread_id)
    except NarrativeThread.DoesNotExist:
        raise ValueError(f"Thread not found: {thread_id}")
    return {
        "id": thread.id,
        "title": thread.title,
        "summary": thread.summary,
        "narrative": thread.narrative,
        "member_ids": thread.member_ids,
        "last_surfaced_at": thread.last_surfaced_at,
    }


def _get_threads_for_memory(memory_id):
    """Helper to get threads that contain a given memory."""
    threads = (
        NarrativeThread.select()
        .join(ThreadMember, on=(NarrativeThread.id == ThreadMember.thread_id))
        .where(ThreadMember.memory_id == memory_id)
    )
    result = []
    for t in threads:
        result.append({
            "id": t.id,
            "title": t.title,
            "narrative": t.narrative,
            "member_ids": t.member_ids,
        })
    return result


def _get_threads_for_memories_batch(memory_ids):
    """Helper to get threads for multiple memories, deduplicated."""
    if not memory_ids:
        return []

    threads = (
        NarrativeThread.select()
        .join(ThreadMember, on=(NarrativeThread.id == ThreadMember.thread_id))
        .where(ThreadMember.memory_id.in_(memory_ids))
        .distinct()
        .order_by(NarrativeThread.updated_at.desc())
    )
    result = []
    seen = set()
    for t in threads:
        if t.id not in seen:
            seen.add(t.id)
            result.append({
                "id": t.id,
                "title": t.title,
                "narrative": t.narrative,
                "member_ids": t.member_ids,
            })
    return result


def _delete_thread(thread_id):
    """Helper to delete a thread."""
    try:
        thread = NarrativeThread.get_by_id(thread_id)
    except NarrativeThread.DoesNotExist:
        raise ValueError(f"Thread not found: {thread_id}")
    ThreadMember.delete().where(ThreadMember.thread_id == thread_id).execute()
    thread.delete_instance()


def _list_threads():
    """Helper to list all threads."""
    threads = NarrativeThread.select().order_by(NarrativeThread.updated_at.desc())
    result = []
    for t in threads:
        result.append({
            "id": t.id,
            "title": t.title,
            "member_ids": t.member_ids,
        })
    return result


# ---------------------------------------------------------------------------
# Storage: thread CRUD
# ---------------------------------------------------------------------------


class TestThreadCRUD:

    def test_create_thread(self, base):
        m1 = _create_memory("First", "Content one")
        m2 = _create_memory("Second", "Content two")

        tid = _create_thread("My Thread", "A test thread", "First this, then that.", [m1, m2])
        assert tid is not None

        thread = _get_thread(tid)
        assert thread["title"] == "My Thread"
        assert thread["narrative"] == "First this, then that."
        assert thread["member_ids"] == [m1, m2]

    def test_create_thread_empty_members_raises(self, base):
        with pytest.raises(ValueError, match="at least one member"):
            _create_thread("T", "S", "N", member_ids=[])

    def test_create_thread_invalid_member_raises(self, base):
        with pytest.raises(ValueError, match="Memory not found"):
            _create_thread("T", "S", "N", member_ids=["nonexistent-id"])

    def test_get_thread_not_found_raises(self, base):
        with pytest.raises(ValueError, match="Thread not found"):
            _get_thread("nonexistent-id")

    def test_get_threads_for_memory(self, base):
        m1 = _create_memory("Shared", "Content")
        m2 = _create_memory("Other", "Content two")

        t1 = _create_thread("Thread A", "S", "N", member_ids=[m1, m2])
        t2 = _create_thread("Thread B", "S", "N2", member_ids=[m1])

        threads = _get_threads_for_memory(m1)
        assert len(threads) == 2
        thread_ids = {t["id"] for t in threads}
        assert t1 in thread_ids
        assert t2 in thread_ids

        threads_m2 = _get_threads_for_memory(m2)
        assert len(threads_m2) == 1

    def test_get_threads_for_unthreaded_memory(self, base):
        m1 = _create_memory("Lonely", "No threads")
        threads = _get_threads_for_memory(m1)
        assert threads == []

    def test_list_threads(self, base):
        m1 = _create_memory("A", "Content")
        m2 = _create_memory("B", "Content")

        _create_thread("T1", "S1", "N1", member_ids=[m1])
        _create_thread("T2", "S2", "N2", member_ids=[m1, m2])

        threads = _list_threads()
        assert len(threads) == 2
        # Most recent first
        assert threads[0]["title"] == "T2"
        assert len(threads[0]["member_ids"]) == 2

    def test_delete_thread(self, base):
        m1 = _create_memory("Mem", "Content")
        tid = _create_thread("Doomed", "S", "N", member_ids=[m1])

        _delete_thread(tid)

        with pytest.raises(ValueError, match="Thread not found"):
            _get_thread(tid)

        # Memory itself still exists
        mem = Memory.get_by_id(m1)
        assert mem.title == "Mem"

    def test_delete_nonexistent_thread_raises(self, base):
        with pytest.raises(ValueError, match="Thread not found"):
            _delete_thread("nonexistent")

    def test_member_order_preserved(self, base):
        ids = [_create_memory(f"M{i}", f"Content {i}") for i in range(5)]
        tid = _create_thread("Ordered", "S", "N", member_ids=ids)

        thread = _get_thread(tid)
        assert thread["member_ids"] == ids


# ---------------------------------------------------------------------------
# ThreadDetector
# ---------------------------------------------------------------------------


class TestThreadDetector:

    def _spread_timestamps(self, mem_ids, start=None, gap_hours=48):
        """Set created_at timestamps with a gap between each memory."""
        if start is None:
            start = datetime(2026, 1, 1)
        for i, mid in enumerate(mem_ids):
            ts = (start + timedelta(hours=i * gap_hours)).isoformat()
            Memory.update(created_at=ts).where(Memory.id == mid).execute()

    def test_no_candidates_returns_empty(self, base):
        detector = ThreadDetector()
        assert detector.detect_threads() == []

    def test_single_memory_no_thread(self, base):
        _create_memory("Solo", "Content", tags=["python"])
        detector = ThreadDetector()
        assert detector.detect_threads() == []

    def test_related_memories_with_temporal_spread(self, base):
        ids = [
            _create_memory(f"Async Fix {i}", f"Fixed async issue #{i}",
                          tags=["type:correction", "async"])
            for i in range(3)
        ]
        self._spread_timestamps(ids)

        detector = ThreadDetector()
        clusters = detector.detect_threads()

        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_unrelated_memories_no_thread(self, base):
        _create_memory("Python garbage collection internals", "CPython reference counting and generational GC", tags=["python"])
        _create_memory("Rust borrow checker rules", "Ownership model and lifetime annotations", tags=["rust"])
        ids = [
            _create_memory("Kubernetes pod scheduling", "Node affinity and tolerations", tags=["k8s_unique"]),
            _create_memory("SQL query optimization", "Index selection and explain plans", tags=["sql_unique"]),
            _create_memory("CSS grid layout patterns", "Responsive design with minmax and auto-fill", tags=["css_unique"]),
        ]
        self._spread_timestamps(ids)

        detector = ThreadDetector()
        vec = get_vec_store()
        with patch.object(vec, "get_embedding", return_value=None):
            clusters = detector.detect_threads()
        assert all(len(c) < 2 for c in clusters) or len(clusters) == 0

    def test_memories_too_close_in_time_rejected(self, base):
        """Memories all created in the same minute don't form a thread."""
        now = datetime.now().isoformat()
        ids = [
            _create_memory(f"Burst {i}", f"Content {i}",
                          tags=["type:correction", "burst"],
                          created_at=now)
            for i in range(3)
        ]

        detector = ThreadDetector()
        clusters = detector.detect_threads()
        assert clusters == []

    def test_exclude_already_threaded(self, base):
        ids = [
            _create_memory(f"Threaded {i}", f"Content {i}",
                          tags=["type:correction", "threading"])
            for i in range(3)
        ]
        self._spread_timestamps(ids)

        _create_thread("Existing", "S", "N", member_ids=ids[:2])

        detector = ThreadDetector()
        clusters = detector.detect_threads(exclude_threaded=True)
        assert clusters == []

    def test_include_already_threaded(self, base):
        ids = [
            _create_memory(f"Rethreadable {i}", f"Content {i}",
                          tags=["type:correction", "rethreading"])
            for i in range(3)
        ]
        self._spread_timestamps(ids)

        _create_thread("Existing", "S", "N", member_ids=ids[:2])

        detector = ThreadDetector()
        clusters = detector.detect_threads(exclude_threaded=False)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_chronological_ordering(self, base):
        """Clusters are returned in chronological order."""
        ids = [
            _create_memory(f"Ordered {i}", f"Content {i}",
                          tags=["type:correction", "ordering"])
            for i in range(3)
        ]
        start = datetime(2026, 1, 1)
        for i, mid in enumerate(reversed(ids)):
            ts = (start + timedelta(hours=i * 48)).isoformat()
            Memory.update(created_at=ts).where(Memory.id == mid).execute()

        detector = ThreadDetector()
        clusters = detector.detect_threads()
        assert len(clusters) == 1
        timestamps = [c.created_at for c in clusters[0]]
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

    def _make_cluster(self, n=3):
        """Create a cluster of related memories."""
        ids = []
        start = datetime(2026, 1, 1)
        for i in range(n):
            ts = (start + timedelta(days=i * 3)).isoformat()
            mid = _create_memory(
                f"Async Fix {i}", f"Fixed async issue #{i}",
                tags=["type:correction", "async"],
                created_at=ts,
            )
            ids.append(mid)
        return [Memory.get_by_id(mid) for mid in ids]

    @patch("core.threads.call_llm")
    def test_narrate_produces_thread(self, mock_llm, base):
        mock_llm.return_value = json.dumps(MOCK_NARRATIVE_RESPONSE)
        cluster = self._make_cluster()

        narrator = ThreadNarrator()
        result = narrator.narrate_cluster(cluster)

        assert result is not None
        assert result["title"] == "Learning async patterns in this codebase"
        assert "async/await" in result["narrative"]
        assert "Current understanding:" in result["narrative"]
        assert result["arc_type"] == "correction_chain"
        assert result["confidence"] == 0.85
        assert len(result["member_ids"]) == 3

    @patch("core.threads.call_llm")
    def test_low_confidence_rejected(self, mock_llm, base):
        """LLM says these don't form a real arc -> None returned."""
        mock_llm.return_value = json.dumps({
            **MOCK_NARRATIVE_RESPONSE,
            "confidence": 0.2,
        })
        cluster = self._make_cluster()

        narrator = ThreadNarrator()
        result = narrator.narrate_cluster(cluster)
        assert result is None

    @patch("core.llm.call_llm", side_effect=Exception("LLM down"))
    def test_llm_failure_returns_none(self, mock_llm, base):
        cluster = self._make_cluster()

        narrator = ThreadNarrator()
        result = narrator.narrate_cluster(cluster)
        assert result is None

    def test_empty_cluster_returns_none(self, base):
        narrator = ThreadNarrator()
        assert narrator.narrate_cluster([]) is None

    def test_strip_frontmatter(self, base):
        narrator = ThreadNarrator()
        content = "---\nname: Test\ndescription: A test\n---\n\nActual content here."
        assert narrator._strip_frontmatter(content) == "Actual content here."

    def test_strip_frontmatter_no_frontmatter(self, base):
        narrator = ThreadNarrator()
        content = "Just plain content."
        assert narrator._strip_frontmatter(content) == "Just plain content."


# ---------------------------------------------------------------------------
# build_threads end-to-end (mocked LLM)
# ---------------------------------------------------------------------------


class TestBuildThreads:

    @patch("core.threads.call_llm")
    def test_end_to_end(self, mock_llm, base):
        mock_llm.return_value = json.dumps(MOCK_NARRATIVE_RESPONSE)

        start = datetime(2026, 1, 1)
        ids = []
        for i in range(3):
            ts = (start + timedelta(days=i * 3)).isoformat()
            mid = _create_memory(
                f"Async {i}", f"Content {i}",
                tags=["type:correction", "async"],
                created_at=ts,
            )
            ids.append(mid)

        created = build_threads()
        assert len(created) == 1
        assert created[0]["title"] == "Learning async patterns in this codebase"
        assert created[0]["id"] is not None

        # Thread is persisted
        thread = _get_thread(created[0]["id"])
        assert len(thread["member_ids"]) == 3

    @patch("core.threads.call_llm")
    def test_no_clusters_no_threads(self, mock_llm, base):
        """No related memories -> no threads created."""
        _create_memory("Lonely", "Content", tags=["unique_tag"])
        created = build_threads()
        assert created == []
        mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# RetrievalEngine thread injection
# ---------------------------------------------------------------------------


class TestRetrievalThreadInjection:

    def test_thread_narratives_injected(self, base):
        """Tier 2 memories with threads get narrative section in output."""
        m1 = _create_memory("Crystal A", "Content A", stage="crystallized")
        m2 = _create_memory("Crystal B", "Content B", stage="crystallized")

        _create_thread(
            "How we learned X", "A story",
            "First we thought A, then learned B.",
            [m1, m2],
        )

        engine = RetrievalEngine()
        output = engine.inject_for_session("test-session")

        assert "Narrative Threads" in output
        assert "How we learned X" in output
        assert "First we thought A, then learned B." in output

    def test_no_threads_no_section(self, base):
        """Without threads, no narrative section appears."""
        _create_memory("Crystal", "Content", stage="crystallized")

        engine = RetrievalEngine()
        output = engine.inject_for_session("test-session")

        assert "Narrative Threads" not in output

    def test_thread_deduplication(self, base):
        """A thread appears once even if multiple of its members are injected."""
        m1 = _create_memory("C1", "Content 1", stage="crystallized")
        m2 = _create_memory("C2", "Content 2", stage="crystallized")

        _create_thread("Shared Thread", "S", "The narrative.", [m1, m2])

        engine = RetrievalEngine()
        output = engine.inject_for_session("test-session")

        assert output.count("Shared Thread") == 1


# ---------------------------------------------------------------------------
# last_surfaced_at schema and migration
# ---------------------------------------------------------------------------


class TestLastSurfacedAtMigration:

    def test_new_store_has_last_surfaced_at_column(self, tmp_path):
        """A freshly initialised DB must include last_surfaced_at in narrative_threads."""
        init_db(base_dir=str(tmp_path / "memory"))
        try:
            cursor = db.execute_sql("PRAGMA table_info(narrative_threads)")
            columns = [row[1] for row in cursor.fetchall()]
            assert "last_surfaced_at" in columns
        finally:
            close_db()

    def test_migration_is_idempotent(self, tmp_path):
        """Opening a DB twice on the same path must not raise."""
        base = str(tmp_path / "memory")
        init_db(base_dir=base)
        close_db()
        init_db(base_dir=base)
        close_db()

    def test_last_surfaced_at_defaults_null(self, base):
        """A newly created thread has last_surfaced_at == None."""
        m1 = _create_memory("SurfaceMe", "Content")
        tid = _create_thread("T", "S", "N", member_ids=[m1])
        thread = _get_thread(tid)
        assert thread["last_surfaced_at"] is None


# ---------------------------------------------------------------------------
# Batch thread query
# ---------------------------------------------------------------------------


class TestBatchThreadQuery:

    def test_empty_list_returns_empty(self, base):
        result = _get_threads_for_memories_batch([])
        assert result == []

    def test_single_memory_matches_per_memory_method(self, base):
        """Batch result for one memory ID must equal get_threads_for_memory for that ID."""
        m1 = _create_memory("Batch M1", "Content one")
        m2 = _create_memory("Batch M2", "Content two")
        _create_thread("Thread X", "S", "N", member_ids=[m1, m2])

        batch_result = _get_threads_for_memories_batch([m1])
        per_memory_result = _get_threads_for_memory(m1)

        assert len(batch_result) == len(per_memory_result)
        assert {t["id"] for t in batch_result} == {t["id"] for t in per_memory_result}

    def test_multiple_memories_deduplicates_shared_thread(self, base):
        """When m1 and m2 share one thread, batch([m1, m2]) returns exactly 1 thread."""
        m1 = _create_memory("Dedup M1", "Content one")
        m2 = _create_memory("Dedup M2", "Content two")
        _create_thread("Shared", "S", "N", member_ids=[m1, m2])

        result = _get_threads_for_memories_batch([m1, m2])
        assert len(result) == 1

    def test_returns_full_narrative(self, base):
        """Each returned thread dict must contain the narrative field with full text."""
        m1 = _create_memory("Narrative M1", "Content")
        narrative_text = "The full and complete narrative story."
        _create_thread("Narrated", "S", narrative_text, member_ids=[m1])

        result = _get_threads_for_memories_batch([m1])
        assert len(result) == 1
        assert result[0]["narrative"] == narrative_text

    def test_no_threads_returns_empty(self, base):
        """A valid memory ID that belongs to no threads returns []."""
        m1 = _create_memory("Unthreaded", "Content")

        result = _get_threads_for_memories_batch([m1])
        assert result == []

    def test_cross_thread_union(self, base):
        """m1->threadA, m2->threadB, m3->both; batch([m1,m2,m3]) returns exactly 2 distinct threads."""
        m1 = _create_memory("Cross M1", "Content one")
        m2 = _create_memory("Cross M2", "Content two")
        m3 = _create_memory("Cross M3", "Content three")

        tid_a = _create_thread("Thread A", "SA", "NA", member_ids=[m1, m3])
        tid_b = _create_thread("Thread B", "SB", "NB", member_ids=[m2, m3])

        result = _get_threads_for_memories_batch([m1, m2, m3])
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
    rng = np.random.default_rng(0)
    base = rng.standard_normal(384)
    base /= np.linalg.norm(base)
    similar = base + rng.standard_normal((cluster_size, 384)) * 0.01
    dissimilar = rng.standard_normal((n - cluster_size, 384))
    all_vecs = np.vstack([similar, dissimilar])
    norms = np.linalg.norm(all_vecs, axis=1, keepdims=True)
    return all_vecs / np.maximum(norms, 1e-9)


def _make_embedding_bytes(values):
    """Convert a list of floats to raw float32 bytes."""
    return struct.pack(f"{len(values)}f", *values)


# ---------------------------------------------------------------------------
# Embedding-based thread clustering (D-09, D-10)
# ---------------------------------------------------------------------------


class TestEmbeddingClustering:
    """Tests for embedding-based clustering in ThreadDetector.detect_threads."""

    def _spread_timestamps(self, mem_ids, start=None, gap_hours=48):
        """Set created_at timestamps with a gap between each memory."""
        if start is None:
            start = datetime(2026, 1, 1)
        for i, mid in enumerate(mem_ids):
            ts = (start + timedelta(hours=i * gap_hours)).isoformat()
            Memory.update(created_at=ts).where(Memory.id == mid).execute()

    def test_semantically_similar_memories_cluster(self, base):
        """Memories with highly similar embeddings form at least one cluster."""
        ids = [
            _create_memory(
                f"Async Fix {i}", f"Fixed async issue {i}",
                tags=["type:correction", "async"],
            )
            for i in range(3)
        ]
        self._spread_timestamps(ids)

        embeddings = _cluster_embeddings(3, cluster_size=3)
        detector = ThreadDetector()

        id_to_bytes = {
            mid: _make_embedding_bytes(embeddings[i].tolist())
            for i, mid in enumerate(ids)
        }

        vec = get_vec_store()
        with patch.object(vec, "get_embedding", side_effect=lambda mid: id_to_bytes.get(mid)):
            clusters = detector.detect_threads()

        assert len(clusters) >= 1

    def test_semantically_dissimilar_memories_do_not_cluster(self, base):
        """Memories with dissimilar embeddings and no tag overlap do not cluster."""
        ids = [
            _create_memory(
                f"Unrelated Topic {i}", f"Content about unrelated subject {i}",
                tags=[f"unique_embed_tag_{i}"],
            )
            for i in range(3)
        ]
        self._spread_timestamps(ids)

        embeddings = _fake_embeddings(3)
        detector = ThreadDetector()

        id_to_bytes = {
            mid: _make_embedding_bytes(embeddings[i].tolist())
            for i, mid in enumerate(ids)
        }

        vec = get_vec_store()
        with patch.object(vec, "get_embedding", side_effect=lambda mid: id_to_bytes.get(mid)):
            clusters = detector.detect_threads()

        assert all(len(c) < 2 for c in clusters) or len(clusters) == 0

    def test_clustering_fallback_when_unavailable(self, base):
        """When get_embedding returns None, tag-overlap fallback still forms clusters."""
        ids = [
            _create_memory(
                f"Tag Cluster {i}", f"Content {i}",
                tags=["type:correction", "fallback-topic"],
            )
            for i in range(3)
        ]
        self._spread_timestamps(ids)

        detector = ThreadDetector()

        vec = get_vec_store()
        with patch.object(vec, "get_embedding", return_value=None):
            clusters = detector.detect_threads()

        assert len(clusters) >= 1
        all_member_ids = {m.id for c in clusters for m in c}
        assert all(mid in all_member_ids for mid in ids)
