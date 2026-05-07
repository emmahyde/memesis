"""
Embedding service — OpenAI-compatible client routed via OpenRouter
(or directly to OpenAI if EMBEDDINGS_PROVIDER=openai).

Computes text embeddings via API and returns raw float32 bytes ready
for storage in sqlite-vec's vec0 virtual table.

The embedding is computed once at memory creation time and persisted
in the vec_memories table. Query-time embedding uses the same function.

Provider selection (env):
  EMBEDDINGS_PROVIDER=openrouter (default) — uses OPENROUTER_API_KEY,
      base_url=https://openrouter.ai/api/v1, model=openai/text-embedding-3-small
  EMBEDDINGS_PROVIDER=openai — uses OPENAI_API_KEY, model=text-embedding-3-small
  EMBEDDINGS_MODEL — override the model id (still must be embedding-capable)
"""

import logging
import os
import struct

logger = logging.getLogger(__name__)

# text-embedding-3-small supports any dimension up to 1536 (Matryoshka).
DEFAULT_DIMENSIONS = 512

_PROVIDER_DEFAULTS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        "model": "gemini-embedding-001",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "model": "openai/text-embedding-3-small",
    },
    "openai": {
        "base_url": None,  # SDK default
        "key_env": "OPENAI_API_KEY",
        "model": "text-embedding-3-small",
    },
}

_client = None
_model = None


def _get_client():
    """Lazy-init the OpenAI-compatible client."""
    global _client, _model
    if _client is not None:
        return _client

    provider = os.environ.get("EMBEDDINGS_PROVIDER", "gemini").lower()
    cfg = _PROVIDER_DEFAULTS.get(provider)
    if cfg is None:
        logger.warning("Unknown EMBEDDINGS_PROVIDER %r — embeddings unavailable", provider)
        return None

    api_key = os.environ.get(cfg["key_env"])
    if not api_key:
        logger.warning("%s not set — embeddings unavailable", cfg["key_env"])
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed — embeddings unavailable")
        return None

    try:
        kwargs = {"api_key": api_key}
        if cfg["base_url"]:
            kwargs["base_url"] = cfg["base_url"]
        _client = OpenAI(**kwargs)
        _model = os.environ.get("EMBEDDINGS_MODEL", cfg["model"])
        return _client
    except Exception as e:
        logger.warning("Failed to create embeddings client: %s", e)
        return None


def embed_text(text: str, dimensions: int = DEFAULT_DIMENSIONS) -> bytes | None:
    """
    Embed text via OpenAI text-embedding-3-small.

    Returns raw float32 bytes (len = dimensions * 4) for sqlite-vec,
    or None if embedding is unavailable.

    Args:
        text: The text to embed. 3-small handles up to 8192 tokens.
        dimensions: Output vector dimensions (1..1536, Matryoshka truncation).

    Returns:
        Raw float32 bytes suitable for sqlite-vec INSERT, or None.
    """
    client = _get_client()
    if client is None:
        return None

    text = text[:4000]

    try:
        response = client.embeddings.create(
            model=_model,
            input=text,
            dimensions=dimensions,
            encoding_format="float",
        )
        embedding = list(response.data[0].embedding)
        # L2-normalize so sqlite-vec L2 distance ranks identically to cosine.
        # Gemini truncated outputs ship pre-truncation-normalized; renormalize
        # to be safe across providers.
        norm = sum(x * x for x in embedding) ** 0.5
        if norm > 0:
            embedding = [x / norm for x in embedding]
        return struct.pack(f"{len(embedding)}f", *embedding)
    except Exception as e:
        logger.warning("Embedding failed: %s", e)
        return None


def embed_for_memory(title: str, summary: str = "", content: str = "") -> bytes | None:
    """
    Embed a memory's key fields for storage in vec_memories.

    Combines title + summary + content prefix into one text block,
    weighted toward title (appears first, most signal per token).
    """
    text = f"{title}. {summary}"
    if content:
        text += f" {content[:500]}"
    return embed_text(text)
