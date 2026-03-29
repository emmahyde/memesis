"""
Storage layer for memory lifecycle with dual-write atomicity.

Implements markdown CRUD with SQLite FTS5 index for efficient search
while preserving human-readable memory format.
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import sqlite_vec
    _SQLITE_VEC_AVAILABLE = True
except ImportError:
    _SQLITE_VEC_AVAILABLE = False

logger = logging.getLogger(__name__)


class MemoryStore:
    """
    Memory storage with dual-write atomicity: markdown + SQLite FTS5.

    Directory structure:
        {base_dir}/
            ephemeral/
            consolidated/
            crystallized/
            instinctive/
            meta/
            archived/
            index.db
    """

    STAGES = ['ephemeral', 'consolidated', 'crystallized', 'instinctive']

    def __init__(self, base_dir: str = None, project_context: str = None):
        """
        Initialize memory store.

        Args:
            base_dir: Storage root. If None, uses ~/.claude/memory
            project_context: Project path. If provided, stores in project-specific location
        """
        if project_context:
            path_hash = self._hash_project_path(project_context)
            self.base_dir = Path.home() / '.claude' / 'projects' / path_hash / 'memory'
            self.project_context = project_context
        elif base_dir:
            self.base_dir = Path(base_dir).expanduser()
            self.project_context = None
        else:
            self.base_dir = Path.home() / '.claude' / 'memory'
            self.project_context = None

        self.db_path = self.base_dir / 'index.db'
        self._init_db()

    def close(self) -> None:
        """
        Checkpoint the WAL log and release file descriptors.

        Call this when a MemoryStore is no longer needed (especially in tests)
        to collapse WAL/SHM files and avoid EMFILE exhaustion under load.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass  # DB may already be cleaned up

    def __del__(self) -> None:
        """Best-effort WAL cleanup on garbage collection."""
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _hash_project_path(path: str) -> str:
        """Convert project path to safe directory name, matching Claude Code's convention."""
        import re
        return re.sub(r'[^a-zA-Z0-9-]', '-', path)

    def init_dirs(self) -> None:
        """Initialize directory structure."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

        for stage in self.STAGES:
            (self.base_dir / stage).mkdir(exist_ok=True)

        (self.base_dir / 'meta').mkdir(exist_ok=True)
        (self.base_dir / 'archived').mkdir(exist_ok=True)

    def _init_db(self) -> None:
        """Initialize SQLite database with schema and WAL mode."""
        self._vec_available = False
        self.init_dirs()

        with sqlite3.connect(self.db_path) as conn:
            # Enable WAL mode for concurrent read safety
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA busy_timeout=5000')

            # Main memories table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    stage TEXT NOT NULL CHECK(stage IN ('ephemeral', 'consolidated', 'crystallized', 'instinctive')),
                    title TEXT,
                    summary TEXT,
                    tags TEXT,
                    importance REAL DEFAULT 0.5 CHECK(importance BETWEEN 0.0 AND 1.0),
                    reinforcement_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_injected_at TEXT,
                    last_used_at TEXT,
                    injection_count INTEGER DEFAULT 0,
                    usage_count INTEGER DEFAULT 0,
                    project_context TEXT,
                    source_session TEXT,
                    content_hash TEXT
                )
            ''')

            # FTS5 virtual table
            conn.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    title, summary, tags, content,
                    content='memories',
                    content_rowid='rowid'
                )
            ''')

            # Retrieval log
            conn.execute('''
                CREATE TABLE IF NOT EXISTS retrieval_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    session_id TEXT,
                    memory_id TEXT,
                    retrieval_type TEXT CHECK(retrieval_type IN ('injected', 'active_search', 'user_prompted')),
                    was_used INTEGER DEFAULT 0,
                    relevance_score REAL,
                    project_context TEXT
                )
            ''')
            # Migration: add project_context if upgrading from earlier schema
            try:
                conn.execute("ALTER TABLE retrieval_log ADD COLUMN project_context TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Migration: add archived_at for reversible archival
            try:
                conn.execute("ALTER TABLE memories ADD COLUMN archived_at TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Migration: add subsumed_by for inhibition (retrieval-induced
            # forgetting).  When a memory is crystallized into a higher-level
            # insight, the source memories are marked with the crystallized
            # memory's ID.  The relevance engine skips subsumed memories
            # during rehydration — they should not compete with their parent.
            try:
                conn.execute("ALTER TABLE memories ADD COLUMN subsumed_by TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Consolidation log
            conn.execute('''
                CREATE TABLE IF NOT EXISTS consolidation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    session_id TEXT,
                    action TEXT CHECK(action IN ('kept', 'pruned', 'promoted', 'demoted', 'merged', 'deprecated', 'subsumed')),
                    memory_id TEXT,
                    from_stage TEXT,
                    to_stage TEXT,
                    rationale TEXT
                )
            ''')

            # Narrative threads — ordered sequences of related memories
            conn.execute('''
                CREATE TABLE IF NOT EXISTS narrative_threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT,
                    narrative TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS thread_members (
                    thread_id TEXT NOT NULL REFERENCES narrative_threads(id),
                    memory_id TEXT NOT NULL REFERENCES memories(id),
                    position INTEGER NOT NULL,
                    PRIMARY KEY (thread_id, memory_id)
                )
            ''')

            # Migration: add last_surfaced_at for thread recency tracking
            try:
                conn.execute("ALTER TABLE narrative_threads ADD COLUMN last_surfaced_at TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # FTS sync is managed manually in create/update/delete methods
            # (SQLite readfile() is not available in standard builds)

            conn.commit()

        # sqlite-vec setup — requires enable_load_extension (guaranteed by
        # the plugin venv which uses a Python compiled with extension support).
        if _SQLITE_VEC_AVAILABLE:
            try:
                with sqlite3.connect(self.db_path) as vec_conn:
                    vec_conn.enable_load_extension(True)
                    sqlite_vec.load(vec_conn)
                    vec_conn.enable_load_extension(False)
                    vec_conn.execute('''
                        CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                            memory_id TEXT PRIMARY KEY,
                            embedding float[512]
                        )
                    ''')
                    vec_conn.commit()
                self._vec_available = True
            except (AttributeError, Exception) as e:
                # AttributeError: enable_load_extension not available on this Python
                # Other: sqlite-vec load failure
                logger.warning("sqlite-vec unavailable (need Python with extension loading): %s", e)

    def _fts_insert(self, conn, rowid: int, title: str, summary: str, tags: str, content: str) -> None:
        """Insert a row into the FTS5 index."""
        conn.execute(
            'INSERT INTO memories_fts(rowid, title, summary, tags, content) VALUES (?, ?, ?, ?, ?)',
            (rowid, title or '', summary or '', tags or '', content or '')
        )

    def _fts_delete(self, conn, rowid: int, title: str, summary: str, tags: str, content: str) -> None:
        """Delete a row from the FTS5 index using delete command."""
        conn.execute(
            "INSERT INTO memories_fts(memories_fts, rowid, title, summary, tags, content) VALUES('delete', ?, ?, ?, ?, ?)",
            (rowid, title or '', summary or '', tags or '', content or '')
        )

    def _get_rowid(self, conn, memory_id: str) -> Optional[int]:
        """Get the rowid for a memory by its UUID."""
        cursor = conn.execute('SELECT rowid FROM memories WHERE id = ?', (memory_id,))
        row = cursor.fetchone()
        return row[0] if row else None

    def _vec_connect(self):
        """Open a sqlite3 connection with sqlite-vec loaded. Caller must close."""
        if not self._vec_available:
            return None
        conn = sqlite3.connect(self.db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return conn

    def _compute_content_hash(self, content: str) -> str:
        """Compute MD5 hash of content for deduplication."""
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def _extract_frontmatter(self, content: str) -> tuple[dict, str]:
        """
        Extract YAML frontmatter from markdown content.

        Returns:
            (metadata_dict, body_content)
        """
        lines = content.split('\n')
        if not lines or lines[0] != '---':
            return {}, content

        # Find closing ---
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i] == '---':
                end_idx = i
                break

        if end_idx is None:
            return {}, content

        # Parse frontmatter as key: value pairs
        metadata = {}
        for line in lines[1:end_idx]:
            if ':' in line:
                key, value = line.split(':', 1)
                metadata[key.strip()] = value.strip()

        body = '\n'.join(lines[end_idx + 1:]).strip()
        return metadata, body

    def _format_markdown(self, metadata: dict, content: str) -> str:
        """Format metadata and content as markdown with YAML frontmatter."""
        lines = ['---']

        for key in ['name', 'description', 'type']:
            if key in metadata:
                lines.append(f'{key}: {metadata[key]}')

        lines.append('---')
        lines.append('')
        lines.append(content)

        return '\n'.join(lines)

    def create(self, path: str, content: str, metadata: dict) -> str:
        """
        Create a new memory.

        Args:
            path: Relative path within stage directory (e.g., "feedback_python.md")
            content: Memory content (markdown body)
            metadata: Dict with keys: stage, title, summary, tags, importance, etc.

        Returns:
            Memory ID (UUID)

        Raises:
            ValueError: If stage is invalid or content hash already exists
        """
        stage = metadata.get('stage', 'ephemeral')

        if stage not in self.STAGES:
            raise ValueError(f"Invalid stage: {stage}. Must be one of {self.STAGES}")

        # Build file path
        file_path = self.base_dir / stage / path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Extract metadata for frontmatter
        frontmatter = {
            'name': metadata.get('title', path.replace('.md', '').replace('_', ' ').title()),
            'description': metadata.get('summary', ''),
            'type': metadata.get('type', 'memory')
        }

        full_content = self._format_markdown(frontmatter, content)
        content_hash = self._compute_content_hash(full_content)
        memory_id = str(uuid.uuid4())
        now = metadata.get('created_at') or datetime.now().isoformat()
        tags_json = json.dumps(metadata.get('tags', []))

        with sqlite3.connect(self.db_path) as conn:
            # Check for duplicate content
            cursor = conn.execute('SELECT id FROM memories WHERE content_hash = ?', (content_hash,))
            if cursor.fetchone():
                raise ValueError(f"Duplicate content detected (hash: {content_hash})")

            # Atomic write: tmp file + rename
            tmp_fd, tmp_path = tempfile.mkstemp(dir=file_path.parent, suffix='.tmp')
            try:
                os.write(tmp_fd, full_content.encode('utf-8'))
                os.close(tmp_fd)
                shutil.move(tmp_path, file_path)
            except Exception:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            # Insert into database
            conn.execute('''
                INSERT INTO memories (
                    id, file_path, stage, title, summary, tags,
                    importance, reinforcement_count, created_at, updated_at,
                    project_context, source_session, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                memory_id, str(file_path), stage,
                metadata.get('title'), metadata.get('summary'), tags_json,
                metadata.get('importance', 0.5), metadata.get('reinforcement_count', 0),
                now, now, self.project_context, metadata.get('source_session'), content_hash
            ))

            # Sync FTS
            rowid = self._get_rowid(conn, memory_id)
            self._fts_insert(conn, rowid, metadata.get('title'), metadata.get('summary'), tags_json, full_content)
            conn.commit()

        return memory_id

    def update(self, memory_id: str, content: str = None, metadata: dict = None) -> None:
        """
        Update an existing memory.

        Args:
            memory_id: Memory UUID
            content: New content (if provided)
            metadata: New metadata (merged with existing)

        Raises:
            ValueError: If memory not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM memories WHERE id = ?', (memory_id,))
            row = cursor.fetchone()

            if not row:
                raise ValueError(f"Memory not found: {memory_id}")

            file_path = Path(row['file_path'])
            old_rowid = self._get_rowid(conn, memory_id)

            # Read existing content for FTS delete
            old_file_content = ''
            if file_path.exists():
                old_file_content = file_path.read_text()
                existing_metadata, existing_body = self._extract_frontmatter(old_file_content)
            else:
                existing_metadata, existing_body = {}, ''

            # Merge metadata
            new_metadata = existing_metadata.copy()
            if metadata:
                new_metadata.update(metadata)

            new_body = content if content is not None else existing_body
            full_content = self._format_markdown(new_metadata, new_body)
            content_hash = self._compute_content_hash(full_content)

            # Atomic write
            tmp_fd, tmp_path = tempfile.mkstemp(dir=file_path.parent, suffix='.tmp')
            try:
                os.write(tmp_fd, full_content.encode('utf-8'))
                os.close(tmp_fd)
                shutil.move(tmp_path, file_path)
            except Exception:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            # FTS: delete old entry
            self._fts_delete(conn, old_rowid, row['title'], row['summary'], row['tags'], old_file_content)

            # Update database
            now = datetime.now().isoformat()
            updates = {'updated_at': now, 'content_hash': content_hash}

            if metadata:
                if 'title' in metadata:
                    updates['title'] = metadata['title']
                if 'summary' in metadata:
                    updates['summary'] = metadata['summary']
                if 'tags' in metadata:
                    updates['tags'] = json.dumps(metadata['tags'])
                if 'importance' in metadata:
                    updates['importance'] = metadata['importance']
                if 'reinforcement_count' in metadata:
                    updates['reinforcement_count'] = metadata['reinforcement_count']
                if 'subsumed_by' in metadata:
                    updates['subsumed_by'] = metadata['subsumed_by']
                if 'stage' in metadata:
                    if metadata['stage'] not in self.STAGES:
                        raise ValueError(f"Invalid stage: {metadata['stage']}")
                    updates['stage'] = metadata['stage']

                    if metadata['stage'] != row['stage']:
                        new_file_path = self.base_dir / metadata['stage'] / file_path.name
                        new_file_path.parent.mkdir(parents=True, exist_ok=True)
                        updates['file_path'] = str(new_file_path)

            set_clause = ', '.join([f'{k} = ?' for k in updates.keys()])
            values = list(updates.values()) + [memory_id]

            conn.execute(f'UPDATE memories SET {set_clause} WHERE id = ?', values)

            # FTS: insert new entry
            new_title = updates.get('title', row['title'])
            new_summary = updates.get('summary', row['summary'])
            new_tags = updates.get('tags', row['tags'])
            self._fts_insert(conn, old_rowid, new_title, new_summary, new_tags, full_content)
            conn.commit()

            # Move file after DB commit — DB is source of truth
            if 'file_path' in updates and updates['file_path'] != str(file_path):
                shutil.move(file_path, updates['file_path'])

    def delete(self, memory_id: str) -> None:
        """
        Delete a memory.

        Args:
            memory_id: Memory UUID

        Raises:
            ValueError: If memory not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM memories WHERE id = ?', (memory_id,))
            row = cursor.fetchone()

            if not row:
                raise ValueError(f"Memory not found: {memory_id}")

            file_path = Path(row['file_path'])
            rowid = self._get_rowid(conn, memory_id)

            # Read file content for FTS delete
            file_content = ''
            if file_path.exists():
                file_content = file_path.read_text()
                file_path.unlink()

            # FTS: delete entry
            self._fts_delete(conn, rowid, row['title'], row['summary'], row['tags'], file_content)

            # Delete from database
            conn.execute('DELETE FROM memories WHERE id = ?', (memory_id,))
            conn.commit()

    def get(self, memory_id: str) -> dict:
        """
        Retrieve a memory by ID.

        Returns:
            Dict with all memory fields + 'content' key

        Raises:
            ValueError: If memory not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM memories WHERE id = ?', (memory_id,))
            row = cursor.fetchone()

            if not row:
                raise ValueError(f"Memory not found: {memory_id}")

            result = dict(row)
            result['tags'] = json.loads(result['tags']) if result['tags'] else []

            # Load content from file
            file_path = Path(result['file_path'])
            if file_path.exists():
                result['content'] = file_path.read_text()
            else:
                result['content'] = ''

            return result

    def list_by_stage(self, stage: str, include_archived: bool = False) -> list[dict]:
        """
        List all memories in a stage.

        Args:
            stage: Memory stage
            include_archived: If False (default), exclude archived memories.

        Returns:
            List of memory dicts (without content)
        """
        if stage not in self.STAGES:
            raise ValueError(f"Invalid stage: {stage}")

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if include_archived:
                cursor.execute('SELECT * FROM memories WHERE stage = ? ORDER BY updated_at DESC', (stage,))
            else:
                cursor.execute('SELECT * FROM memories WHERE stage = ? AND archived_at IS NULL ORDER BY updated_at DESC', (stage,))

            results = []
            for row in cursor.fetchall():
                result = dict(row)
                result['tags'] = json.loads(result['tags']) if result['tags'] else []
                results.append(result)

            return results

    @staticmethod
    def sanitize_fts_term(term: str) -> str:
        """
        Sanitize a term for safe use in FTS5 queries.

        Wraps the term in double-quotes so FTS5 treats it as a literal
        phrase, neutralizing operators (AND, OR, NOT, NEAR, *, ^, etc.).
        Internal double-quotes are escaped by doubling them.

        Args:
            term: Raw search term from user input or observation text.

        Returns:
            Quoted term safe for FTS5 MATCH.
        """
        escaped = term.replace('"', '""')
        return f'"{escaped}"'

    def search_fts(self, query: str, limit: int = 10) -> list[dict]:
        """
        Full-text search across memories.

        Args:
            query: FTS5 query string
            limit: Max results

        Returns:
            List of memory dicts with relevance rank
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute('''
                SELECT m.*, rank
                FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            ''', (query, limit))

            results = []
            for row in cursor.fetchall():
                result = dict(row)
                result['tags'] = json.loads(result['tags']) if result['tags'] else []

                # Load content
                file_path = Path(result['file_path'])
                if file_path.exists():
                    result['content'] = file_path.read_text()

                results.append(result)

            return results

    def search_by_tags(self, tags: list[str]) -> list[dict]:
        """
        Search memories by tags (AND logic).

        Args:
            tags: List of tag strings

        Returns:
            List of memory dicts
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM memories')

            results = []
            for row in cursor.fetchall():
                memory_tags = json.loads(row['tags']) if row['tags'] else []

                # AND logic: all requested tags must be present
                if all(tag in memory_tags for tag in tags):
                    result = dict(row)
                    result['tags'] = memory_tags
                    results.append(result)

            return results

    def record_injection(self, memory_id: str, session_id: str, project_context: str = None) -> None:
        """
        Record that a memory was injected into a session.

        Args:
            memory_id: Memory UUID
            session_id: Session identifier
            project_context: Project directory where the injection occurred (for D-08 tracking)
        """
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Update memory metadata
            conn.execute('''
                UPDATE memories
                SET last_injected_at = ?,
                    injection_count = injection_count + 1
                WHERE id = ?
            ''', (now, memory_id))

            # Log retrieval with project context for cross-project tracking (D-08)
            conn.execute('''
                INSERT INTO retrieval_log (timestamp, session_id, memory_id, retrieval_type, project_context)
                VALUES (?, ?, ?, 'injected', ?)
            ''', (now, session_id, memory_id, project_context))

            conn.commit()

    def record_usage(self, memory_id: str, session_id: str) -> None:
        """
        Record that a memory was actively used (not just injected).

        Args:
            memory_id: Memory UUID
            session_id: Session identifier
        """
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Update memory metadata
            conn.execute('''
                UPDATE memories
                SET last_used_at = ?,
                    usage_count = usage_count + 1
                WHERE id = ?
            ''', (now, memory_id))

            # Update most recent retrieval log entry
            conn.execute('''
                UPDATE retrieval_log
                SET was_used = 1
                WHERE memory_id = ?
                  AND session_id = ?
                  AND timestamp = (
                      SELECT MAX(timestamp)
                      FROM retrieval_log
                      WHERE memory_id = ? AND session_id = ?
                  )
            ''', (memory_id, session_id, memory_id, session_id))

            conn.commit()

    def log_consolidation(
        self,
        action: str,
        memory_id: str,
        from_stage: str,
        to_stage: str,
        rationale: str,
        session_id: str = None
    ) -> None:
        """
        Log a consolidation action.

        Args:
            action: One of: kept, pruned, promoted, demoted, merged, deprecated
            memory_id: Memory UUID
            from_stage: Source stage
            to_stage: Destination stage (can be same as from_stage)
            rationale: Explanation
            session_id: Optional session identifier
        """
        valid_actions = ['kept', 'pruned', 'promoted', 'demoted', 'merged', 'deprecated', 'subsumed']
        if action not in valid_actions:
            raise ValueError(f"Invalid action: {action}. Must be one of {valid_actions}")

        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO consolidation_log (
                    timestamp, session_id, action, memory_id,
                    from_stage, to_stage, rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (now, session_id, action, memory_id, from_stage, to_stage, rationale))
            conn.commit()

    def get_retrieval_log(self, limit: int = 100) -> list[dict]:
        """
        Retrieve recent retrieval log entries.

        Args:
            limit: Max entries to return

        Returns:
            List of log entry dicts
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM retrieval_log
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))

            return [dict(row) for row in cursor.fetchall()]

    def get_session_injections(self, session_id: str) -> list[str]:
        """
        Return memory IDs that were injected in the given session.

        Args:
            session_id: Session identifier

        Returns:
            List of distinct memory_id strings
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT DISTINCT memory_id FROM retrieval_log WHERE session_id = ?",
                (session_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def archive(self, memory_id: str) -> None:
        """
        Archive a memory — exclude from injection but keep searchable via FTS.

        Unlike deprecate(), archival is reversible via unarchive().

        Args:
            memory_id: Memory UUID

        Raises:
            ValueError: If memory not found
        """
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT id FROM memories WHERE id = ?', (memory_id,))
            if not cursor.fetchone():
                raise ValueError(f"Memory not found: {memory_id}")

            # Only set archived_at — do NOT update updated_at, which would
            # reset the recency signal and defeat relevance decay.
            conn.execute(
                'UPDATE memories SET archived_at = ? WHERE id = ?',
                (now, memory_id),
            )
            conn.commit()

    def unarchive(self, memory_id: str) -> None:
        """
        Unarchive (rehydrate) a memory — return it to the active injection pool.

        Args:
            memory_id: Memory UUID

        Raises:
            ValueError: If memory not found
        """
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT id FROM memories WHERE id = ?', (memory_id,))
            if not cursor.fetchone():
                raise ValueError(f"Memory not found: {memory_id}")

            conn.execute(
                'UPDATE memories SET archived_at = NULL, updated_at = ? WHERE id = ?',
                (now, memory_id),
            )
            conn.commit()

    def list_archived(self) -> list[dict]:
        """
        List all archived memories across all stages.

        Returns:
            List of memory dicts (without content) where archived_at is set.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM memories WHERE archived_at IS NOT NULL ORDER BY archived_at DESC'
            )

            results = []
            for row in cursor.fetchall():
                result = dict(row)
                result['tags'] = json.loads(result['tags']) if result['tags'] else []
                results.append(result)

            return results

    # ------------------------------------------------------------------
    # Narrative thread operations
    # ------------------------------------------------------------------

    def create_thread(
        self,
        title: str,
        summary: str,
        narrative: str,
        member_ids: list[str],
    ) -> str:
        """
        Create a narrative thread linking an ordered sequence of memories.

        Args:
            title: Thread title (the narrative arc name).
            summary: One-line summary of the thread.
            narrative: Full narrative text for injection.
            member_ids: Ordered list of memory IDs forming the thread.

        Returns:
            Thread ID (UUID).

        Raises:
            ValueError: If member_ids is empty or any memory ID is invalid.
        """
        if not member_ids:
            raise ValueError("A thread must have at least one member memory")

        thread_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Validate all member IDs exist
            for mid in member_ids:
                cursor = conn.execute('SELECT id FROM memories WHERE id = ?', (mid,))
                if not cursor.fetchone():
                    raise ValueError(f"Memory not found: {mid}")

            conn.execute(
                'INSERT INTO narrative_threads (id, title, summary, narrative, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (thread_id, title, summary, narrative, now, now),
            )

            for position, mid in enumerate(member_ids):
                conn.execute(
                    'INSERT INTO thread_members (thread_id, memory_id, position) VALUES (?, ?, ?)',
                    (thread_id, mid, position),
                )

            conn.commit()

        return thread_id

    def get_thread(self, thread_id: str) -> dict:
        """
        Retrieve a thread with its member memory IDs.

        Returns:
            Dict with thread fields + 'member_ids' list (ordered by position).

        Raises:
            ValueError: If thread not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                'SELECT * FROM narrative_threads WHERE id = ?', (thread_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Thread not found: {thread_id}")

            result = dict(row)

            members = conn.execute(
                'SELECT memory_id FROM thread_members WHERE thread_id = ? ORDER BY position',
                (thread_id,),
            )
            result['member_ids'] = [r['memory_id'] for r in members.fetchall()]

            return result

    def get_threads_for_memory(self, memory_id: str) -> list[dict]:
        """
        Find all threads that contain a given memory.

        Returns:
            List of thread dicts (without member_ids for efficiency).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                'SELECT nt.* FROM narrative_threads nt '
                'JOIN thread_members tm ON nt.id = tm.thread_id '
                'WHERE tm.memory_id = ? '
                'ORDER BY nt.updated_at DESC',
                (memory_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_threads_for_memories_batch(self, memory_ids: list[str]) -> list[dict]:
        """
        Find all threads that contain any of the given memories, in a single query.

        Args:
            memory_ids: List of memory IDs to look up. Empty list returns [] immediately.

        Returns:
            Deduplicated list of thread dicts ordered by updated_at DESC.
            Each dict includes all narrative_threads columns, including narrative
            and last_surfaced_at.
        """
        if not memory_ids:
            return []
        placeholders = ",".join("?" * len(memory_ids))
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f"SELECT DISTINCT nt.* FROM narrative_threads nt "
                f"JOIN thread_members tm ON nt.id = tm.thread_id "
                f"WHERE tm.memory_id IN ({placeholders}) "
                f"ORDER BY nt.updated_at DESC",
                memory_ids,
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_threads_last_surfaced(self, thread_ids: list[str], timestamp: str) -> None:
        """Update last_surfaced_at for given thread IDs in a single query."""
        if not thread_ids:
            return
        placeholders = ",".join("?" * len(thread_ids))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE narrative_threads SET last_surfaced_at = ? "
                f"WHERE id IN ({placeholders})",
                [timestamp, *thread_ids],
            )
            conn.commit()

    def list_threads(self) -> list[dict]:
        """
        List all narrative threads.

        Returns:
            List of thread dicts with member_ids.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                'SELECT * FROM narrative_threads ORDER BY updated_at DESC'
            )
            threads = []
            for row in cursor.fetchall():
                thread = dict(row)
                members = conn.execute(
                    'SELECT memory_id FROM thread_members WHERE thread_id = ? ORDER BY position',
                    (thread['id'],),
                )
                thread['member_ids'] = [r['memory_id'] for r in members.fetchall()]
                threads.append(thread)

            return threads

    def delete_thread(self, thread_id: str) -> None:
        """
        Delete a narrative thread and its membership records.

        Does not delete the member memories themselves.

        Raises:
            ValueError: If thread not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT id FROM narrative_threads WHERE id = ?', (thread_id,)
            )
            if not cursor.fetchone():
                raise ValueError(f"Thread not found: {thread_id}")

            conn.execute('DELETE FROM thread_members WHERE thread_id = ?', (thread_id,))
            conn.execute('DELETE FROM narrative_threads WHERE id = ?', (thread_id,))
            conn.commit()

    # ------------------------------------------------------------------
    # Vector embedding operations (sqlite-vec)
    # ------------------------------------------------------------------

    def store_embedding(self, memory_id: str, embedding: bytes) -> None:
        """
        Store a vector embedding for a memory.

        Args:
            memory_id: Memory UUID.
            embedding: Raw float32 bytes (512 dimensions = 2048 bytes).
        """
        if not self._vec_available:
            return
        conn = self._vec_connect()
        if conn is None:
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
                (memory_id, embedding),
            )
            conn.commit()
        finally:
            conn.close()

    def search_vector(
        self,
        query_embedding: bytes,
        k: int = 10,
        exclude_ids: set = None,
    ) -> list[dict]:
        """
        KNN search against stored embeddings.

        Uses a JOIN against vec_memories and memories in the same index.db.

        Args:
            query_embedding: Raw float32 bytes to search against.
            k: Number of nearest neighbours to retrieve.
            exclude_ids: Optional set of memory IDs to exclude from results.

        Returns:
            List of memory dicts with 'distance' key, ordered by ascending distance.
        """
        if not self._vec_available or query_embedding is None:
            return []
        conn = self._vec_connect()
        if conn is None:
            return []
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT m.*, knn.distance
                FROM (
                    SELECT memory_id, distance
                    FROM vec_memories
                    WHERE embedding MATCH ? AND k = ?
                ) knn
                JOIN memories m ON m.id = knn.memory_id
                ORDER BY knn.distance
                """,
                (query_embedding, k),
            ).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                if exclude_ids and d.get("id") in exclude_ids:
                    continue
                results.append(d)
            return results
        finally:
            conn.close()

    def get_embedding(self, memory_id: str) -> bytes | None:
        """
        Get stored embedding for a memory from index.db.

        Args:
            memory_id: Memory UUID.

        Returns:
            Raw float32 bytes, or None if not stored or vec is unavailable.
        """
        if not self._vec_available:
            return None
        conn = self._vec_connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT embedding FROM vec_memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
