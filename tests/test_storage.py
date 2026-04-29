"""
Tests for database schema migrations and Memory model storage.

Covers Task 3.1 acceptance criteria:
- TestSchemaMigrationIdempotency
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, _SCHEMA_VERSION
from core.models import Memory, db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base(tmp_path):
    """Initialize DB in a throwaway temp directory."""
    base_dir = init_db(base_dir=str(tmp_path / "memory"))
    yield base_dir
    close_db()


# ---------------------------------------------------------------------------
# TestSchemaMigrationIdempotency
# ---------------------------------------------------------------------------

class TestSchemaMigrationIdempotency:
    def test_migration_idempotent(self, tmp_path):
        """Calling init_db twice against the same path must not raise,
        and all four new columns must be present afterward."""
        db_dir = str(tmp_path / "memory")

        # First init
        init_db(base_dir=db_dir)
        close_db()

        # Second init — idempotent
        init_db(base_dir=db_dir)

        cursor = db.execute_sql("PRAGMA table_info(memories)")
        cols = [row[1] for row in cursor.fetchall()]

        assert "temporal_scope" in cols
        assert "confidence" in cols
        assert "affect_valence" in cols
        assert "actor" in cols

        close_db()

    def test_new_columns_nullable(self, base):
        """Create a Memory without the new fields; it must save and reload with None values."""
        from datetime import datetime
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="consolidated",
            title="Test nullable columns",
            summary="Testing that new columns default to None.",
            content="Some content.",
            tags="[]",
            importance=0.5,
            created_at=now,
            updated_at=now,
        )
        mem_id = mem.id

        # Reload from DB
        reloaded = Memory.get_by_id(mem_id)
        assert reloaded.temporal_scope is None
        assert reloaded.confidence is None
        assert reloaded.affect_valence is None
        assert reloaded.actor is None

    def test_user_version_bumped(self, tmp_path):
        """After migration, PRAGMA user_version must equal _SCHEMA_VERSION."""
        db_dir = str(tmp_path / "uv_memory")
        init_db(base_dir=db_dir)

        cursor = db.execute_sql("PRAGMA user_version")
        version = cursor.fetchone()[0]
        assert version == _SCHEMA_VERSION

        close_db()
