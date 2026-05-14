"""
Tests for core.vec.VecStore — numpy-backed KNN, dimension validation, metadata.

Uses conftest.py fixtures for DB isolation. Tests NEVER touch ~/.claude/memory.
No apsw, no sqlite_vec, no raw sqlite3 connections to index.db.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Constants (mirror core.embeddings defaults)
# ---------------------------------------------------------------------------

_DIM = 384
_EXPECTED_BYTES = _DIM * 4  # float32


def _normalized_vector(seed: int, dim: int = _DIM) -> bytes:
    """Produce a normalized random float32 vector as bytes."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tobytes()


def _one_hot(idx: int, dim: int = _DIM) -> bytes:
    """Produce a one-hot float32 vector (already unit-norm)."""
    v = np.zeros(dim, dtype=np.float32)
    v[idx] = 1.0
    return v.tobytes()


GOOD_EMBEDDING = _normalized_vector(seed=0)
SHORT_EMBEDDING = np.zeros(4, dtype=np.float32).tobytes()
LONG_EMBEDDING = np.zeros(1024, dtype=np.float32).tobytes()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def vec_store(temp_dir):
    """Initialize DB at temp_dir and return a VecStore."""
    from core.database import init_db, close_db, get_vec_store
    init_db(base_dir=str(temp_dir))
    yield get_vec_store()
    close_db()


# ---------------------------------------------------------------------------
# Tests: dimension validation
# ---------------------------------------------------------------------------

class TestDimensionValidation:
    def test_correct_dim_stores_without_error(self, vec_store):
        """Embedding with correct dim stores successfully."""
        assert vec_store.available
        vec_store.store_embedding("mem-1", GOOD_EMBEDDING)

    def test_short_embedding_raises_value_error(self, vec_store):
        """Embedding with too few floats raises ValueError."""
        with pytest.raises(ValueError, match="dimension mismatch"):
            vec_store.store_embedding("mem-short", SHORT_EMBEDDING)

    def test_long_embedding_raises_value_error(self, vec_store):
        """Embedding with too many floats raises ValueError."""
        with pytest.raises(ValueError, match="dimension mismatch"):
            vec_store.store_embedding("mem-long", LONG_EMBEDDING)

    def test_error_message_includes_expected_and_actual(self, vec_store):
        """ValueError message includes expected and actual dimensions."""
        with pytest.raises(ValueError) as exc_info:
            vec_store.store_embedding("mem-bad", SHORT_EMBEDDING)
        msg = str(exc_info.value)
        assert "384" in msg  # expected dim
        assert "4" in msg    # actual dim (4 floats)


# ---------------------------------------------------------------------------
# Tests: metadata round-trip
# ---------------------------------------------------------------------------

class TestMetadataRoundTrip:
    def test_store_writes_metadata(self, vec_store):
        """store_embedding writes metadata columns on memory_embeddings row."""
        vec_store.store_embedding("mem-1", GOOD_EMBEDDING)

        meta = vec_store.get_embedding_meta("mem-1")
        assert meta is not None
        assert meta["memory_id"] == "mem-1"
        assert meta["embedding_model"] == vec_store._embedding_model
        assert meta["embedding_version"] == vec_store._embedding_version
        assert meta["embedding_dim"] == vec_store._embedding_dim

    def test_metadata_uses_current_constants(self, vec_store):
        """Metadata matches current DEFAULT_EMBEDDING_MODEL / DEFAULT_DIMENSIONS."""
        from core.embeddings import (
            DEFAULT_DIMENSIONS,
            DEFAULT_EMBEDDING_MODEL,
            DEFAULT_EMBEDDING_VERSION,
        )
        vec_store.store_embedding("mem-2", GOOD_EMBEDDING)

        meta = vec_store.get_embedding_meta("mem-2")
        assert meta["embedding_model"] == DEFAULT_EMBEDDING_MODEL
        assert meta["embedding_version"] == DEFAULT_EMBEDDING_VERSION
        assert meta["embedding_dim"] == DEFAULT_DIMENSIONS

    def test_overwrite_updates_metadata(self, vec_store):
        """Calling store_embedding twice on same id updates the row."""
        vec_store.store_embedding("mem-1", GOOD_EMBEDDING)
        vec_store.store_embedding("mem-1", GOOD_EMBEDDING)

        meta = vec_store.get_embedding_meta("mem-1")
        assert meta is not None
        assert meta["embedding_dim"] == _DIM

    def test_get_embedding_meta_missing_returns_none(self, vec_store):
        """get_embedding_meta returns None for unknown memory_id."""
        meta = vec_store.get_embedding_meta("nonexistent-id")
        assert meta is None


# ---------------------------------------------------------------------------
# Tests: reindex idempotency
# ---------------------------------------------------------------------------

