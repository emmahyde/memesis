"""Tests for the Peewee model layer (replaces old storage layer tests)."""

import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, get_base_dir, get_db_path, get_vec_store
from core.models import Memory, ConsolidationLog, RetrievalLog, db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """Initialize database in a temp directory and return base_dir."""
    base_dir = init_db(base_dir=str(tmp_path / "memory"))
    yield base_dir
    close_db()


@pytest.fixture
def project_store(tmp_path, monkeypatch):
    """Initialize database with project context."""
    monkeypatch.setenv('HOME', str(tmp_path))
    base_dir = init_db(project_context='/Users/test/my-project')
    yield base_dir
    close_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_memory(stage='ephemeral', title='Test', content='Content', tags=None, **kwargs):
    """Create a memory and return it."""
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage=stage,
        title=title,
        summary=kwargs.get('summary', f'Summary of {title}'),
        content=content,
        tags=json.dumps(tags or []),
        importance=kwargs.get('importance', 0.5),
        created_at=now,
        updated_at=now,
        reinforcement_count=kwargs.get('reinforcement_count', 0),
        source_session=kwargs.get('source_session'),
    )
    return mem


class TestDatabaseInit:
    """Test database initialization."""

    def test_default_base_dir(self, tmp_path, monkeypatch):
        """Test default base directory is ~/.claude/memory."""
        monkeypatch.setenv('HOME', str(tmp_path))
        base_dir = init_db()
        try:
            assert base_dir == tmp_path / '.claude' / 'memory'
        finally:
            close_db()

    def test_explicit_base_dir(self, tmp_path):
        """Test explicit base directory."""
        base_dir = init_db(base_dir=str(tmp_path / 'custom'))
        try:
            assert base_dir == tmp_path / 'custom'
        finally:
            close_db()

    def test_project_context(self, tmp_path, monkeypatch):
        """Test project-specific storage."""
        monkeypatch.setenv('HOME', str(tmp_path))
        base_dir = init_db(project_context='/Users/test/my-project')
        try:
            expected = tmp_path / '.claude' / 'projects' / '-Users-test-my-project' / 'memory'
            assert base_dir == expected
        finally:
            close_db()

    def test_init_dirs_creates_base(self, store):
        """Test base directory creation (stage dirs no longer created)."""
        base_dir = store
        assert base_dir.exists()
        assert (base_dir / 'index.db').exists()

    def test_database_initialized(self, store):
        """Test SQLite database is initialized with correct schema."""
        db_path = get_db_path()
        assert db_path.exists()

        cursor = db.execute_sql("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert 'memories' in tables
        assert 'memories_fts' in tables
        assert 'retrieval_log' in tables
        assert 'consolidation_log' in tables

        cursor = db.execute_sql('PRAGMA journal_mode')
        assert cursor.fetchone()[0] == 'wal'


class TestMemoryCRUD:
    """Test CRUD operations."""

    def test_create_memory(self, store):
        """Test creating a new memory."""
        mem = _create_memory(
            stage='ephemeral',
            title='Test Memory',
            summary='A test memory',
            content='Test memory content',
            tags=['test', 'python'],
            importance=0.7,
        )

        assert len(mem.id) == 36
        assert mem.id.count('-') == 4

        retrieved = Memory.get_by_id(mem.id)
        assert retrieved.stage == 'ephemeral'
        assert retrieved.title == 'Test Memory'
        assert retrieved.summary == 'A test memory'
        assert retrieved.tag_list == ['test', 'python']
        assert retrieved.importance == 0.7
        assert retrieved.content_hash is not None

    def test_get_memory(self, store):
        """Test retrieving a memory."""
        mem = _create_memory(
            stage='ephemeral',
            title='Test',
            summary='Summary',
            content='Test content',
            tags=['tag1'],
        )

        result = Memory.get_by_id(mem.id)
        assert result.id == mem.id
        assert result.stage == 'ephemeral'
        assert result.title == 'Test'
        assert result.summary == 'Summary'
        assert result.tag_list == ['tag1']
        assert 'Test content' in result.content

    def test_get_nonexistent_memory(self, store):
        """Test getting nonexistent memory raises DoesNotExist."""
        with pytest.raises(Memory.DoesNotExist):
            Memory.get_by_id('00000000-0000-0000-0000-000000000000')

    def test_update_memory_content(self, store):
        """Test updating memory content."""
        mem = _create_memory(content='Original content')
        mem.content = 'Updated content'
        mem.save()

        result = Memory.get_by_id(mem.id)
        assert 'Updated content' in result.content

    def test_update_memory_metadata(self, store):
        """Test updating memory metadata."""
        mem = _create_memory(title='Original', importance=0.5)
        mem.title = 'Updated'
        mem.importance = 0.8
        mem.save()

        result = Memory.get_by_id(mem.id)
        assert result.title == 'Updated'
        assert result.importance == 0.8

    def test_update_memory_stage(self, store):
        """Test updating memory stage."""
        mem = _create_memory(stage='ephemeral')
        mem.stage = 'consolidated'
        mem.save()

        result = Memory.get_by_id(mem.id)
        assert result.stage == 'consolidated'

    def test_delete_memory(self, store):
        """Test deleting a memory."""
        mem = _create_memory()
        mem_id = mem.id
        mem.delete_instance()

        with pytest.raises(Memory.DoesNotExist):
            Memory.get_by_id(mem_id)

    def test_list_by_stage(self, store):
        """Test listing memories by stage."""
        m1 = _create_memory(stage='ephemeral', title='Test 1', content='Content 1')
        m2 = _create_memory(stage='ephemeral', title='Test 2', content='Content 2')
        m3 = _create_memory(stage='consolidated', title='Test 3', content='Content 3')

        ephemeral = list(Memory.by_stage('ephemeral'))
        assert len(ephemeral) == 2
        assert {m.id for m in ephemeral} == {m1.id, m2.id}

        consolidated = list(Memory.by_stage('consolidated'))
        assert len(consolidated) == 1
        assert consolidated[0].id == m3.id


class TestMemorySearch:
    """Test search operations."""

    def test_search_fts_by_title(self, store):
        """Test FTS search by title."""
        _create_memory(title='Python Best Practices', content='Python tips')
        _create_memory(title='Ruby Style Guide', content='Ruby tips')

        results = Memory.search_fts('Python')
        assert len(results) >= 1
        assert any('Python' in r.title for r in results)

    def test_search_fts_by_content(self, store):
        """Test FTS search by content."""
        _create_memory(
            title='Async',
            content='This memory contains information about async programming',
        )
        results = Memory.search_fts('"async programming"')
        assert len(results) >= 1

    def test_search_fts_limit(self, store):
        """Test FTS search respects limit."""
        for i in range(10):
            _create_memory(title=f'Test {i}', content=f'Common content item {i}')

        results = Memory.search_fts('"Common content"', limit=5)
        assert len(results) == 5


class TestMetadataAndLogging:
    """Test metadata and logging operations."""

    def test_record_injection(self, store):
        """Test recording memory injection."""
        mem = _create_memory()
        now = datetime.now().isoformat()

        Memory.update(
            last_injected_at=now,
            injection_count=Memory.injection_count + 1,
        ).where(Memory.id == mem.id).execute()

        RetrievalLog.create(
            timestamp=now,
            session_id='session-123',
            memory_id=mem.id,
            retrieval_type='injected',
        )

        result = Memory.get_by_id(mem.id)
        assert result.last_injected_at is not None
        assert result.injection_count == 1

        logs = list(RetrievalLog.select().where(RetrievalLog.memory_id == mem.id))
        assert len(logs) == 1
        assert logs[0].session_id == 'session-123'
        assert logs[0].retrieval_type == 'injected'

    def test_record_injection_stores_project_context(self, store):
        """project_context is stored in retrieval_log."""
        mem = _create_memory(stage='crystallized')
        now = datetime.now().isoformat()

        RetrievalLog.create(
            timestamp=now,
            session_id='session-123',
            memory_id=mem.id,
            retrieval_type='injected',
            project_context='/Users/test/proj-a',
        )

        log = RetrievalLog.get(RetrievalLog.memory_id == mem.id)
        assert log.project_context == '/Users/test/proj-a'

    def test_log_consolidation(self, store):
        """Test logging consolidation actions."""
        mem = _create_memory()

        ConsolidationLog.create(
            timestamp=datetime.now().isoformat(),
            action='promoted',
            memory_id=mem.id,
            from_stage='ephemeral',
            to_stage='consolidated',
            rationale='Met threshold for promotion',
            session_id='session-123',
        )

        log = ConsolidationLog.get(ConsolidationLog.memory_id == mem.id)
        assert log.action == 'promoted'
        assert log.from_stage == 'ephemeral'
        assert log.to_stage == 'consolidated'
        assert log.rationale == 'Met threshold for promotion'
        assert log.session_id == 'session-123'


class TestFTS:
    """Test FTS5 sync."""

    def test_fts_synced_on_create(self, store):
        mem = _create_memory(content='Unique search term xyz123')
        results = Memory.search_fts('xyz123')
        assert len(results) == 1
        assert results[0].id == mem.id

    def test_fts_synced_on_update(self, store):
        mem = _create_memory(content='Original content')
        mem.content = 'Updated with newterm456'
        mem.save()

        results = Memory.search_fts('newterm456')
        assert len(results) == 1

        results = Memory.search_fts('"Original content"')
        assert len(results) == 0

    def test_fts_synced_on_delete(self, store):
        mem = _create_memory(content='Content to delete abc789')
        results = Memory.search_fts('abc789')
        assert len(results) == 1

        mem.delete_instance()
        results = Memory.search_fts('abc789')
        assert len(results) == 0


class TestSanitizeFtsTerm:
    """Test FTS5 term sanitization."""

    def test_plain_term(self):
        assert Memory.sanitize_fts_term("hello") == '"hello"'

    def test_fts_operator_and(self):
        assert Memory.sanitize_fts_term("AND") == '"AND"'

    def test_fts_operator_not(self):
        assert Memory.sanitize_fts_term("NOT") == '"NOT"'

    def test_fts_operator_near(self):
        assert Memory.sanitize_fts_term("NEAR") == '"NEAR"'

    def test_wildcard_star(self):
        assert Memory.sanitize_fts_term("test*") == '"test*"'

    def test_column_filter(self):
        assert Memory.sanitize_fts_term("title:hack") == '"title:hack"'

    def test_internal_double_quotes(self):
        assert Memory.sanitize_fts_term('say "hello"') == '"say ""hello"""'

    def test_empty_string(self):
        assert Memory.sanitize_fts_term("") == '""'

    def test_sanitized_query_executes(self, store):
        """Sanitized FTS operators don't crash search_fts."""
        _create_memory(
            stage='consolidated',
            title='Test',
            summary='test',
            content='Test content about AND operators',
        )
        for dangerous_term in ["AND", "NOT", "NEAR", "*", "OR"]:
            query = Memory.sanitize_fts_term(dangerous_term)
            Memory.search_fts(query, limit=5)


class TestContentHash:
    """Test content hash deduplication."""

    def test_content_hash_computed(self, store):
        mem = _create_memory()
        result = Memory.get_by_id(mem.id)
        assert result.content_hash is not None
        assert len(result.content_hash) == 32

    def test_content_hash_updated_on_change(self, store):
        mem = _create_memory(content='Original')
        original_hash = Memory.get_by_id(mem.id).content_hash

        mem.content = 'Updated'
        mem.save()

        new_hash = Memory.get_by_id(mem.id).content_hash
        assert new_hash != original_hash


# ---------------------------------------------------------------------------
# W2 schema additions
# ---------------------------------------------------------------------------


class TestW2SchemaFields:
    """Round-trip tests for Wave 2 Memory schema additions."""

    def test_all_new_fields_persist(self, store):
        """All W2 fields can be written and read back."""
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="ephemeral",
            title="W2 test",
            content="content",
            created_at=now,
            updated_at=now,
            kind="decision",
            knowledge_type="conceptual",
            knowledge_type_confidence="high",
            subject="system",
            work_event="feature",
            subtitle="Short retrieval subtitle here",
            cwd="/Users/test/myproject",
            session_type="code",
            raw_importance=0.7,
            linked_observation_ids=json.dumps(["abc-123", "def-456"]),
            access_count=3,
        )
        result = Memory.get_by_id(mem.id)
        assert result.kind == "decision"
        assert result.knowledge_type == "conceptual"
        assert result.knowledge_type_confidence == "high"
        assert result.subject == "system"
        assert result.work_event == "feature"
        assert result.subtitle == "Short retrieval subtitle here"
        assert result.cwd == "/Users/test/myproject"
        assert result.session_type == "code"
        assert result.raw_importance == pytest.approx(0.7)
        assert result.access_count == 3

    def test_linked_observations_accessor_parses_json(self, store):
        """linked_observations property returns list from JSON text."""
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="ephemeral",
            title="linked test",
            content="c",
            created_at=now,
            updated_at=now,
            linked_observation_ids=json.dumps(["id-1", "id-2", "id-3"]),
        )
        result = Memory.get_by_id(mem.id)
        assert result.linked_observations == ["id-1", "id-2", "id-3"]

    def test_linked_observations_accessor_returns_empty_for_null(self, store):
        """linked_observations returns [] when field is null."""
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="ephemeral",
            title="null linked",
            content="c",
            created_at=now,
            updated_at=now,
            linked_observation_ids=None,
        )
        result = Memory.get_by_id(mem.id)
        assert result.linked_observations == []

    def test_linked_observations_accessor_returns_empty_for_missing(self, store):
        """linked_observations returns [] when field is empty string."""
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="ephemeral",
            title="empty linked",
            content="c",
            created_at=now,
            updated_at=now,
            linked_observation_ids="",
        )
        result = Memory.get_by_id(mem.id)
        assert result.linked_observations == []

    def test_session_type_accepts_valid_values(self, store):
        """session_type stores code|writing|research values."""
        now = datetime.now().isoformat()
        for val in ("code", "writing", "research"):
            mem = Memory.create(
                stage="ephemeral",
                title=f"session_type {val}",
                content="c",
                created_at=now,
                updated_at=now,
                session_type=val,
            )
            result = Memory.get_by_id(mem.id)
            assert result.session_type == val

    def test_session_type_accepts_null(self, store):
        """session_type can be null."""
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="ephemeral",
            title="no session type",
            content="c",
            created_at=now,
            updated_at=now,
            session_type=None,
        )
        result = Memory.get_by_id(mem.id)
        assert result.session_type is None

    def test_w2_created_at_default_is_set(self, store):
        """w2_created_at gets a default value on creation."""
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="ephemeral",
            title="created_at test",
            content="c",
            created_at=now,
            updated_at=now,
        )
        result = Memory.get_by_id(mem.id)
        # w2_created_at should be set (not null)
        assert result.w2_created_at is not None

    def test_access_count_defaults_to_zero(self, store):
        """access_count defaults to 0."""
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="ephemeral",
            title="access_count default",
            content="c",
            created_at=now,
            updated_at=now,
        )
        result = Memory.get_by_id(mem.id)
        assert result.access_count == 0

    def test_raw_importance_null_by_default(self, store):
        """raw_importance is null when not set."""
        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="ephemeral",
            title="raw_importance null",
            content="c",
            created_at=now,
            updated_at=now,
        )
        result = Memory.get_by_id(mem.id)
        assert result.raw_importance is None


