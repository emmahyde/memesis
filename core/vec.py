"""
Vector embedding storage using sqlite-vec via the shared peewee database.

sqlite-vec is loaded into every connection via VecSqliteDatabase in core.models,
so all operations go through the standard db.execute_sql() path.
"""

import logging

logger = logging.getLogger(__name__)

from importlib.util import find_spec
_SQLITE_VEC_AVAILABLE = find_spec("sqlite_vec") is not None


class VecStore:
    """Manages vector embeddings in a sqlite-vec virtual table."""

    def __init__(self):
        self._available = False

        if not _SQLITE_VEC_AVAILABLE:
            return

        try:
            from .models import db
            db.execute_sql(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                    memory_id TEXT PRIMARY KEY,
                    embedding float[512]
                )
                """
            )
            self._available = True
        except Exception as e:
            logger.warning("sqlite-vec unavailable: %s", e)

    @property
    def available(self) -> bool:
        return self._available

    def store_embedding(self, memory_id: str, embedding: bytes) -> None:
        """Store (or replace) a vector embedding for a memory."""
        if not self._available:
            return
        from .models import db
        with db.atomic():
            db.execute_sql(
                "DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,)
            )
            db.execute_sql(
                "INSERT INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
                (memory_id, embedding),
            )

    def search_vector(
        self,
        query_embedding: bytes,
        k: int = 10,
        exclude_ids: set = None,
    ) -> list[dict]:
        """
        KNN search against stored embeddings.

        Returns list of {"memory_id": ..., "distance": ...} dicts ordered by distance.
        """
        if not self._available or query_embedding is None:
            return []
        from .models import db
        cursor = db.execute_sql(
            """
            SELECT memory_id, distance
            FROM vec_memories
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (query_embedding, k),
        )
        rows = cursor.fetchall()
        if exclude_ids:
            rows = [(mid, dist) for mid, dist in rows if mid not in exclude_ids]
        return [{"memory_id": mid, "distance": dist} for mid, dist in rows]

    def get_embedding(self, memory_id: str) -> bytes | None:
        """Get the stored embedding for a memory, or None."""
        if not self._available:
            return None
        from .models import db
        cursor = db.execute_sql(
            "SELECT embedding FROM vec_memories WHERE memory_id = ?",
            (memory_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None
