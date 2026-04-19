"""
Vector embedding storage using sqlite-vec via apsw.

Isolated from the main Peewee database because sqlite-vec requires
extension loading through apsw (the stdlib sqlite3 module on macOS
ships without load_extension support).
"""

import logging
from pathlib import Path

import apsw

logger = logging.getLogger(__name__)

try:
    import sqlite_vec

    _SQLITE_VEC_AVAILABLE = True
except ImportError:
    _SQLITE_VEC_AVAILABLE = False


class VecStore:
    """
    Manages vector embeddings in a sqlite-vec virtual table.

    Uses apsw for all connections because sqlite-vec needs
    enable_load_extension(True).
    """

    def __init__(self, db_path: Path):
        self._db_path = str(db_path)
        self._available = False

        if not _SQLITE_VEC_AVAILABLE:
            return

        try:
            conn = self._connect()
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                    memory_id TEXT PRIMARY KEY,
                    embedding float[512]
                )
                """
            )
            conn.close()
            self._available = True
        except Exception as e:
            logger.warning("sqlite-vec unavailable: %s", e)

    def _connect(self):
        """Open an apsw connection with the sqlite-vec extension loaded."""
        conn = apsw.Connection(self._db_path)
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())
        return conn

    @property
    def available(self) -> bool:
        return self._available

    def store_embedding(self, memory_id: str, embedding: bytes) -> None:
        """Store (or replace) a vector embedding for a memory."""
        if not self._available:
            return
        conn = self._connect()
        try:
            conn.execute("DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "INSERT INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
                (memory_id, embedding),
            )
        finally:
            conn.close()

    def search_vector(
        self,
        query_embedding: bytes,
        k: int = 10,
        exclude_ids: set = None,
    ) -> list[tuple]:
        """
        KNN search against stored embeddings.

        Returns list of (memory_id, distance) tuples ordered by distance.
        The caller is responsible for hydrating full memory dicts.
        """
        if not self._available or query_embedding is None:
            return []
        conn = self._connect()
        try:
            rows = list(
                conn.execute(
                    """
                    SELECT memory_id, distance
                    FROM vec_memories
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance
                    """,
                    (query_embedding, k),
                )
            )
        finally:
            conn.close()

        if exclude_ids:
            rows = [(mid, dist) for mid, dist in rows if mid not in exclude_ids]
        return [{"memory_id": mid, "distance": dist} for mid, dist in rows]

    def get_embedding(self, memory_id: str) -> bytes | None:
        """Get the stored embedding for a memory, or None."""
        if not self._available:
            return None
        conn = self._connect()
        try:
            row = next(
                conn.execute(
                    "SELECT embedding FROM vec_memories WHERE memory_id = ?",
                    (memory_id,),
                ),
                None,
            )
            return row[0] if row else None
        finally:
            conn.close()
