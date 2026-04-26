"""
Peewee ORM model definitions for the memory lifecycle.

All tables are defined here; the database connection is deferred
(SqliteDatabase(None)) and bound at runtime via init_db() in core.database.
"""

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime

from peewee import (
    AutoField,
    CompositeKey,
    FloatField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

logger = logging.getLogger(__name__)

# Deferred database — bound by init_db()
db = SqliteDatabase(None)

_FTS_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "and", "but", "or",
    "if", "while", "what", "which", "who", "whom", "this", "that",
    "these", "those", "it", "its", "s",
})


class BaseModel(Model):
    class Meta:
        database = db


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class Memory(BaseModel):
    """Maps to the ``memories`` table."""

    id = TextField(primary_key=True, default=lambda: str(uuid.uuid4()))
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
    echo_count = IntegerField(default=0, null=True)
    next_injection_due = TextField(null=True)
    injection_ease_factor = FloatField(default=2.5, null=True)
    injection_interval_days = FloatField(default=1.0, null=True)

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

    @staticmethod
    def tokenize_fts_query(query: str) -> str:
        """
        Convert a natural language query into an FTS5 OR query.

        Strips stop words, quotes each remaining token to neutralize FTS5
        operators, and joins with OR so any matching term produces results.
        Falls back to the raw query (quoted) if no tokens remain.
        """
        tokens = re.findall(r"[a-zA-Z0-9_'-]+", query.lower())
        keywords = [t for t in tokens if t not in _FTS_STOP_WORDS and len(t) > 1]
        if not keywords:
            escaped = query.replace('"', '""')
            return f'"{escaped}"'
        # Quote each keyword and join with OR
        return " OR ".join(f'"{kw}"' for kw in keywords)

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
    created_at = TextField(default=lambda: datetime.now().isoformat())
    updated_at = TextField(default=lambda: datetime.now().isoformat())
    last_surfaced_at = TextField(null=True)
    arc_affect = TextField(null=True)  # JSON: trajectory, start/end valence, friction_ratio

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
# MemoryEdge
# ---------------------------------------------------------------------------


class MemoryEdge(BaseModel):
    """Pre-computed and incremental edges between memories for graph expansion.

    Recomputable types (rebuilt by compute_edges):
        thread_neighbor, tag_cooccurrence

    Incremental types (created during pipeline steps, preserved across rebuilds):
        caused_by, refined_from, subsumed_into, contradicts, echo
    """

    id = AutoField()
    source_id = TextField()
    target_id = TextField()
    edge_type = TextField()
    weight = FloatField(default=1.0)
    metadata = TextField(null=True)  # JSON: evidence, affect, timestamps

    class Meta:
        table_name = "memory_edges"

    # Edge types that compute_edges() rebuilds from scratch each run.
    RECOMPUTABLE_TYPES = {"thread_neighbor", "tag_cooccurrence"}


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
    query_text = TextField(null=True)
    limit_count = IntegerField(null=True)
    selected_count = IntegerField(null=True)
    metadata = TextField(null=True)

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
    prompt = TextField(null=True)
    llm_response = TextField(null=True)
    model = TextField(null=True)
    input_tokens = IntegerField(null=True)
    output_tokens = IntegerField(null=True)
    latency_ms = IntegerField(null=True)
    input_observation_refs = TextField(null=True)

    class Meta:
        table_name = "consolidation_log"


# ---------------------------------------------------------------------------
# Observer instrumentation tables
# ---------------------------------------------------------------------------


class Observation(BaseModel):
    """Raw and filtered observations captured before consolidation decisions."""

    id = AutoField()
    created_at = TextField(default=lambda: datetime.now().isoformat())
    session_id = TextField(null=True)
    source_path = TextField(null=True)
    ordinal = IntegerField(null=True)
    content = TextField()
    filtered_content = TextField(null=True)
    content_hash = TextField(null=True)
    status = TextField(null=True)
    memory_id = TextField(null=True)
    metadata = TextField(null=True)

    class Meta:
        table_name = "observations"


class RetrievalCandidate(BaseModel):
    """Per-candidate retrieval scoring details for Observer waterfall views."""

    id = AutoField()
    retrieval_log_id = IntegerField()
    memory_id = TextField()
    rank = IntegerField()
    fts_rank = IntegerField(null=True)
    vector_rank = IntegerField(null=True)
    semantic_score = FloatField(default=0.0)
    recency_score = FloatField(default=0.0)
    importance_score = FloatField(default=0.0)
    affect_score = FloatField(default=0.0)
    reinforcement_score = FloatField(default=0.0)
    boost_score = FloatField(default=0.0)
    final_score = FloatField(default=0.0)
    was_selected = IntegerField(default=0)
    metadata = TextField(null=True)

    class Meta:
        table_name = "retrieval_candidates"


class AffectLog(BaseModel):
    """Point-in-time affect/coherence state snapshots for Observer timelines."""

    id = AutoField()
    timestamp = TextField(default=lambda: datetime.now().isoformat())
    session_id = TextField(null=True)
    project_context = TextField(null=True)
    frustration = FloatField(default=0.0)
    satisfaction = FloatField(default=0.0)
    momentum = FloatField(default=0.0)
    arousal = FloatField(default=0.0)
    valence = FloatField(default=0.0)
    degradation = FloatField(default=0.0)
    coherence = FloatField(null=True)
    metadata = TextField(null=True)

    class Meta:
        table_name = "affect_log"


class EvalRun(BaseModel):
    """Eval report metadata and JSON payloads consumed by Observer."""

    id = AutoField()
    run_id = TextField(unique=True)
    created_at = TextField(default=lambda: datetime.now().isoformat())
    finished_at = TextField(null=True)
    suite = TextField()
    status = TextField(default="running")
    command = TextField(null=True)
    config_a = TextField(null=True)
    config_b = TextField(null=True)
    score = FloatField(null=True)
    report_json = TextField(null=True)

    class Meta:
        table_name = "eval_runs"
