"""Tests for storage layer."""

import json
import sqlite3
import struct
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.storage import MemoryStore


class TestMemoryStoreInit:
    """Test MemoryStore initialization."""

    def test_default_base_dir(self, temp_dir, monkeypatch):
        """Test default base directory is ~/.claude/memory."""
        monkeypatch.setenv('HOME', str(temp_dir))
        store = MemoryStore()
        assert store.base_dir == temp_dir / '.claude' / 'memory'
        assert store.project_context is None

    def test_explicit_base_dir(self, temp_dir):
        """Test explicit base directory."""
        store = MemoryStore(base_dir=str(temp_dir / 'custom'))
        assert store.base_dir == temp_dir / 'custom'

    def test_project_context(self, temp_dir, monkeypatch):
        """Test project-specific storage."""
        monkeypatch.setenv('HOME', str(temp_dir))
        store = MemoryStore(project_context='/Users/test/my-project')

        expected = temp_dir / '.claude' / 'projects' / '-Users-test-my-project' / 'memory'
        assert store.base_dir == expected
        assert store.project_context == '/Users/test/my-project'

    def test_init_dirs_creates_structure(self, memory_store):
        """Test directory structure creation."""
        memory_store.init_dirs()

        assert (memory_store.base_dir / 'ephemeral').exists()
        assert (memory_store.base_dir / 'consolidated').exists()
        assert (memory_store.base_dir / 'crystallized').exists()
        assert (memory_store.base_dir / 'instinctive').exists()
        assert (memory_store.base_dir / 'meta').exists()
        assert (memory_store.base_dir / 'archived').exists()

    def test_database_initialized(self, memory_store):
        """Test SQLite database is initialized with correct schema."""
        assert memory_store.db_path.exists()

        with sqlite3.connect(memory_store.db_path) as conn:
            cursor = conn.cursor()

            # Check tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}

            assert 'memories' in tables
            assert 'memories_fts' in tables
            assert 'retrieval_log' in tables
            assert 'consolidation_log' in tables

            # Check WAL mode
            cursor.execute('PRAGMA journal_mode')
            assert cursor.fetchone()[0] == 'wal'


