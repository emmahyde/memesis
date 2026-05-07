"""
Vector embedding storage using sqlite-vec via apsw.

Isolated from the main Peewee database because sqlite-vec requires
extension loading through apsw (the stdlib sqlite3 module on macOS
ships without load_extension support).

Embedding metadata (model, version, dim) is stored in a companion regular
table (vec_embedding_meta) because vec0 virtual tables do not support
ALTER TABLE ADD COLUMN. Active system configuration is stored in _system.
"""

import logging
import struct
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

    On initialisation:
    - Ensures vec_memories virtual table exists.
    - Populates _system with current embedding constants.
    - Backfills vec_embedding_meta rows that have a blank embedding_model.
    """

    def __init__(self, db_path: Path):
        self._db_path = str(db_path)
        self._available = False

        # Import embedding constants here to avoid circular imports at module level.
        from .embeddings import (
            DEFAULT_DIMENSIONS,
            DEFAULT_EMBEDDING_MODEL,
            DEFAULT_EMBEDDING_VERSION,
        )
        self._embedding_model = DEFAULT_EMBEDDING_MODEL
        self._embedding_version = DEFAULT_EMBEDDING_VERSION
        self._embedding_dim = DEFAULT_DIMENSIONS

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

            # Populate _system with active model constants.
            # _system may not exist yet on a fresh DB (migration creates it),
            # so we guard with a try/except.
            self._sync_system_table(conn)

            # Backfill: rows in vec_embedding_meta with blank embedding_model
            # get updated with the current model constants.
            self._backfill_metadata(conn)

            conn.close()
            self._available = True
        except Exception as e:
            logger.warning("sqlite-vec unavailable: %s", e)

    def _connect(self):
        """Open an apsw connection with the sqlite-vec extension loaded.

        Sets busy_timeout to 5000ms so concurrent writers back off gracefully
        instead of immediately raising an error (previously defaulted to 0ms).
        """
        conn = apsw.Connection(self._db_path)
        conn.setbusytimeout(5000)
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())
        return conn

    def _sync_system_table(self, conn) -> None:
        """Write active embedding constants into _system table if it exists."""
        try:
            conn.execute(
                "INSERT OR REPLACE INTO _system (key, value) VALUES (?, ?)",
                ("embedding_model", self._embedding_model),
            )
            conn.execute(
                "INSERT OR REPLACE INTO _system (key, value) VALUES (?, ?)",
                ("embedding_version", self._embedding_version),
            )
            conn.execute(
                "INSERT OR REPLACE INTO _system (key, value) VALUES (?, ?)",
                ("embedding_dim", str(self._embedding_dim)),
            )
        except Exception as exc:
            # _system table may not exist yet if migration hasn't run
            logger.debug("_system table not ready yet (migration pending?): %s", exc)

    def _backfill_metadata(self, conn) -> None:
        """Update vec_embedding_meta rows with blank embedding_model."""
        try:
            conn.execute(
                """
                UPDATE vec_embedding_meta
                SET embedding_model = ?,
                    embedding_version = ?,
                    embedding_dim = ?
                WHERE embedding_model = ''
                """,
                (self._embedding_model, self._embedding_version, self._embedding_dim),
            )
        except Exception as exc:
            logger.debug("vec_embedding_meta backfill skipped (table not ready?): %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    def store_embedding(self, memory_id: str, embedding: bytes) -> None:
        """
        Store (or replace) a vector embedding for a memory.

        Also writes/updates vec_embedding_meta for this memory_id.

        Raises:
            ValueError: if embedding byte length does not correspond to
                        self._embedding_dim float32 values.
        """
        if not self._available:
            return

        expected_bytes = self._embedding_dim * 4  # float32 = 4 bytes each
        if len(embedding) != expected_bytes:
            actual_dim = len(embedding) // 4
            raise ValueError(
                f"Embedding dimension mismatch: expected {self._embedding_dim} floats "
                f"({expected_bytes} bytes), got {len(embedding)} bytes "
                f"({actual_dim} floats). Ensure the embedding was produced with "
                f"model={self._embedding_model} and dimensions={self._embedding_dim}."
            )

        conn = self._connect()
        try:
            conn.execute("DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "INSERT INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
                (memory_id, embedding),
            )
            # Upsert metadata companion row.
            try:
                conn.execute(
                    """
                    INSERT INTO vec_embedding_meta
                        (memory_id, embedding_model, embedding_version, embedding_dim)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        embedding_model = excluded.embedding_model,
                        embedding_version = excluded.embedding_version,
                        embedding_dim = excluded.embedding_dim
                    """,
                    (
                        memory_id,
                        self._embedding_model,
                        self._embedding_version,
                        self._embedding_dim,
                    ),
                )
            except Exception as exc:
                logger.debug("vec_embedding_meta upsert failed for %s: %s", memory_id, exc)
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

    def get_embedding_meta(self, memory_id: str) -> dict | None:
        """
        Get the stored embedding metadata for a memory.

        Returns dict with keys: memory_id, embedding_model, embedding_version,
        embedding_dim. Returns None if not found or table unavailable.
        """
        if not self._available:
            return None
        conn = self._connect()
        try:
            row = next(
                conn.execute(
                    """
                    SELECT memory_id, embedding_model, embedding_version, embedding_dim
                    FROM vec_embedding_meta
                    WHERE memory_id = ?
                    """,
                    (memory_id,),
                ),
                None,
            )
            if row is None:
                return None
            return {
                "memory_id": row[0],
                "embedding_model": row[1],
                "embedding_version": row[2],
                "embedding_dim": row[3],
            }
        except Exception as exc:
            logger.debug("get_embedding_meta failed for %s: %s", memory_id, exc)
            return None
        finally:
            conn.close()
