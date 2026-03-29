"""
Embedding service — wraps AWS Bedrock Titan Text Embeddings v2.

Computes text embeddings via API and returns raw float32 bytes ready
for storage in sqlite-vec's vec0 virtual table.

The embedding is computed once at memory creation time and persisted
in the vec_memories table. Query-time embedding uses the same function.
"""

import json
import logging
import os
import struct

logger = logging.getLogger(__name__)

# Titan v2 supports 256, 512, or 1024 dimensions.
# 512 is a good tradeoff: half the storage of 1024, minimal quality loss.
DEFAULT_DIMENSIONS = 512

_client = None


def _get_bedrock_client():
    """Lazy-init the Bedrock runtime client."""
    global _client
    if _client is not None:
        return _client

    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not installed — embeddings unavailable")
        return None

    region = os.environ.get("AWS_REGION", "us-west-2")
    profile = os.environ.get("AWS_PROFILE", "bedrock-users")

    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        _client = session.client("bedrock-runtime")
        return _client
    except Exception as e:
        logger.warning("Failed to create Bedrock client: %s", e)
        return None


def embed_text(text: str, dimensions: int = DEFAULT_DIMENSIONS) -> bytes | None:
    """
    Embed text via Bedrock Titan Text Embeddings v2.

    Returns raw float32 bytes (len = dimensions * 4) for sqlite-vec,
    or None if embedding is unavailable.

    Args:
        text: The text to embed. Titan v2 supports up to 8192 tokens.
        dimensions: Output vector dimensions (256, 512, or 1024).

    Returns:
        Raw float32 bytes suitable for sqlite-vec INSERT, or None.
    """
    client = _get_bedrock_client()
    if client is None:
        return None

    # Truncate to reasonable size — Titan v2 handles 8192 tokens,
    # but we cap text to avoid sending huge payloads for long content.
    text = text[:4000]

    try:
        response = client.invoke_model(
            modelId="amazon.titan-embed-text-v2:0",
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "inputText": text,
                "dimensions": dimensions,
                "normalize": True,  # unit-length → L2 distance = cosine ranking
            }),
        )
        body = json.loads(response["body"].read())
        embedding = body["embedding"]
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
        # Include content prefix for richer signal
        text += f" {content[:500]}"
    return embed_text(text)
