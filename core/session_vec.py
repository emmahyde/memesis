"""
In-session ephemeral vector index for cross-window deduplication.

Used by Reframe A (REFRAME_A_ENABLED) to inject prior-window observations into
subsequent window prompts, reducing paraphrase re-extraction.

Design: a per-session dict keyed by obs_idx. Vectors are dot-product compared in
numpy. State is in-process only — lost on crash, not persisted to disk. Drop()
clears the dict. The constructor still takes (db_path, session_id) for
call-site compatibility but does not touch disk.
"""

import logging
from pathlib import Path

import numpy as np

from .embeddings import DEFAULT_DIMENSIONS

logger = logging.getLogger(__name__)

_EMBEDDING_DIM = DEFAULT_DIMENSIONS


class SessionVecStore:
    """In-process per-session vector index.

    Usage:
        svec = SessionVecStore(db_path, session_id)
        svec.add(obs_idx=0, embedding=embed_text("auth uses JWT"))
        similar_idxs = svec.query_similar(query_embedding_bytes, k=3)
        svec.drop()
    """

    def __init__(self, db_path: Path, session_id: str):
        self._session_id = session_id
        self._embeddings: dict[int, bytes] = {}
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def add(self, obs_idx: int, embedding: bytes) -> bool:
        """
        Store obs_idx + embedding bytes in the session dict.

        Returns True on success, False on skip (None embedding or dim mismatch).
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
        self._embeddings[obs_idx] = embedding
        return True

    def query_similar(self, query_embedding: bytes, k: int = 3) -> list[int]:
        """
        Return top-k obs_idx values by cosine similarity (descending).

        bge-small embeddings are L2-normalized → cosine = dot product.
        """
        if not self._available or query_embedding is None:
            return []
        if not self._embeddings:
            return []
        if len(query_embedding) != _EMBEDDING_DIM * 4:
            return []

        ids = list(self._embeddings.keys())
        matrix = np.frombuffer(
            b"".join(self._embeddings[i] for i in ids),
            dtype=np.float32,
        ).reshape(len(ids), _EMBEDDING_DIM)
        query = np.frombuffer(query_embedding, dtype=np.float32)
        sims = matrix @ query
        order = np.argsort(-sims)[:k]
        return [ids[int(i)] for i in order]

    def drop(self) -> None:
        """Clear the session dict. Idempotent."""
        self._embeddings.clear()
        self._available = False