class TestReindexIdempotency:
    def test_store_same_embedding_twice_idempotent(self, vec_store):
        """Storing the same embedding twice leaves a clean, readable state."""
        vec_store.store_embedding("mem-1", GOOD_EMBEDDING)
        vec_store.store_embedding("mem-1", GOOD_EMBEDDING)

        emb = vec_store.get_embedding("mem-1")
        assert emb is not None
        assert len(emb) == _EXPECTED_BYTES

        meta = vec_store.get_embedding_meta("mem-1")
        assert meta["embedding_dim"] == _DIM

    def test_store_multiple_memories_idempotent(self, vec_store):
        """Storing N memories twice (simulating reindex) yields correct N rows."""
        memory_ids = [f"mem-{i}" for i in range(5)]

        for mid in memory_ids:
            vec_store.store_embedding(mid, GOOD_EMBEDDING)

        for mid in memory_ids:
            vec_store.store_embedding(mid, GOOD_EMBEDDING)

        for mid in memory_ids:
            meta = vec_store.get_embedding_meta(mid)
            assert meta is not None, f"Missing meta for {mid}"
            assert meta["embedding_dim"] == _DIM


# ---------------------------------------------------------------------------
# Tests: KNN search
# ---------------------------------------------------------------------------

class TestKNNSearch:
    def test_search_returns_empty_when_no_embeddings(self, vec_store):
        """No rows — search_vector returns []."""
        query = _normalized_vector(seed=42)
        results = vec_store.search_vector(query, k=5)
        assert results == []

    def test_search_returns_nearest_neighbor(self, vec_store):
        """Query close to embedding[0] puts mem-0 first."""
        # Use one-hot at distinct positions so distances are exact
        vec_store.store_embedding("mem-0", _one_hot(0))
        vec_store.store_embedding("mem-1", _one_hot(1))
        vec_store.store_embedding("mem-2", _one_hot(2))

        # Query = one-hot at 0 → mem-0 should be closest
        results = vec_store.search_vector(_one_hot(0), k=3)
        assert len(results) >= 1
        assert results[0]["memory_id"] == "mem-0"
        assert results[0]["distance"] == pytest.approx(0.0, abs=1e-5)

    def test_search_respects_k(self, vec_store):
        """search_vector with k=2 returns at most 2 results from 5 stored."""
        for i in range(5):
            vec_store.store_embedding(f"mem-{i}", _normalized_vector(seed=i))

        results = vec_store.search_vector(_normalized_vector(seed=99), k=2)
        assert len(results) == 2

    def test_search_excludes_ids(self, vec_store):
        """exclude_ids removes those memory_ids from results."""
        vec_store.store_embedding("mem-0", _one_hot(0))
        vec_store.store_embedding("mem-1", _one_hot(1))
        vec_store.store_embedding("mem-2", _one_hot(2))

        results = vec_store.search_vector(_one_hot(0), k=3, exclude_ids={"mem-0"})
        returned_ids = {r["memory_id"] for r in results}
        assert "mem-0" not in returned_ids
        assert len(results) <= 2

    def test_search_orders_by_distance(self, vec_store):
        """Results come back in ascending distance order."""
        # Use one-hots: query at index 0, so mem-0 is closest (dist=0),
        # mem-1/mem-2 are equally far (cosine dist = 1).
        vec_store.store_embedding("mem-0", _one_hot(0))
        vec_store.store_embedding("mem-1", _one_hot(1))
        vec_store.store_embedding("mem-2", _one_hot(2))

        results = vec_store.search_vector(_one_hot(0), k=3)
        distances = [r["distance"] for r in results]
        assert distances == sorted(distances)

    def test_search_skips_dim_mismatched_rows(self, vec_store):
        """Rows whose embedding_dim differs from current are excluded from search."""
        from core.models import MemoryEmbedding
        from datetime import datetime, timezone

        # Insert a row with a wrong dim directly via Peewee (not store_embedding,
        # which would reject it via ValueError)
        wrong_dim = 128
        wrong_vec = np.zeros(wrong_dim, dtype=np.float32)
        wrong_vec[0] = 1.0
        MemoryEmbedding.insert(
            memory_id="mem-wrong-dim",
            embedding=wrong_vec.tobytes(),
            embedding_model="test-model",
            embedding_version="v0",
            embedding_dim=wrong_dim,
            updated_at=datetime.now(timezone.utc),
        ).on_conflict_replace().execute()

        # Also store a correctly-dimensioned row
        vec_store.store_embedding("mem-good", _one_hot(0))

        results = vec_store.search_vector(_one_hot(0), k=10)
        returned_ids = {r["memory_id"] for r in results}
        assert "mem-wrong-dim" not in returned_ids
        assert "mem-good" in returned_ids
