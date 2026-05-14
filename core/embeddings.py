"""
Embedding service — wraps fastembed (BAAI/bge-small-en-v1.5) for local CPU embeddings.

Computes text embeddings via ONNX runtime and returns raw float32 bytes ready
for storage as a Peewee BlobField on the MemoryEmbedding table.

The embedding is computed once at memory creation time and persisted in
memory_embeddings. Query-time embedding uses the same function.
"""

import logging
import threading

logger = logging.getLogger(__name__)

DEFAULT_DIMENSIONS = 384
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBEDDING_VERSION = "fastembed-v1"

_model = None
_model_lock = threading.Lock()


def _get_model():
    """Lazy-init the fastembed model. Singleton, thread-safe."""
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model
        try:
            from fastembed import TextEmbedding
        except ImportError:
            logger.warning("fastembed not installed — embeddings unavailable")
            return None
        try:
            _model = TextEmbedding(model_name=DEFAULT_EMBEDDING_MODEL)
            return _model
        except Exception as e:
            logger.warning("Failed to init fastembed model: %s", e)
            return None


def embed_text(text: str, dimensions: int = DEFAULT_DIMENSIONS) -> bytes | None:
    """
    Embed text via fastembed (BAAI/bge-small-en-v1.5).

    Returns raw float32 bytes (len = dimensions * 4), or None if unavailable.

    Args:
        text: The text to embed. bge-small handles up to 512 tokens (~2000 chars).
        dimensions: Output vector dimensions. Must equal DEFAULT_DIMENSIONS (384) —
                    bge-small is fixed-dim. Other values raise ValueError.

    Returns:
        Raw float32 bytes suitable for BlobField storage, or None on failure.
    """
    if dimensions != DEFAULT_DIMENSIONS:
        raise ValueError(
            f"bge-small-en-v1.5 is fixed at {DEFAULT_DIMENSIONS} dims; "
            f"requested {dimensions}"
        )

    model = _get_model()
    if model is None:
        return None

    text = text[:2000]

    try:
        vec = next(iter(model.embed([text])))
        # bge-small fastembed output is already L2-normalized; cosine = dot product.
        return vec.astype("float32").tobytes()
    except Exception as e:
        logger.warning("Embedding failed: %s", e)
        return None


def embed_for_memory(
    title: str,
    summary: str = "",
    content: str = "",
    tags: list[str] | None = None,
) -> bytes | None:
    """
    Embed a memory's key fields for storage in memory_embeddings.

    Composition strategy (calibrated for bge-small short-text similarity):
    - Title is repeated 2× — title carries the strongest topic signal per
      token; doubling inflates same-topic cosine similarity by ~0.05, which
      keeps clustering thresholds portable across embedding models.
    - Tags appended as space-joined text. Shared `type:X` tags pull related
      memories closer in vector space, reinforcing tag-based grouping.
    - Full content is included (capped to 2000 chars by embed_text's
      tokenizer-safe truncation), not just a 500-char prefix.
    """
    parts: list[str] = []
    if title:
        parts.append(title)
        parts.append(title)
    if summary:
        parts.append(summary)
    if content:
        parts.append(content)
    if tags:
        parts.append(" ".join(str(t) for t in tags if t))
    return embed_text(". ".join(parts))
