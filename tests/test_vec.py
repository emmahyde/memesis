"""
Tests for core.vec.VecStore — embedding metadata, dimension validation, backfill.

Uses tmp_path for isolation. Tests skip when sqlite-vec is unavailable.
All tests use a local index.db that is separate from ~/.claude/memory.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

try:
    import apsw
    import sqlite_vec  # noqa: F401

    _VEC_AVAILABLE = True
except ImportError:
    _VEC_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _VEC_AVAILABLE, reason="sqlite-vec not installed"
)

# ---------------------------------------------------------------------------
# Constants (mirror core.embeddings defaults)
# ---------------------------------------------------------------------------

_DIM = 512  # DEFAULT_DIMENSIONS
_EXPECTED_BYTES = _DIM * 4  # float32


def _make_embedding(dim: int = _DIM, fill: float = 1.0) -> bytes:
    """Produce a `dim`-float bytes object (all set to `fill`)."""
    return struct.pack(f"{dim}f", *([fill] * dim))


GOOD_EMBEDDING = _make_embedding(_DIM)
SHORT_EMBEDDING = _make_embedding(4)   # 4 floats — wrong dim
LONG_EMBEDDING = _make_embedding(1024)  # 1024 floats — wrong dim


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tables(db_path: Path) -> None:
    """Create the companion tables that migrations normally create."""
    import apsw as _apsw
    conn = _apsw.Connection(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vec_embedding_meta (
            memory_id TEXT PRIMARY KEY,
            embedding_model TEXT NOT NULL DEFAULT '',
            embedding_version TEXT NOT NULL DEFAULT '',
            embedding_dim INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _system (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.close()


def make_store(db_path: Path):
    """Create a VecStore against the given db_path (with companion tables pre-created)."""
    from core.vec import VecStore

    _make_tables(db_path)
    return VecStore(db_path)


# ---------------------------------------------------------------------------
# Tests: dimension validation
# ---------------------------------------------------------------------------

class TestDimensionValidation:
    def test_correct_dim_stores_without_error(self, tmp_path: Path):
        """Embedding with correct dim stores successfully."""
        store = make_store(tmp_path / "index.db")
        assert store.available
        # Should not raise
        store.store_embedding("mem-1", GOOD_EMBEDDING)

    def test_short_embedding_raises_value_error(self, tmp_path: Path):
        """Embedding with too few floats raises ValueError."""
        store = make_store(tmp_path / "index.db")
        with pytest.raises(ValueError, match="dimension mismatch"):
            store.store_embedding("mem-short", SHORT_EMBEDDING)

    def test_long_embedding_raises_value_error(self, tmp_path: Path):
        """Embedding with too many floats raises ValueError."""
        store = make_store(tmp_path / "index.db")
        with pytest.raises(ValueError, match="dimension mismatch"):
            store.store_embedding("mem-long", LONG_EMBEDDING)

    def test_error_message_includes_expected_and_actual(self, tmp_path: Path):
        """ValueError message includes expected and actual dimensions."""
        store = make_store(tmp_path / "index.db")
        with pytest.raises(ValueError) as exc_info:
            store.store_embedding("mem-bad", SHORT_EMBEDDING)
        msg = str(exc_info.value)
        assert "512" in msg  # expected dim
        assert "4" in msg    # actual dim


# ---------------------------------------------------------------------------
# Tests: metadata round-trip
# ---------------------------------------------------------------------------

class TestMetadataRoundTrip:
    def test_store_writes_metadata(self, tmp_path: Path):
        """store_embedding writes vec_embedding_meta row."""
        store = make_store(tmp_path / "index.db")
        store.store_embedding("mem-1", GOOD_EMBEDDING)

        meta = store.get_embedding_meta("mem-1")
        assert meta is not None
        assert meta["memory_id"] == "mem-1"
        assert meta["embedding_model"] == store._embedding_model
        assert meta["embedding_version"] == store._embedding_version
        assert meta["embedding_dim"] == store._embedding_dim

    def test_metadata_uses_current_constants(self, tmp_path: Path):
        """Metadata matches current DEFAULT_EMBEDDING_MODEL / DEFAULT_DIMENSIONS."""
        from core.embeddings import (
            DEFAULT_DIMENSIONS,
            DEFAULT_EMBEDDING_MODEL,
            DEFAULT_EMBEDDING_VERSION,
        )
        store = make_store(tmp_path / "index.db")
        store.store_embedding("mem-2", GOOD_EMBEDDING)

        meta = store.get_embedding_meta("mem-2")
        assert meta["embedding_model"] == DEFAULT_EMBEDDING_MODEL
        assert meta["embedding_version"] == DEFAULT_EMBEDDING_VERSION
        assert meta["embedding_dim"] == DEFAULT_DIMENSIONS

    def test_overwrite_updates_metadata(self, tmp_path: Path):
        """Calling store_embedding twice on same id updates metadata."""
        store = make_store(tmp_path / "index.db")
        store.store_embedding("mem-1", GOOD_EMBEDDING)
        store.store_embedding("mem-1", GOOD_EMBEDDING)  # overwrite

        meta = store.get_embedding_meta("mem-1")
        assert meta is not None
        assert meta["embedding_dim"] == _DIM

    def test_get_embedding_meta_missing_returns_none(self, tmp_path: Path):
        """get_embedding_meta returns None for unknown memory_id."""
        store = make_store(tmp_path / "index.db")
        meta = store.get_embedding_meta("nonexistent-id")
        assert meta is None


# ---------------------------------------------------------------------------
# Tests: _system table population
# ---------------------------------------------------------------------------

class TestSystemTable:
    def test_system_table_populated_on_init(self, tmp_path: Path):
        """VecStore init writes active embedding constants to _system table."""
        store = make_store(tmp_path / "index.db")
        assert store.available

        # Read back via apsw directly
        conn = apsw.Connection(str(tmp_path / "index.db"))
        rows = {key: val for key, val in conn.execute("SELECT key, value FROM _system")}
        conn.close()

        assert rows.get("embedding_model") == store._embedding_model
        assert rows.get("embedding_version") == store._embedding_version
        assert rows.get("embedding_dim") == str(store._embedding_dim)

    def test_system_table_survives_reinit(self, tmp_path: Path):
        """Re-creating VecStore on same db updates _system without error."""
        db_path = tmp_path / "index.db"
        _make_tables(db_path)

        from core.vec import VecStore
        store1 = VecStore(db_path)
        assert store1.available

        store2 = VecStore(db_path)
        assert store2.available

        conn = apsw.Connection(str(db_path))
        rows = {key: val for key, val in conn.execute("SELECT key, value FROM _system")}
        conn.close()
        assert "embedding_model" in rows


# ---------------------------------------------------------------------------
# Tests: backfill
# ---------------------------------------------------------------------------

class TestBackfill:
    def test_backfill_updates_blank_embedding_model(self, tmp_path: Path):
        """Rows with empty embedding_model get backfilled on VecStore init."""
        db_path = tmp_path / "index.db"
        _make_tables(db_path)

        # Insert a stale row with blank embedding_model (simulating pre-migration state)
        conn = apsw.Connection(str(db_path))
        conn.execute(
            "INSERT INTO vec_embedding_meta (memory_id, embedding_model, embedding_version, embedding_dim) "
            "VALUES ('stale-mem', '', '', 0)"
        )
        conn.close()

        from core.vec import VecStore
        store = VecStore(db_path)
        assert store.available

        # After init, backfill should have populated the stale row
        meta = store.get_embedding_meta("stale-mem")
        assert meta is not None
        assert meta["embedding_model"] == store._embedding_model
        assert meta["embedding_version"] == store._embedding_version
        assert meta["embedding_dim"] == store._embedding_dim

    def test_backfill_leaves_populated_rows_unchanged(self, tmp_path: Path):
        """Rows with non-blank embedding_model are not overwritten by backfill."""
        db_path = tmp_path / "index.db"
        _make_tables(db_path)

        # Insert a row that already has metadata (different model)
        conn = apsw.Connection(str(db_path))
        conn.execute(
            "INSERT INTO vec_embedding_meta (memory_id, embedding_model, embedding_version, embedding_dim) "
            "VALUES ('good-mem', 'some-other-model', 'v1', 256)"
        )
        conn.close()

        from core.vec import VecStore
        VecStore(db_path)

        # Check via apsw directly (not get_embedding_meta, which returns current model)
        conn = apsw.Connection(str(db_path))
        row = next(
            conn.execute(
                "SELECT embedding_model, embedding_version, embedding_dim FROM vec_embedding_meta WHERE memory_id = 'good-mem'"
            ),
            None,
        )
        conn.close()

        assert row is not None
        assert row[0] == "some-other-model"  # not overwritten
        assert row[1] == "v1"
        assert row[2] == 256

    def test_backfill_idempotent(self, tmp_path: Path):
        """Running backfill (via init) twice does not corrupt data."""
        db_path = tmp_path / "index.db"
        _make_tables(db_path)

        # Insert stale row
        conn = apsw.Connection(str(db_path))
        conn.execute(
            "INSERT INTO vec_embedding_meta (memory_id, embedding_model, embedding_version, embedding_dim) "
            "VALUES ('mem-x', '', '', 0)"
        )
        conn.close()

        from core.vec import VecStore
        store1 = VecStore(db_path)
        store2 = VecStore(db_path)  # second init — idempotent

        meta = store2.get_embedding_meta("mem-x")
        assert meta is not None
        assert meta["embedding_model"] == store2._embedding_model
        assert meta["embedding_dim"] == store2._embedding_dim


# ---------------------------------------------------------------------------
# Tests: reindex idempotency
# ---------------------------------------------------------------------------

class TestReindexIdempotency:
    def test_store_same_embedding_twice_idempotent(self, tmp_path: Path):
        """Storing the same embedding twice leaves the same state."""
        store = make_store(tmp_path / "index.db")
        store.store_embedding("mem-1", GOOD_EMBEDDING)
        store.store_embedding("mem-1", GOOD_EMBEDDING)

        # Retrieval should still work
        emb = store.get_embedding("mem-1")
        assert emb is not None
        assert len(emb) == _EXPECTED_BYTES

        meta = store.get_embedding_meta("mem-1")
        assert meta["embedding_dim"] == _DIM

    def test_store_multiple_memories_idempotent(self, tmp_path: Path):
        """Storing N memories twice (simulating reindex) yields same N metadata rows."""
        store = make_store(tmp_path / "index.db")
        memory_ids = [f"mem-{i}" for i in range(5)]

        # First pass
        for mid in memory_ids:
            store.store_embedding(mid, GOOD_EMBEDDING)

        # Second pass (reindex)
        for mid in memory_ids:
            store.store_embedding(mid, GOOD_EMBEDDING)

        # Check all metadata is present and correct
        for mid in memory_ids:
            meta = store.get_embedding_meta(mid)
            assert meta is not None, f"Missing meta for {mid}"
            assert meta["embedding_dim"] == _DIM
