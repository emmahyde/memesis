"""
Tests for core.session_vec.SessionVecStore.

Uses deterministic small float vectors (4-dim rather than 512) — BUT sqlite-vec
requires the declared dimension to match the actual bytes. So we use 512-dim
zero-padded vectors: the test helper produces a 512-float bytes object where
only the first few values differ between "similar" and "dissimilar" vectors.

All tests use tmp_path for isolation — no real ~/.claude/memory access.
"""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIM = 512


def _make_embedding(values: list[float]) -> bytes:
    """Produce a _DIM-float bytes object with `values` at [0:len(values)], rest 0."""
    assert len(values) <= _DIM
    floats = values + [0.0] * (_DIM - len(values))
    return struct.pack(f"{_DIM}f", *floats)


# Two "orthogonal" unit vectors in 512-d space (well, just the first component differs).
EMBED_A = _make_embedding([1.0, 0.0])   # "topic A"
EMBED_B = _make_embedding([0.0, 1.0])   # "topic B" — orthogonal to A


# ---------------------------------------------------------------------------
# Skip all tests if sqlite-vec is unavailable (CI without the extension)
# ---------------------------------------------------------------------------

try:
    import sqlite_vec  # noqa: F401
    _VEC_AVAILABLE = True
except ImportError:
    _VEC_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _VEC_AVAILABLE, reason="sqlite-vec not installed"
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path: Path):
    """Initialise a fresh isolated DB and yield its path."""
    from core.database import init_db, close_db
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path / "memory" / "index.db"
    close_db()


def make_store(session_id: str = "test-session-001"):
    from core.session_vec import SessionVecStore
    import tempfile, pathlib
    db = pathlib.Path(tempfile.mkdtemp()) / "session.db"
    return SessionVecStore(db, session_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSessionVecIndex:
    def test_add_and_query_returns_self(self, db_path: Path):
        """Single insert, query with same embedding → returns that obs_idx."""
        store = make_store()
        assert store.available

        ok = store.add(obs_idx=0, embedding=EMBED_A)
        assert ok

        results = store.query_similar(EMBED_A, k=3)
        assert results == [0]

    def test_query_empty_returns_empty(self, db_path: Path):
        """Querying an empty index returns []."""
        store = make_store()
        results = store.query_similar(EMBED_A, k=3)
        assert results == []

    def test_query_returns_similar_ordered(self, db_path: Path):
        """Insert A and B; querying with A vector returns A first."""
        store = make_store()
        store.add(obs_idx=0, embedding=EMBED_A)
        store.add(obs_idx=1, embedding=EMBED_B)

        results = store.query_similar(EMBED_A, k=2)
        # A should be first (distance 0); B second
        assert results[0] == 0

    def test_top_k_respected(self, db_path: Path):
        """Insert 5 embeddings, query top_k=2 → 2 results."""
        store = make_store()
        for i in range(5):
            store.add(obs_idx=i, embedding=EMBED_A)

        results = store.query_similar(EMBED_A, k=2)
        assert len(results) == 2

    def test_clear_removes_entries(self, db_path: Path):
        """After drop(), query returns 0 results (table gone)."""
        store = make_store()
        store.add(obs_idx=0, embedding=EMBED_A)

        store.drop()

        # After drop, available should be False
        assert not store.available

        # query_similar on unavailable store returns []
        results = store.query_similar(EMBED_A, k=3)
        assert results == []

    def test_session_isolation(self, db_path: Path):
        """Two SessionVecStore instances with different session_ids don't see each other's entries."""
        store_a = make_store(session_id="session-alpha")
        store_b = make_store(session_id="session-beta")

        assert store_a._table != store_b._table

        store_a.add(obs_idx=0, embedding=EMBED_A)

        # store_b has its own empty table — should return nothing
        results_b = store_b.query_similar(EMBED_A, k=3)
        assert results_b == []

        # store_a should return its own entry
        results_a = store_a.query_similar(EMBED_A, k=3)
        assert results_a == [0]

    def test_add_wrong_embedding_size_skipped(self, db_path: Path):
        """Embedding with wrong byte length returns False without crash."""
        store = make_store()
        bad_embedding = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)  # only 4 floats
        result = store.add(obs_idx=0, embedding=bad_embedding)
        assert result is False

    def test_add_none_embedding_skipped(self, db_path: Path):
        """None embedding returns False without crash."""
        store = make_store()
        result = store.add(obs_idx=0, embedding=None)
        assert result is False

    def test_query_none_embedding_returns_empty(self, db_path: Path):
        """None query embedding returns [] without crash."""
        store = make_store()
        store.add(obs_idx=0, embedding=EMBED_A)
        results = store.query_similar(None, k=3)
        assert results == []

    def test_drop_idempotent(self, db_path: Path):
        """Calling drop() twice does not raise."""
        store = make_store()
        store.add(obs_idx=0, embedding=EMBED_A)
        store.drop()
        store.drop()  # should not raise
