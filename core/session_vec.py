"""
In-session ephemeral vector index for cross-window deduplication.

Used by Reframe A (REFRAME_A_ENABLED) to inject prior-window observations
into subsequent window prompts, reducing paraphrase re-extraction.

Design notes:
- Table is session-scoped: vec_session_{slug} is a sqlite-vec virtual table
  in the same index.db used by VecStore (vec_memories).
- Single-writer per session.
- Dropped after extraction completes via drop().
- Embeddings are raw float32 bytes from core.embeddings.embed_text.
- Embedding dimension: 512 (matches DEFAULT_DIMENSIONS in embeddings.py).
"""

import logging
import re

logger = logging.getLogger(__name__)

from importlib.util import find_spec
_SQLITE_VEC_AVAILABLE = find_spec("sqlite_vec") is not None

# Embedding dimension must match core.embeddings.DEFAULT_DIMENSIONS
_EMBEDDING_DIM = 512


def _slug(session_id: str) -> str:
    """Convert session_id to a safe SQLite table-name component."""
    return re.sub(r"[^a-zA-Z0-9]", "_", session_id)[:40]


class SessionVecStore:
    """In-session ephemeral vector index for cross-window dedup.

    Scoped to one session_id. Created on first use, dropped after session
    completes. Uses the same index.db as VecStore but a different table.

    Usage:
        svec = SessionVecStore(session_id)
        svec.add(obs_idx=0, text="auth uses JWT")
        similar = svec.query_similar(query_embedding_bytes, k=3)
        svec.drop()
    """

    def __init__(self, session_id: str):
        self._session_id = session_id
        self._table = f"vec_session_{_slug(session_id)}"
        self._available = False

        if not _SQLITE_VEC_AVAILABLE:
            logger.warning("SessionVecStore: sqlite-vec not available — Reframe A will skip index")
            return

        try:
            from .models import db
            db.execute_sql(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {self._table} USING vec0(
                    obs_idx INTEGER PRIMARY KEY,
                    embedding float[{_EMBEDDING_DIM}]
                )
                """
            )
            self._available = True
        except Exception as exc:
            logger.warning("SessionVecStore: failed to create table %s: %s", self._table, exc)

    @property
    def available(self) -> bool:
        return self._available

    def add(self, obs_idx: int, embedding: bytes) -> bool:
        """Store obs_idx + embedding bytes in the session table.

        Returns True on success, False on skip/error.
        """
        if not self._available or embedding is None:
            return False

        if len(embedding) != _EMBEDDING_DIM * 4:
            logger.warning(
                "SessionVecStore.add: expected %d bytes, got %d — skipping",
                _EMBEDDING_DIM * 4,
                len(embedding),
            )
            return False

        try:
            from .models import db
            with db.atomic():
                db.execute_sql(
                    f"DELETE FROM {self._table} WHERE obs_idx = ?",
                    (obs_idx,),
                )
                db.execute_sql(
                    f"INSERT INTO {self._table}(obs_idx, embedding) VALUES (?, ?)",
                    (obs_idx, embedding),
                )
            return True
        except Exception as exc:
            logger.warning("SessionVecStore.add: insert failed for obs_idx=%d: %s", obs_idx, exc)
            return False

    def query_similar(self, query_embedding: bytes, k: int = 3) -> list[int]:
        """Return top-k obs_idx values by cosine similarity.

        Returns list of obs_idx integers ordered by ascending distance.
        Empty list on error or if index is unavailable.
        """
        if not self._available or query_embedding is None:
            return []

        if len(query_embedding) != _EMBEDDING_DIM * 4:
            return []

        try:
            from .models import db
            cursor = db.execute_sql(
                f"""
                SELECT obs_idx, distance
                FROM {self._table}
                WHERE embedding MATCH ? AND k = ?
                ORDER BY distance
                """,
                (query_embedding, k),
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows]
        except Exception as exc:
            logger.warning("SessionVecStore.query_similar: query failed: %s", exc)
            return []

    def drop(self) -> None:
        """Drop the session-scoped virtual table. Idempotent."""
        if not _SQLITE_VEC_AVAILABLE:
            return

        try:
            from .models import db
            db.execute_sql(f"DROP TABLE IF EXISTS {self._table}")
            self._available = False
        except Exception as exc:
            logger.warning("SessionVecStore.drop: failed to drop %s: %s", self._table, exc)
