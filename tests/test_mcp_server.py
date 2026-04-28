"""Tests for core/mcp_server.py — in-process FastMCP invocation.

Uses anyio.run() to drive async FastMCP.call_tool / list_tools without
requiring pytest-asyncio.  All tests are synchronous from pytest's perspective.

DB isolation via the tmp_path + init_db fixture pattern used throughout
the codebase.

Return-value convention: FastMCP.call_tool returns (list[ContentBlock], dict).
The structured dict has key "result" containing the serialized return value.
We use structured["result"] throughout — it is the reliable machine-readable path.
"""

import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import anyio
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import close_db, init_db
from core.models import Memory
from core.mcp_server import mcp
from mcp.server.fastmcp.exceptions import ToolError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base(tmp_path):
    """Isolated SQLite DB for each test."""
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _make_memory(
    *,
    stage: str = "consolidated",
    title: str = "Test memory",
    summary: str = "A test summary",
    content: str = "Some content",
    source_session: str | None = None,
    importance: float = 0.5,
) -> Memory:
    now = datetime.now().isoformat()
    return Memory.create(
        stage=stage,
        title=title,
        summary=summary,
        content=content,
        source_session=source_session,
        importance=importance,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_all_three_tools_registered():
    """Server must expose exactly the three tools: search_memory, get_memory,
    recent_observations."""

    async def _run():
        tools = await mcp.list_tools()
        return {t.name for t in tools}

    names = anyio.run(_run)
    assert "search_memory" in names
    assert "get_memory" in names
    assert "recent_observations" in names


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


def test_search_memory_calls_hybrid_search(base):
    """search_memory must delegate to RetrievalEngine.hybrid_search."""
    fake_results: list[tuple[str, float]] = []

    with patch("core.retrieval.RetrievalEngine") as MockEngine:
        mock_instance = MagicMock()
        mock_instance.hybrid_search.return_value = fake_results
        MockEngine.return_value = mock_instance

        async def _run():
            return await mcp.call_tool("search_memory", {"query": "anything"})

        anyio.run(_run)
        mock_instance.hybrid_search.assert_called_once()
        call_kwargs = mock_instance.hybrid_search.call_args
        # query may be positional or keyword depending on how the tool calls it
        assert (
            call_kwargs.kwargs.get("query") == "anything"
            or (call_kwargs.args and call_kwargs.args[0] == "anything")
        )


def test_search_memory_returns_summary_shape(base):
    """search_memory results must include id, title, summary, stage, rank."""
    _make_memory(title="Alpha", summary="Alpha summary", stage="consolidated")

    async def _run():
        return await mcp.call_tool("search_memory", {"query": "Alpha"})

    _blocks, structured = anyio.run(_run)
    items = structured["result"]
    assert isinstance(items, list)
    if items:
        item = items[0]
        assert "id" in item
        assert "title" in item
        assert "summary" in item
        assert "stage" in item
        assert "rank" in item


def test_search_memory_tier_filter(base):
    """Tier filter must exclude memories whose stage maps to a different tier."""
    # T3 = consolidated; T2 = crystallized
    _make_memory(title="Consolidated one", stage="consolidated")
    _make_memory(title="Crystallized one", stage="crystallized")

    async def _run():
        return await mcp.call_tool("search_memory", {"query": "one", "tier": "T2"})

    _blocks, structured = anyio.run(_run)
    items = structured["result"]
    assert isinstance(items, list)
    for item in items:
        assert item["stage"] == "crystallized", (
            f"Expected stage=crystallized for T2 filter, got {item['stage']!r}"
        )


# ---------------------------------------------------------------------------
# get_memory
# ---------------------------------------------------------------------------


def test_get_memory_returns_full_shape(base):
    """get_memory must return content, tags, and provenance fields."""
    import json as _json
    mem = _make_memory(title="Detail test", content="Full content here", stage="crystallized")

    async def _run():
        return await mcp.call_tool("get_memory", {"memory_id": mem.id})

    # get_memory returns dict → FastMCP returns list[ContentBlock] (no structured tuple)
    result = anyio.run(_run)
    blocks = result if isinstance(result, list) else result[0]
    data = _json.loads(blocks[0].text)

    assert data["id"] == mem.id
    assert data["title"] == "Detail test"
    assert data["content"] == "Full content here"
    assert data["stage"] == "crystallized"
    assert "tags" in data
    assert "created_at" in data
    assert "source" in data
    assert "expires_at" in data
    assert "is_pinned" in data


def test_get_memory_unknown_id_raises_tool_error(base):
    """get_memory with an unknown ID must raise ToolError, not leak raw exception."""

    async def _run():
        return await mcp.call_tool("get_memory", {"memory_id": "nonexistent-uuid-1234"})

    with pytest.raises(ToolError) as exc_info:
        anyio.run(_run)

    assert "Memory not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# recent_observations
# ---------------------------------------------------------------------------


def test_recent_observations_filters_by_session(base):
    """recent_observations must return only memories for the given session_id."""
    _make_memory(title="Session A mem", source_session="session-A")
    _make_memory(title="Session B mem", source_session="session-B")

    async def _run():
        return await mcp.call_tool("recent_observations", {"session_id": "session-A"})

    _blocks, structured = anyio.run(_run)
    items = structured["result"]
    assert len(items) == 1
    assert items[0]["title"] == "Session A mem"


def test_recent_observations_recency_order(base):
    """recent_observations must return newest first."""
    _make_memory(title="Older", source_session="sess-order")
    time.sleep(0.01)  # ensure distinct created_at strings
    _make_memory(title="Newer", source_session="sess-order")

    async def _run():
        return await mcp.call_tool(
            "recent_observations", {"session_id": "sess-order", "limit": 10}
        )

    _blocks, structured = anyio.run(_run)
    items = structured["result"]
    assert len(items) == 2
    assert items[0]["created_at"] >= items[1]["created_at"]


def test_recent_observations_limit(base):
    """recent_observations must respect the limit parameter."""
    for i in range(5):
        _make_memory(title=f"Obs {i}", source_session="sess-limit")

    async def _run():
        return await mcp.call_tool(
            "recent_observations", {"session_id": "sess-limit", "limit": 2}
        )

    _blocks, structured = anyio.run(_run)
    items = structured["result"]
    assert len(items) == 2


def test_recent_observations_excludes_archived(base):
    """recent_observations must not return archived memories."""
    mem = _make_memory(title="Archived mem", source_session="sess-arch")
    now = datetime.now().isoformat()
    Memory.update(archived_at=now).where(Memory.id == mem.id).execute()

    _make_memory(title="Live mem", source_session="sess-arch")

    async def _run():
        return await mcp.call_tool("recent_observations", {"session_id": "sess-arch"})

    _blocks, structured = anyio.run(_run)
    items = structured["result"]
    titles = [i["title"] for i in items]
    assert "Archived mem" not in titles
    assert "Live mem" in titles
