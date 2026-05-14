"""
Vector embedding storage using Peewee + numpy brute-force KNN.

Replaces the prior apsw + sqlite-vec implementation. At our scale
(~1K-10K memories × 384 dims), numpy dot product is ~5ms — well under
the 500ms UserPromptSubmit budget. Bedrock RTT used to dominate (~200ms)
but we now use local fastembed (~15ms).

All storage goes through the MemoryEmbedding Peewee model, which uses the
same SqliteDatabase connection as the rest of the app. No extension loading,
no second SQLite driver — eliminates the WAL mode dual-driver risk.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .embeddings import (
    DEFAULT_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_VERSION,
)

logger = logging.getLogger(__name__)


class VecStore:
    """
    Manages vector embeddings via the MemoryEmbedding Peewee model.

    Embeddings stored as float32 BLOBs. KNN computed in numpy. Embedding
    metadata (model, version, dim) lives on the same row as the embedding.

    The `available` property is preserved for call-site compatibility but is
    always True post-numpy-rewrite — there is no optional C extension to fail.
    """

    def __init__(self, db_path: Path):
        self._db_path = str(db_path)
        self._embedding_model = DEFAULT_EMBEDDING_MODEL
        self._embedding_version = DEFAULT_EMBEDDING_VERSION
        self._embedding_dim = DEFAULT_DIMENSIONS
        self._available = True
        try:
            self._sync_system_table()
        except Exception as exc:
            logger.debug("VecStore _system sync skipped: %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def dim(self) -> int:
        return self._embedding_dim

    @property
    def model(self) -> str:
        return self._embedding_model

    def _sync_system_table(self) -> None:
        """Write active embedding constants into _system table if it exists."""
        from .models import db
        for k, v in (
            ("embedding_model", self._embedding_model),
            ("embedding_version", self._embedding_version),
            ("embedding_dim", str(self._embedding_dim)),
        ):
            try:
                db.execute_sql(
                    "INSERT OR REPLACE INTO _system (key, value) VALUES (?, ?)",
                    (k, v),
                )
            except Exception as exc:
                logger.debug("_system sync skipped (%s): %s", k, exc)
                return

    def store_embedding(self, memory_id: str, embedding: bytes) -> None:
        """
        Store (or replace) a vector embedding for a memory.

        Raises:
            ValueError: if embedding byte length does not match
                        self._embedding_dim float32 values.
        """
        expected_bytes = self._embedding_dim * 4
        if len(embedding) != expected_bytes:
            actual_dim = len(embedding) // 4
            raise ValueError(
                f"Embedding dimension mismatch: expected {self._embedding_dim} floats "
                f"({expected_bytes} bytes), got {len(embedding)} bytes "
                f"({actual_dim} floats). Ensure embedding was produced with "
                f"model={self._embedding_model} and dimensions={self._embedding_dim}."
            )

        from .models import MemoryEmbedding, db
        now = datetime.now(timezone.utc)
        with db.atomic():
            MemoryEmbedding.insert(
                memory_id=memory_id,
                embedding=embedding,
                embedding_model=self._embedding_model,
                embedding_version=self._embedding_version,
                embedding_dim=self._embedding_dim,
                updated_at=now,
            ).on_conflict(
                conflict_target=[MemoryEmbedding.memory_id],
                update={
                    MemoryEmbedding.embedding: embedding,
                    MemoryEmbedding.embedding_model: self._embedding_model,
                    MemoryEmbedding.embedding_version: self._embedding_version,
                    MemoryEmbedding.embedding_dim: self._embedding_dim,
                    MemoryEmbedding.updated_at: now,
                },
            ).execute()

    def search_vector(
        self,
        query_embedding: bytes,
        k: int = 10,
        exclude_ids: set | None = None,
    ) -> list[dict]:
        """
        KNN search over stored embeddings via numpy brute force.

        Returns list of {"memory_id": str, "distance": float} dicts ordered by
        ascending distance (most similar first). Uses cosine distance (1 - dot
        product) — bge-small embeddings are L2-normalized at production time.
        """
        if query_embedding is None:
            return []
        if len(query_embedding) != self._embedding_dim * 4:
            return []

        from .models import MemoryEmbedding
        rows = list(
            MemoryEmbedding.select(
                MemoryEmbedding.memory_id, MemoryEmbedding.embedding
            ).where(MemoryEmbedding.embedding_dim == self._embedding_dim)
        )
        if not rows:
            return []

        ids = [r.memory_id for r in rows]
        matrix = np.frombuffer(
            b"".join(bytes(r.embedding) for r in rows),
            dtype=np.float32,
        ).reshape(len(ids), self._embedding_dim)
        query = np.frombuffer(query_embedding, dtype=np.float32)
        sims = matrix @ query
        dists = 1.0 - sims
        order = np.argsort(dists)

        excluded = exclude_ids or set()
        results: list[dict] = []
        for idx in order:
            mid = ids[int(idx)]
            if mid in excluded:
                continue
            results.append({"memory_id": mid, "distance": float(dists[int(idx)])})
            if len(results) >= k:
                break
        return results

    def get_embedding(self, memory_id: str) -> bytes | None:
        """Return stored embedding bytes for a memory, or None."""
        from .models import MemoryEmbedding
        try:
            row = MemoryEmbedding.get(MemoryEmbedding.memory_id == memory_id)
            return bytes(row.embedding) if row.embedding else None
        except MemoryEmbedding.DoesNotExist:
            return None

    def get_embedding_meta(self, memory_id: str) -> dict | None:
        """
        Return embedding metadata for a memory.

        Returns dict with keys: memory_id, embedding_model, embedding_version,
        embedding_dim. Returns None if not found.
        """
        from .models import MemoryEmbedding
        try:
            row = MemoryEmbedding.get(MemoryEmbedding.memory_id == memory_id)
        except MemoryEmbedding.DoesNotExist:
            return None
        return {
            "memory_id": row.memory_id,
            "embedding_model": row.embedding_model,
            "embedding_version": row.embedding_version,
            "embedding_dim": row.embedding_dim,
        }