class TestMemoryStoreCRUD:
    """Test CRUD operations."""

    def test_create_memory(self, memory_store):
        """Test creating a new memory."""
        content = "Test memory content"
        metadata = {
            'stage': 'ephemeral',
            'title': 'Test Memory',
            'summary': 'A test memory',
            'tags': ['test', 'python'],
            'importance': 0.7,
            'type': 'feedback'
        }

        memory_id = memory_store.create('test_memory.md', content, metadata)

        # Verify UUID format
        assert len(memory_id) == 36
        assert memory_id.count('-') == 4

        # Verify file created
        file_path = memory_store.base_dir / 'ephemeral' / 'test_memory.md'
        assert file_path.exists()

        # Verify content format
        file_content = file_path.read_text()
        assert '---' in file_content
        assert 'name: Test Memory' in file_content
        assert 'description: A test memory' in file_content
        assert 'type: feedback' in file_content
        assert content in file_content

        # Verify database entry
        with sqlite3.connect(memory_store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM memories WHERE id = ?', (memory_id,))
            row = cursor.fetchone()

            assert row is not None
            assert row['stage'] == 'ephemeral'
            assert row['title'] == 'Test Memory'
            assert row['summary'] == 'A test memory'
            assert json.loads(row['tags']) == ['test', 'python']
            assert row['importance'] == 0.7
            assert row['content_hash'] is not None

    def test_create_with_invalid_stage(self, memory_store):
        """Test creating memory with invalid stage raises error."""
        with pytest.raises(ValueError, match="Invalid stage"):
            memory_store.create(
                'test.md',
                'content',
                {'stage': 'invalid_stage'}
            )

    def test_create_duplicate_content(self, memory_store):
        """Test creating duplicate content raises error."""
        content = "Duplicate content"
        metadata = {'stage': 'ephemeral', 'title': 'Test'}

        memory_store.create('test1.md', content, metadata)

        with pytest.raises(ValueError, match="Duplicate content detected"):
            memory_store.create('test2.md', content, metadata)

    def test_get_memory(self, memory_store):
        """Test retrieving a memory."""
        content = "Test content"
        metadata = {
            'stage': 'ephemeral',
            'title': 'Test',
            'summary': 'Summary',
            'tags': ['tag1']
        }

        memory_id = memory_store.create('test.md', content, metadata)
        result = memory_store.get(memory_id)

        assert result['id'] == memory_id
        assert result['stage'] == 'ephemeral'
        assert result['title'] == 'Test'
        assert result['summary'] == 'Summary'
        assert result['tags'] == ['tag1']
        assert 'Test content' in result['content']

    def test_get_nonexistent_memory(self, memory_store):
        """Test getting nonexistent memory raises error."""
        with pytest.raises(ValueError, match="Memory not found"):
            memory_store.get('00000000-0000-0000-0000-000000000000')

    def test_update_memory_content(self, memory_store):
        """Test updating memory content."""
        memory_id = memory_store.create(
            'test.md',
            'Original content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        memory_store.update(memory_id, content='Updated content')

        result = memory_store.get(memory_id)
        assert 'Updated content' in result['content']
        assert 'Original content' not in result['content']

    def test_update_memory_metadata(self, memory_store):
        """Test updating memory metadata."""
        memory_id = memory_store.create(
            'test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Original', 'importance': 0.5}
        )

        memory_store.update(
            memory_id,
            metadata={'title': 'Updated', 'importance': 0.8}
        )

        result = memory_store.get(memory_id)
        assert result['title'] == 'Updated'
        assert result['importance'] == 0.8

    def test_update_memory_stage(self, memory_store):
        """Test updating memory stage moves file."""
        memory_id = memory_store.create(
            'test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        old_path = memory_store.base_dir / 'ephemeral' / 'test.md'
        assert old_path.exists()

        memory_store.update(memory_id, metadata={'stage': 'consolidated'})

        new_path = memory_store.base_dir / 'consolidated' / 'test.md'
        assert new_path.exists()
        assert not old_path.exists()

        result = memory_store.get(memory_id)
        assert result['stage'] == 'consolidated'

    def test_update_nonexistent_memory(self, memory_store):
        """Test updating nonexistent memory raises error."""
        with pytest.raises(ValueError, match="Memory not found"):
            memory_store.update('00000000-0000-0000-0000-000000000000', content='test')

    def test_delete_memory(self, memory_store):
        """Test deleting a memory."""
        memory_id = memory_store.create(
            'test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        file_path = memory_store.base_dir / 'ephemeral' / 'test.md'
        assert file_path.exists()

        memory_store.delete(memory_id)

        assert not file_path.exists()

        with pytest.raises(ValueError, match="Memory not found"):
            memory_store.get(memory_id)

    def test_delete_nonexistent_memory(self, memory_store):
        """Test deleting nonexistent memory raises error."""
        with pytest.raises(ValueError, match="Memory not found"):
            memory_store.delete('00000000-0000-0000-0000-000000000000')

    def test_list_by_stage(self, memory_store):
        """Test listing memories by stage."""
        # Create memories in different stages
        id1 = memory_store.create('test1.md', 'Content 1', {'stage': 'ephemeral', 'title': 'Test 1'})
        id2 = memory_store.create('test2.md', 'Content 2', {'stage': 'ephemeral', 'title': 'Test 2'})
        id3 = memory_store.create('test3.md', 'Content 3', {'stage': 'consolidated', 'title': 'Test 3'})

        ephemeral = memory_store.list_by_stage('ephemeral')
        assert len(ephemeral) == 2
        assert {m['id'] for m in ephemeral} == {id1, id2}

        consolidated = memory_store.list_by_stage('consolidated')
        assert len(consolidated) == 1
        assert consolidated[0]['id'] == id3

    def test_list_by_invalid_stage(self, memory_store):
        """Test listing with invalid stage raises error."""
        with pytest.raises(ValueError, match="Invalid stage"):
            memory_store.list_by_stage('invalid')


class TestMemoryStoreSearch:
    """Test search operations."""

    def test_search_fts_by_title(self, memory_store):
        """Test FTS search by title."""
        memory_store.create(
            'python.md',
            'Python tips',
            {'stage': 'ephemeral', 'title': 'Python Best Practices'}
        )
        memory_store.create(
            'ruby.md',
            'Ruby tips',
            {'stage': 'ephemeral', 'title': 'Ruby Style Guide'}
        )

        results = memory_store.search_fts('Python')
        assert len(results) >= 1
        assert any('Python' in r['title'] for r in results)

    def test_search_fts_by_content(self, memory_store):
        """Test FTS search by content."""
        memory_store.create(
            'test.md',
            'This memory contains information about async programming',
            {'stage': 'ephemeral', 'title': 'Async'}
        )

        results = memory_store.search_fts('async programming')
        assert len(results) >= 1

    def test_search_fts_limit(self, memory_store):
        """Test FTS search respects limit."""
        for i in range(10):
            memory_store.create(
                f'test{i}.md',
                'Common content',
                {'stage': 'ephemeral', 'title': f'Test {i}'}
            )

        results = memory_store.search_fts('Common', limit=5)
        assert len(results) == 5

    def test_search_by_tags(self, memory_store):
        """Test searching by tags."""
        memory_store.create(
            'test1.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test 1', 'tags': ['python', 'async']}
        )
        memory_store.create(
            'test2.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test 2', 'tags': ['python', 'web']}
        )
        memory_store.create(
            'test3.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test 3', 'tags': ['ruby']}
        )

        # Search for single tag
        results = memory_store.search_by_tags(['python'])
        assert len(results) == 2

        # Search for multiple tags (AND logic)
        results = memory_store.search_by_tags(['python', 'async'])
        assert len(results) == 1
        assert results[0]['title'] == 'Test 1'

        # Search for non-matching tags
        results = memory_store.search_by_tags(['nonexistent'])
        assert len(results) == 0


