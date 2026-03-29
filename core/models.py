"""
Peewee ORM model definitions for the memory lifecycle.

All tables are defined here; the database connection is deferred
(SqliteDatabase(None)) and bound at runtime via init_db() in core.database.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    CompositeKey,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

logger = logging.getLogger(__name__)

# Deferred database — bound by init_db()
db = SqliteDatabase(None)


class BaseModel(Model):
    class Meta:
        database = db


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class Memory(BaseModel):
    """Maps to the ``memories`` table."""

    id = TextField(primary_key=True, default=lambda: str(uuid.uuid4()))
    file_path = TextField(null=True)
    stage = TextField()
    title = TextField(null=True)
    summary = TextField(null=True)
    content = TextField(null=True)
    tags = TextField(null=True)  # JSON string
    importance = FloatField(default=0.5, null=True)
    reinforcement_count = IntegerField(default=0, null=True)
    created_at = TextField(null=True)
    updated_at = TextField(null=True)
    last_injected_at = TextField(null=True)
    last_used_at = TextField(null=True)
    injection_count = IntegerField(default=0, null=True)
    usage_count = IntegerField(default=0, null=True)
    project_context = TextField(null=True)
    source_session = TextField(null=True)
    content_hash = TextField(null=True)
    archived_at = TextField(null=True)
    subsumed_by = TextField(null=True)

    class Meta:
        table_name = "memories"

    # -- Scopes --------------------------------------------------------

    @classmethod
    def active(cls):
        """Return a query for non-archived memories."""
        return cls.select().where(cls.archived_at.is_null())

    @classmethod
    def by_stage(cls, stage, include_archived=False):
        """Return a query filtered by stage."""
        q = cls.select().where(cls.stage == stage)
        if not include_archived:
            q = q.where(cls.archived_at.is_null())
        return q.order_by(cls.updated_at.desc())

    # -- FTS search ----------------------------------------------------

    @classmethod
    def search_fts(cls, query, limit=10):
        """
        Full-text search across memories via FTS5.

        Returns a list of Memory model instances with an extra ``rank``
        attribute.
        """
        sql = (
            "SELECT m.*, fts.rank "
            "FROM memories_fts fts "
            "JOIN memories m ON fts.rowid = m.rowid "
            "WHERE memories_fts MATCH ? "
            "ORDER BY fts.rank "
            "LIMIT ?"
        )
        cursor = db.execute_sql(sql, (query, limit))
        desc = [d[0] for d in cursor.description]
        results = []
        for row in cursor.fetchall():
            row_dict = dict(zip(desc, row))
            rank = row_dict.pop("rank", None)
            mem = cls(**row_dict)
            mem._rank = rank
            # Mark as saved to avoid INSERT on accidental save()
            mem._dirty.clear()
            results.append(mem)
        return results

    @staticmethod
    def sanitize_fts_term(term: str) -> str:
        """
        Sanitize a term for safe use in FTS5 queries.

        Wraps the term in double-quotes so FTS5 treats it as a literal
        phrase, neutralising operators (AND, OR, NOT, NEAR, *, ^, etc.).
        Internal double-quotes are escaped by doubling them.
        """
        escaped = term.replace('"', '""')
        return f'"{escaped}"'

    # -- Properties ----------------------------------------------------

    @property
    def tag_list(self):
        """Parse the JSON tags string into a Python list."""
        if self.tags:
            try:
                return json.loads(self.tags)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    @tag_list.setter
    def tag_list(self, value):
        self.tags = json.dumps(value) if value is not None else None

    def compute_hash(self, full_content: str) -> str:
        """Compute MD5 hash of the full markdown content."""
        return hashlib.md5(full_content.encode("utf-8")).hexdigest()

    # -- Save / Delete with FTS sync -----------------------------------

    def save(self, force_insert=False, only=None):
        """Override save to keep FTS index in sync (atomic)."""
        self.updated_at = datetime.now().isoformat()
        if self.content:
            self.content_hash = self.compute_hash(self.content)

        is_update = not force_insert and self.id and self._pk_exists()

        with db.atomic():
            if is_update:
                self._fts_delete_from_db()
            result = super().save(force_insert=force_insert, only=only)
            self._fts_insert()
        return result

    def delete_instance(self, recursive=False, delete_nullable=False):
        """Override delete to remove FTS entry."""
        self._fts_delete()
        return super().delete_instance(
            recursive=recursive, delete_nullable=delete_nullable
        )

    def _pk_exists(self) -> bool:
        """Check if this primary key already exists in the database."""
        try:
            Memory.get_by_id(self.id)
            return True
        except Memory.DoesNotExist:
            return False

    def _fts_insert(self):
        """Insert a row into the FTS5 index."""
        rowid = self._get_rowid()
        if rowid is None:
            return
        db.execute_sql(
            "INSERT INTO memories_fts(rowid, title, summary, tags, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                rowid,
                self.title or "",
                self.summary or "",
                self.tags or "",
                self.content or "",
            ),
        )

    def _fts_delete(self):
        """Delete a row from the FTS5 index using in-memory values."""
        rowid = self._get_rowid()
        if rowid is None:
            return
        db.execute_sql(
            "INSERT INTO memories_fts(memories_fts, rowid, title, summary, tags, content) "
            "VALUES('delete', ?, ?, ?, ?, ?)",
            (
                rowid,
                self.title or "",
                self.summary or "",
                self.tags or "",
                self.content or "",
            ),
        )

    def _fts_delete_from_db(self):
        """Delete a row from the FTS5 index using current DB values (not in-memory)."""
        cursor = db.execute_sql(
            "SELECT rowid, title, summary, tags, content FROM memories WHERE id = ?",
            (self.id,),
        )
        row = cursor.fetchone()
        if row is None:
            return
        rowid, title, summary, tags, content = row
        db.execute_sql(
            "INSERT INTO memories_fts(memories_fts, rowid, title, summary, tags, content) "
            "VALUES('delete', ?, ?, ?, ?, ?)",
            (
                rowid,
                title or "",
                summary or "",
                tags or "",
                content or "",
            ),
        )

    def _get_rowid(self):
        """Get the SQLite rowid for this memory."""
        cursor = db.execute_sql(
            "SELECT rowid FROM memories WHERE id = ?", (self.id,)
        )
        row = cursor.fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# NarrativeThread
# ---------------------------------------------------------------------------


class NarrativeThread(BaseModel):
    """Maps to the ``narrative_threads`` table."""

    id = TextField(primary_key=True, default=lambda: str(uuid.uuid4()))
    title = TextField()
    summary = TextField(null=True)
    narrative = TextField(null=True)
    created_at = TextField()
    updated_at = TextField()
    last_surfaced_at = TextField(null=True)

    class Meta:
        table_name = "narrative_threads"

    @property
    def members(self):
        """Return ordered Memory query via ThreadMember join."""
        return (
            Memory.select()
            .join(ThreadMember, on=(Memory.id == ThreadMember.memory_id))
            .where(ThreadMember.thread_id == self.id)
            .order_by(ThreadMember.position)
        )

    @property
    def member_ids(self):
        """Return list of memory_id strings in order."""
        return [
            tm.memory_id
            for tm in ThreadMember.select()
            .where(ThreadMember.thread_id == self.id)
            .order_by(ThreadMember.position)
        ]


# ---------------------------------------------------------------------------
# ThreadMember
# ---------------------------------------------------------------------------


class ThreadMember(BaseModel):
    """Maps to the ``thread_members`` table. Composite PK (thread_id, memory_id)."""

    thread_id = TextField()
    memory_id = TextField()
    position = IntegerField()

    class Meta:
        table_name = "thread_members"
        primary_key = CompositeKey("thread_id", "memory_id")


# ---------------------------------------------------------------------------
# RetrievalLog
# ---------------------------------------------------------------------------


class RetrievalLog(BaseModel):
    """Maps to the ``retrieval_log`` table."""

    id = AutoField()
    timestamp = TextField()
    session_id = TextField(null=True)
    memory_id = TextField(null=True)
    retrieval_type = TextField(null=True)
    was_used = IntegerField(default=0)
    relevance_score = FloatField(null=True)
    project_context = TextField(null=True)

    class Meta:
        table_name = "retrieval_log"


# ---------------------------------------------------------------------------
# ConsolidationLog
# ---------------------------------------------------------------------------


class ConsolidationLog(BaseModel):
    """Maps to the ``consolidation_log`` table."""

    id = AutoField()
    timestamp = TextField()
    session_id = TextField(null=True)
    action = TextField(null=True)
    memory_id = TextField(null=True)
    from_stage = TextField(null=True)
    to_stage = TextField(null=True)
    rationale = TextField(null=True)

    class Meta:
        table_name = "consolidation_log"