class TestProjectContext:
    """Test project-specific storage."""

    def test_project_path_hashing(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        base_dir = init_db(project_context='/Users/test/my-project')
        try:
            expected = tmp_path / '.claude' / 'projects' / '-Users-test-my-project' / 'memory'
            assert base_dir == expected
        finally:
            close_db()


def _make_embedding(dims: int = 512) -> bytes:
    floats = [float(i % 100) / 100.0 for i in range(dims)]
    return struct.pack(f"{dims}f", *floats)


class TestVecUnavailableFallback:
    """Verify VecStore is accessible after init_db."""

    def test_vec_store_accessible(self, store):
        vec = get_vec_store()
        assert vec is not None
        assert isinstance(vec.available, bool)


class TestVecEnabled:
    """Tests for when sqlite-vec is actually available."""

    @pytest.fixture
    def vec(self, tmp_path):
        import sqlite3 as _sq3
        try:
            import sqlite_vec as _vec
        except ImportError:
            pytest.skip("sqlite_vec not installed")

        test_conn = _sq3.connect(":memory:")
        if not hasattr(test_conn, 'enable_load_extension'):
            pytest.skip("sqlite3 compiled without extension loading support")
        test_conn.close()

        base_dir = init_db(base_dir=str(tmp_path / "memory"))
        v = get_vec_store()
        if not v or not v.available:
            close_db()
            pytest.skip("VecStore not available")
        yield v
        close_db()

    def test_vec_available_true_when_extension_loads(self, vec):
        assert vec.available is True

    def test_store_and_get_embedding_roundtrip(self, vec):
        mem = _create_memory(content='Embedding roundtrip content')
        emb = _make_embedding(512)
        vec.store_embedding(mem.id, emb)
        retrieved = vec.get_embedding(mem.id)
        assert retrieved == emb

    def test_store_embedding_nonexistent_memory_is_silent(self, vec):
        vec.store_embedding('00000000-0000-0000-0000-000000000000', _make_embedding())

    def test_get_embedding_nonexistent_memory_returns_none(self, vec):
        result = vec.get_embedding('00000000-0000-0000-0000-000000000000')
        assert result is None

    def test_store_embedding_overwrite(self, vec):
        mem = _create_memory(content='Overwrite test')
        emb1 = _make_embedding(512)
        emb2 = struct.pack("512f", *([0.99] * 512))
        vec.store_embedding(mem.id, emb1)
        vec.store_embedding(mem.id, emb2)
        retrieved = vec.get_embedding(mem.id)
        assert retrieved == emb2

    def test_search_vector_returns_list(self, vec):
        mem = _create_memory(content='Search test content')
        emb = _make_embedding(512)
        vec.store_embedding(mem.id, emb)
        results = vec.search_vector(emb, k=5)
        assert isinstance(results, list)

    def test_search_vector_result_has_distance_key(self, vec):
        mem = _create_memory(content='Distance key test')
        emb = _make_embedding(512)
        vec.store_embedding(mem.id, emb)
        results = vec.search_vector(emb, k=5)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# WS-H: open_question lifecycle fields (Sprint B DS-F9)
# ---------------------------------------------------------------------------


class TestQuestionLifecycleFields:
    """Round-trip tests for resolves_question_id, resolved_at, is_pinned."""

    def test_is_pinned_defaults_false(self, store):
        mem = _create_memory(kind='open_question')
        fresh = Memory.get_by_id(mem.id)
        # SQLite stores as 0; treat 0 / None / False as falsy
        assert not fresh.is_pinned

    def test_is_pinned_persists_true(self, store):
        mem = _create_memory(kind='open_question')
        Memory.update(is_pinned=1).where(Memory.id == mem.id).execute()
        fresh = Memory.get_by_id(mem.id)
        assert fresh.is_pinned == 1

    def test_resolved_at_defaults_null(self, store):
        mem = _create_memory(kind='open_question')
        fresh = Memory.get_by_id(mem.id)
        assert fresh.resolved_at is None

    def test_resolved_at_accepts_datetime(self, store):
        mem = _create_memory(kind='open_question')
        now = datetime.now(timezone.utc)
        Memory.update(resolved_at=now).where(Memory.id == mem.id).execute()
        fresh = Memory.get_by_id(mem.id)
        assert fresh.resolved_at is not None

    def test_resolved_at_round_trips_none(self, store):
        mem = _create_memory(kind='open_question')
        now = datetime.now(timezone.utc)
        Memory.update(resolved_at=now).where(Memory.id == mem.id).execute()
        Memory.update(resolved_at=None).where(Memory.id == mem.id).execute()
        fresh = Memory.get_by_id(mem.id)
        assert fresh.resolved_at is None

    def test_resolves_question_id_defaults_null(self, store):
        mem = _create_memory(kind='correction')
        fresh = Memory.get_by_id(mem.id)
        assert fresh.resolves_question_id is None

    def test_resolves_question_id_persists(self, store):
        question = _create_memory(kind='open_question')
        resolver = _create_memory(kind='correction')
        Memory.update(resolves_question_id=str(question.id)).where(
            Memory.id == resolver.id
        ).execute()
        fresh = Memory.get_by_id(resolver.id)
        assert fresh.resolves_question_id == str(question.id)

    def test_all_three_fields_in_single_row(self, store):
        now = datetime.now(timezone.utc)
        question = _create_memory(kind='open_question')
        resolver = _create_memory(kind='correction')
        Memory.update(resolved_at=now, is_pinned=1).where(
            Memory.id == question.id
        ).execute()
        Memory.update(resolves_question_id=str(question.id)).where(
            Memory.id == resolver.id
        ).execute()
        q_fresh = Memory.get_by_id(question.id)
        r_fresh = Memory.get_by_id(resolver.id)
        assert q_fresh.resolved_at is not None
        assert q_fresh.is_pinned == 1
        assert r_fresh.resolves_question_id == str(question.id)