class TestMemoryStoreMetadata:
    """Test metadata and logging operations."""

    def test_record_injection(self, memory_store):
        """Test recording memory injection."""
        memory_id = memory_store.create(
            'test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        memory_store.record_injection(memory_id, 'session-123')

        # Verify memory metadata updated
        result = memory_store.get(memory_id)
        assert result['last_injected_at'] is not None
        assert result['injection_count'] == 1

        # Verify retrieval log
        logs = memory_store.get_retrieval_log()
        assert len(logs) == 1
        assert logs[0]['memory_id'] == memory_id
        assert logs[0]['session_id'] == 'session-123'
        assert logs[0]['retrieval_type'] == 'injected'

    def test_record_injection_stores_project_context(self, memory_store):
        """project_context is stored in retrieval_log for D-08 cross-project tracking."""
        import sqlite3
        memory_id = memory_store.create(
            'test.md',
            'Content',
            {'stage': 'crystallized', 'title': 'Test'}
        )

        memory_store.record_injection(memory_id, 'session-123', project_context='/Users/test/proj-a')

        with sqlite3.connect(memory_store.db_path) as conn:
            row = conn.execute(
                'SELECT project_context FROM retrieval_log WHERE memory_id = ?',
                (memory_id,)
            ).fetchone()
        assert row is not None
        assert row[0] == '/Users/test/proj-a'

    def test_schema_migration_adds_project_context_column(self, tmp_path):
        """Migration guard: calling init_dirs on an old DB without project_context adds the column."""
        import sqlite3
        from core.storage import MemoryStore

        # Create a DB with the old schema (no project_context in retrieval_log)
        old_db = tmp_path / 'memory' / 'index.db'
        old_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(old_db) as conn:
            conn.execute('''
                CREATE TABLE retrieval_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    session_id TEXT,
                    memory_id TEXT,
                    retrieval_type TEXT,
                    was_used INTEGER DEFAULT 0,
                    relevance_score REAL
                )
            ''')

        # Initializing a new MemoryStore pointing to that DB should add the column
        MemoryStore(base_dir=str(tmp_path / 'memory'))

        with sqlite3.connect(old_db) as conn:
            cols = [row[1] for row in conn.execute('PRAGMA table_info(retrieval_log)')]
        assert 'project_context' in cols

    def test_record_usage(self, memory_store):
        """Test recording memory usage."""
        memory_id = memory_store.create(
            'test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        memory_store.record_injection(memory_id, 'session-123')
        memory_store.record_usage(memory_id, 'session-123')

        # Verify memory metadata updated
        result = memory_store.get(memory_id)
        assert result['last_used_at'] is not None
        assert result['usage_count'] == 1

        # Verify retrieval log updated
        logs = memory_store.get_retrieval_log()
        assert logs[0]['was_used'] == 1

    def test_get_session_injections(self, memory_store):
        """get_session_injections returns distinct memory IDs injected in a session."""
        id1 = memory_store.create('a.md', 'A', {'stage': 'consolidated', 'title': 'Memory A'})
        id2 = memory_store.create('b.md', 'B', {'stage': 'consolidated', 'title': 'Memory B'})
        id3 = memory_store.create('c.md', 'C', {'stage': 'consolidated', 'title': 'Memory C'})

        memory_store.record_injection(id1, 'sess-1')
        memory_store.record_injection(id2, 'sess-1')
        memory_store.record_injection(id3, 'sess-2')  # different session

        result = memory_store.get_session_injections('sess-1')
        assert set(result) == {id1, id2}

    def test_get_session_injections_empty(self, memory_store):
        """get_session_injections returns empty list for unknown session."""
        result = memory_store.get_session_injections('nonexistent')
        assert result == []

    def test_log_consolidation(self, memory_store):
        """Test logging consolidation actions."""
        memory_id = memory_store.create(
            'test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        memory_store.log_consolidation(
            action='promoted',
            memory_id=memory_id,
            from_stage='ephemeral',
            to_stage='consolidated',
            rationale='Met threshold for promotion',
            session_id='session-123'
        )

        # Verify log entry
        with sqlite3.connect(memory_store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM consolidation_log WHERE memory_id = ?', (memory_id,))
            row = cursor.fetchone()

            assert row is not None
            assert row['action'] == 'promoted'
            assert row['from_stage'] == 'ephemeral'
            assert row['to_stage'] == 'consolidated'
            assert row['rationale'] == 'Met threshold for promotion'
            assert row['session_id'] == 'session-123'

    def test_log_consolidation_invalid_action(self, memory_store):
        """Test logging with invalid action raises error."""
        with pytest.raises(ValueError, match="Invalid action"):
            memory_store.log_consolidation(
                action='invalid',
                memory_id='id',
                from_stage='ephemeral',
                to_stage='consolidated',
                rationale='test'
            )


class TestMemoryStoreAtomicity:
    """Test atomic operations and error handling."""

    def test_create_rollback_on_db_error(self, memory_store):
        """Test that file is cleaned up if database insert fails."""
        # This is hard to test directly, but we can verify the mechanism exists
        # by checking that duplicate content detection works before file write
        memory_store.create('test1.md', 'content', {'stage': 'ephemeral', 'title': 'Test'})

        with pytest.raises(ValueError, match="Duplicate content"):
            memory_store.create('test2.md', 'content', {'stage': 'ephemeral', 'title': 'Test'})

        # Verify test2.md was not created
        assert not (memory_store.base_dir / 'ephemeral' / 'test2.md').exists()

    def test_update_preserves_content_on_error(self, memory_store):
        """Test that original file is preserved if update fails."""
        memory_id = memory_store.create(
            'test.md',
            'Original',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        # Try to update with invalid stage
        with pytest.raises(ValueError, match="Invalid stage"):
            memory_store.update(memory_id, metadata={'stage': 'invalid'})

        # Verify original content preserved
        result = memory_store.get(memory_id)
        assert 'Original' in result['content']
        assert result['stage'] == 'ephemeral'


class TestMemoryStoreFTS:
    """Test FTS5 sync and triggers."""

    def test_fts_synced_on_create(self, memory_store):
        """Test FTS index is updated on create."""
        memory_id = memory_store.create(
            'test.md',
            'Unique search term xyz123',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        results = memory_store.search_fts('xyz123')
        assert len(results) == 1
        assert results[0]['id'] == memory_id

    def test_fts_synced_on_update(self, memory_store):
        """Test FTS index is updated on content change."""
        memory_id = memory_store.create(
            'test.md',
            'Original content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        memory_store.update(memory_id, content='Updated with newterm456')

        results = memory_store.search_fts('newterm456')
        assert len(results) == 1

        # Old content should not be found
        results = memory_store.search_fts('Original')
        assert len(results) == 0

    def test_fts_synced_on_delete(self, memory_store):
        """Test FTS index is cleaned up on delete."""
        memory_id = memory_store.create(
            'test.md',
            'Content to delete abc789',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        # Verify it's searchable
        results = memory_store.search_fts('abc789')
        assert len(results) == 1

        # Delete and verify it's gone from search
        memory_store.delete(memory_id)
        results = memory_store.search_fts('abc789')
        assert len(results) == 0


class TestSanitizeFtsTerm:
    """Test FTS5 term sanitization."""

    def test_plain_term(self):
        assert MemoryStore.sanitize_fts_term("hello") == '"hello"'

    def test_fts_operator_and(self):
        assert MemoryStore.sanitize_fts_term("AND") == '"AND"'

    def test_fts_operator_not(self):
        assert MemoryStore.sanitize_fts_term("NOT") == '"NOT"'

    def test_fts_operator_near(self):
        assert MemoryStore.sanitize_fts_term("NEAR") == '"NEAR"'

    def test_wildcard_star(self):
        assert MemoryStore.sanitize_fts_term("test*") == '"test*"'

    def test_column_filter(self):
        assert MemoryStore.sanitize_fts_term("title:hack") == '"title:hack"'

    def test_internal_double_quotes(self):
        assert MemoryStore.sanitize_fts_term('say "hello"') == '"say ""hello"""'

    def test_empty_string(self):
        assert MemoryStore.sanitize_fts_term("") == '""'

    def test_sanitized_query_executes(self, memory_store):
        """Sanitized FTS operators don't crash search_fts."""
        memory_store.create(
            path="test.md",
            content="Test content about AND operators",
            metadata={"stage": "consolidated", "title": "Test", "summary": "test"},
        )
        # These would crash or misbehave without sanitization
        for dangerous_term in ["AND", "NOT", "NEAR", "*", "OR"]:
            query = MemoryStore.sanitize_fts_term(dangerous_term)
            # Should not raise
            memory_store.search_fts(query, limit=5)


class TestMemoryStoreContentHash:
    """Test content hash deduplication."""

    def test_content_hash_computed(self, memory_store):
        """Test content hash is computed and stored."""
        memory_id = memory_store.create(
            'test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        result = memory_store.get(memory_id)
        assert result['content_hash'] is not None
        assert len(result['content_hash']) == 32  # MD5 hex length

    def test_content_hash_prevents_duplicates(self, memory_store):
        """Test identical content is rejected."""
        content = "Exact duplicate content"
        metadata = {'stage': 'ephemeral', 'title': 'Test'}

        memory_store.create('test1.md', content, metadata)

        with pytest.raises(ValueError, match="Duplicate content"):
            memory_store.create('test2.md', content, metadata)

    def test_content_hash_updated_on_change(self, memory_store):
        """Test content hash changes when content is updated."""
        memory_id = memory_store.create(
            'test.md',
            'Original',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        original_hash = memory_store.get(memory_id)['content_hash']

        memory_store.update(memory_id, content='Updated')

        new_hash = memory_store.get(memory_id)['content_hash']
        assert new_hash != original_hash


class TestMemoryStoreProjectContext:
    """Test project-specific storage."""

    def test_project_context_stored(self, project_memory_store):
        """Test project context is stored with memory."""
        memory_id = project_memory_store.create(
            'test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Test'}
        )

        result = project_memory_store.get(memory_id)
        assert result['project_context'] == '/Users/test/my-project'

    def test_project_path_hashing(self, temp_dir, monkeypatch):
        """Test project path is converted to safe directory name."""
        monkeypatch.setenv('HOME', str(temp_dir))

        store = MemoryStore(project_context='/Users/test/my-project')
        expected = temp_dir / '.claude' / 'projects' / '-Users-test-my-project' / 'memory'
        assert store.base_dir == expected


def _make_embedding(dims: int = 512) -> bytes:
    """Create a float32 embedding of the given dimensionality."""
    floats = [float(i % 100) / 100.0 for i in range(dims)]
    return struct.pack(f"{dims}f", *floats)


def _make_store_with_vec(temp_dir):
    """
    Create a MemoryStore with _vec_available forced True by patching _load_vec
    to be a no-op and re-creating the vec_memories virtual table using a
    real sqlite-vec connection (if the extension can load), or via a minimal
    SQLite virtual table mock.

    On platforms where enable_load_extension is disabled we patch _load_vec
    as a no-op and manually create the virtual table, so the SQL logic can
    be exercised end-to-end.
    """
    import core.storage as storage_mod

    store = MemoryStore(base_dir=str(temp_dir))

    if store._vec_available:
        # sqlite-vec actually loaded — use it directly
        return store

    # sqlite-vec not available on this platform — simulate by patching
    # _load_vec and manually setting up the virtual table via a real
    # sqlite-vec connection (using the loadable extension path directly if
    # possible, otherwise we skip).
    try:
        import sqlite_vec
        import sqlite3 as _sq3

        # Attempt to get a connection with load_extension support
        # This may fail on macOS where the stdlib module is compiled without it
        test_conn = _sq3.connect(":memory:")
        test_conn.enable_load_extension(True)  # raises AttributeError if unsupported
        sqlite_vec.load(test_conn)
        test_conn.close()
    except (AttributeError, Exception):
        # Cannot actually run sqlite-vec on this platform — skip vec table
        return None

    return store


class TestVecUnavailableFallback:
    """
    Verify all three vector methods return graceful empty/None values when
    _vec_available is False (sqlite-vec not loaded or extension support absent).
    """

    def test_store_embedding_no_op_when_unavailable(self, memory_store):
        """store_embedding returns without error when vec is unavailable."""
        memory_store._vec_available = False
        memory_id = memory_store.create(
            'vec_test.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Vec Test'},
        )
        # Should not raise
        memory_store.store_embedding(memory_id, _make_embedding())

    def test_search_vector_returns_empty_when_unavailable(self, memory_store):
        """search_vector returns [] when vec is unavailable."""
        memory_store._vec_available = False
        result = memory_store.search_vector(_make_embedding())
        assert result == []

    def test_search_vector_returns_empty_when_query_is_none(self, memory_store):
        """search_vector returns [] when query_embedding is None."""
        memory_store._vec_available = False
        result = memory_store.search_vector(None)
        assert result == []

    def test_get_embedding_returns_none_when_unavailable(self, memory_store):
        """get_embedding returns None when vec is unavailable."""
        memory_store._vec_available = False
        memory_id = memory_store.create(
            'vec_test2.md',
            'Content',
            {'stage': 'ephemeral', 'title': 'Vec Test 2'},
        )
        result = memory_store.get_embedding(memory_id)
        assert result is None

    def test_vec_available_attribute_exists(self, memory_store):
        """_vec_available attribute is always set after __init__."""
        assert hasattr(memory_store, '_vec_available')
        assert isinstance(memory_store._vec_available, bool)

    def test_search_vector_with_none_query_when_vec_available_returns_empty(self, memory_store):
        """search_vector returns [] for None query even if vec is nominally available."""
        memory_store._vec_available = True  # force True
        result = memory_store.search_vector(None)
        assert result == []


class TestVecEnabled:
    """
    Tests for when sqlite-vec is actually available. These tests use monkeypatching
    to inject a real sqlite-vec connection, or are skipped when sqlite-vec cannot
    load extensions on the current platform.
    """

    @pytest.fixture
    def vec_store(self, temp_dir, monkeypatch):
        """
        Yield a MemoryStore with a real working vec_memories table.

        Patches _load_vec to be a no-op (we pre-create the virtual table using
        a direct call) and sets _vec_available = True.

        Skips if sqlite-vec extension loading is unsupported on this platform.
        """
        import sqlite3 as _sq3

        # Confirm sqlite_vec is importable
        try:
            import sqlite_vec as _vec
        except ImportError:
            pytest.skip("sqlite_vec not installed")

        # Check that enable_load_extension is available
        test_conn = _sq3.connect(":memory:")
        if not hasattr(test_conn, 'enable_load_extension'):
            pytest.skip("sqlite3 compiled without extension loading support on this platform")
        test_conn.close()

        store = MemoryStore(base_dir=str(temp_dir))
        assert store._vec_available, "Expected _vec_available=True when extension is loadable"
        yield store
        store.close()

    def test_vec_available_true_when_extension_loads(self, vec_store):
        """_vec_available is True when sqlite-vec loaded successfully."""
        assert vec_store._vec_available is True

    def test_vec_memories_table_created(self, vec_store):
        """vec_memories virtual table exists in the database."""
        with sqlite3.connect(vec_store.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' OR type='shadow'"
                ).fetchall()
            }
        # vec0 tables show up under various names; check the virtual table exists
        with sqlite3.connect(vec_store.db_path) as conn:
            result = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='vec_memories'"
            ).fetchone()
        assert result is not None, "vec_memories table not found in schema"

    def test_store_and_get_embedding_roundtrip(self, vec_store):
        """store_embedding stores bytes that get_embedding returns unchanged."""
        memory_id = vec_store.create(
            'emb_test.md',
            'Embedding roundtrip content',
            {'stage': 'ephemeral', 'title': 'Emb Test'},
        )
        emb = _make_embedding(512)
        vec_store.store_embedding(memory_id, emb)

        retrieved = vec_store.get_embedding(memory_id)
        assert retrieved == emb

    def test_store_embedding_nonexistent_memory_is_silent(self, vec_store):
        """store_embedding on a missing memory_id returns without error."""
        vec_store.store_embedding('00000000-0000-0000-0000-000000000000', _make_embedding())

    def test_get_embedding_nonexistent_memory_returns_none(self, vec_store):
        """get_embedding on an unrecognised memory_id returns None."""
        result = vec_store.get_embedding('00000000-0000-0000-0000-000000000000')
        assert result is None

    def test_get_embedding_no_embedding_stored_returns_none(self, vec_store):
        """get_embedding returns None when no embedding has been stored yet."""
        memory_id = vec_store.create(
            'no_emb.md',
            'No embedding yet',
            {'stage': 'ephemeral', 'title': 'No Emb'},
        )
        result = vec_store.get_embedding(memory_id)
        assert result is None

    def test_store_embedding_overwrite(self, vec_store):
        """Calling store_embedding twice replaces the previous value."""
        memory_id = vec_store.create(
            'overwrite.md',
            'Overwrite test',
            {'stage': 'ephemeral', 'title': 'Overwrite'},
        )
        emb1 = _make_embedding(512)
        emb2 = struct.pack("512f", *([0.99] * 512))

        vec_store.store_embedding(memory_id, emb1)
        vec_store.store_embedding(memory_id, emb2)

        retrieved = vec_store.get_embedding(memory_id)
        assert retrieved == emb2

    def test_search_vector_returns_list(self, vec_store):
        """search_vector returns a list (possibly empty) without raising."""
        memory_id = vec_store.create(
            'search_test.md',
            'Search test content',
            {'stage': 'ephemeral', 'title': 'Search Test'},
        )
        emb = _make_embedding(512)
        vec_store.store_embedding(memory_id, emb)

        results = vec_store.search_vector(emb, k=5)
        assert isinstance(results, list)

    def test_search_vector_result_has_distance_key(self, vec_store):
        """Results from search_vector include a 'distance' key."""
        memory_id = vec_store.create(
            'dist_test.md',
            'Distance key test',
            {'stage': 'ephemeral', 'title': 'Dist Test'},
        )
        emb = _make_embedding(512)
        vec_store.store_embedding(memory_id, emb)

        results = vec_store.search_vector(emb, k=5)
        assert len(results) >= 1
        assert 'distance' in results[0]

    def test_search_vector_exclude_ids(self, vec_store):
        """search_vector excludes results whose id is in exclude_ids."""
        id1 = vec_store.create(
            'excl1.md',
            'Exclude test 1',
            {'stage': 'ephemeral', 'title': 'Excl 1'},
        )
        id2 = vec_store.create(
            'excl2.md',
            'Exclude test 2',
            {'stage': 'ephemeral', 'title': 'Excl 2'},
        )
        emb = _make_embedding(512)
        vec_store.store_embedding(id1, emb)
        vec_store.store_embedding(id2, emb)

        results = vec_store.search_vector(emb, k=10, exclude_ids={id1})
        ids = {r['id'] for r in results}
        assert id1 not in ids
        assert id2 in ids
