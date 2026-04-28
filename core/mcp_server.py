"""Memesis MCP server — stdio transport.

Exposes three read-only tools for agent memory retrieval:
  - search_memory: hybrid FTS + vector search over stored memories
  - get_memory: full hydration of a single memory by ID
  - recent_observations: recency-ordered live memories for a session

Usage (stdio, for ~/.claude.json mcpServers entry):
    /abs/path/to/.venv/bin/memesis-mcp

Security note (E3): All agent-originated writes MUST set source='agent'.
RetrievalEngine will eventually filter source='agent' rows from semantic
prior computation until they accumulate K independent retrievals (auto-promote).
This guard is designed now; enforced when write tools ship.

CRITICAL: Never print() to stdout — breaks JSON-RPC framing.
All debug output goes to stderr via logging.
"""

import logging
import sys

from mcp.server.fastmcp import FastMCP

# Route all debug output to stderr; stdout is reserved for JSON-RPC framing.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

mcp = FastMCP("memesis-memory")


@mcp.tool()
def search_memory(
    query: str,
    top_k: int = 10,
    tier: str | None = None,
) -> list[dict]:
    """Hybrid semantic + keyword search over stored memories.

    Returns ranked summaries (~50-100 tokens each).
    Call get_memory(id) for full content.

    Args:
        query: Natural-language search query.
        top_k: Maximum number of results to return (default 10).
        tier: Optional tier filter — one of 'T1', 'T2', 'T3', 'T4'.
            When supplied, only memories whose stage maps to this tier are
            returned.  None returns all tiers.
    """
    from core.retrieval import RetrievalEngine  # lazy — keeps cold-start <500ms
    from core.tiers import stage_to_tier  # lazy

    engine = RetrievalEngine()
    ranked = engine.hybrid_search(query=query, k=top_k)

    if not ranked:
        return []

    # Hydrate Memory objects for the summary shape
    from core.models import Memory  # lazy

    ranked_ids = [mid for mid, _ in ranked]
    memories_by_id = {
        m.id: m
        for m in Memory.select().where(Memory.id.in_(ranked_ids))
    }

    results: list[dict] = []
    for memory_id, rrf_score in ranked:
        memory = memories_by_id.get(memory_id)
        if memory is None:
            continue
        # Optional tier filter
        if tier is not None and stage_to_tier(memory.stage) != tier:
            continue
        results.append(
            {
                "id": memory.id,
                "title": memory.title,
                "summary": memory.summary,
                "stage": memory.stage,
                "rank": rrf_score,
            }
        )

    return results


@mcp.tool()
def get_memory(memory_id: str) -> dict:
    """Fetch full detail for a single memory by ID.

    Returns full content, tags, and provenance fields.
    Raises a tool error (isError: true) if the memory does not exist.

    Args:
        memory_id: UUID string of the memory record (from search_memory results).
    """
    from core.models import Memory  # lazy

    try:
        record = Memory.get_by_id(memory_id)
    except Memory.DoesNotExist:
        record = None

    if record is None:
        raise ValueError(f"Memory not found: {memory_id}")

    return {
        "id": record.id,
        "stage": record.stage,
        "title": record.title,
        "summary": record.summary,
        "content": record.content,
        "tags": record.tag_list,
        "importance": record.importance,
        "kind": record.kind,
        "knowledge_type": record.knowledge_type,
        "subject": record.subject,
        "project_context": record.project_context,
        "source_session": record.source_session,
        "cwd": record.cwd,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expires_at": record.expires_at,
        "source": record.source,
        "is_pinned": bool(record.is_pinned),
    }


@mcp.tool()
def recent_observations(session_id: str, limit: int = 10) -> list[dict]:
    """Return the most recently created live memories for a session, newest first.

    Filters by source_session matching session_id (the session that created the
    memory).  Only live (non-archived, non-expired) memories are returned.

    Args:
        session_id: The session identifier to filter by.
        limit: Maximum number of results (default 10).
    """
    from core.models import Memory  # lazy

    rows = list(
        Memory.live()
        .where(Memory.source_session == session_id)
        .order_by(Memory.created_at.desc())
        .limit(limit)
    )

    return [
        {
            "id": m.id,
            "stage": m.stage,
            "title": m.title,
            "summary": m.summary,
            "created_at": m.created_at,
            "importance": m.importance,
        }
        for m in rows
    ]


def main() -> None:
    """Entry point for the memesis-mcp console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
