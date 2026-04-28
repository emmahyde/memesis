# Research: MCP Python SDK — stdio Server Implementation Guide

**Confidence:** HIGH (questions 1–4, 6, 8) / MEDIUM (questions 5, 7)
**SDK version researched:** `mcp` 1.7.1–1.8.0 (PyPI), spec 2025-11-25
**Sources:**
- [Official MCP build-server guide](https://modelcontextprotocol.io/docs/develop/build-server)
- [MCP Tools specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [mcp PyPI](https://pypi.org/project/mcp/)
- [python-sdk GitHub](https://github.com/modelcontextprotocol/python-sdk)
- [FastMCP tools docs](https://gofastmcp.com/servers/tools)
- [FastMCP testing docs](https://gofastmcp.com/development/tests)
- [SDK issue #1252 — unit testing](https://github.com/modelcontextprotocol/python-sdk/issues/1252)
- [SDK issue #1839 — sync tool / asyncio interaction](https://github.com/modelcontextprotocol/python-sdk/issues/1839)
- [MCP cold-start optimization](https://fast.io/resources/mcp-server-cold-start-optimization/)

---

## 1. Server Boilerplate — FastMCP vs low-level Server

**Use FastMCP. It is the official preferred interface** (absorbed into the SDK in late 2024; the low-level `Server` class is the implementation substrate, not the authoring surface).

FastMCP derives tool schemas from type hints and docstrings automatically, handles both sync and async handlers, and requires roughly 10 lines of boilerplate.

```python
# core/mcp_server.py
import sys
import logging
from mcp.server.fastmcp import FastMCP

# CRITICAL for stdio: never print() to stdout — use stderr or logging
logging.basicConfig(stream=sys.stderr, level=logging.INFO)

mcp = FastMCP("memesis-memory")

# --- tool registrations below ---

def main() -> None:
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
```

**Confidence: HIGH** — pattern from official docs, confirmed in multiple sources.

---

## 2. Tool Registration + Schema Derivation

FastMCP inspects function signatures and type annotations to generate JSONSchema automatically. Pydantic models are supported for complex inputs. You do **not** hand-write JSONSchema.

Both `def` (sync) and `async def` are accepted by `@mcp.tool()`. The decorator handles wrapping.

```python
from typing import Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memesis-memory")


@mcp.tool()
def search_memory(
    query: str,
    top_k: int = 10,
    min_score: float = 0.0,
) -> list[dict]:
    """Search memory by semantic + keyword hybrid.

    Args:
        query: Natural-language search query.
        top_k: Maximum number of results (default 10).
        min_score: Minimum relevance score 0.0–1.0 (default 0).
    """
    from core.retrieval import RetrievalEngine  # lazy import
    engine = RetrievalEngine()
    return engine.hybrid_search(query, top_k=top_k, min_score=min_score)


@mcp.tool()
def get_memory(memory_id: str) -> dict:
    """Fetch full detail for a single memory by ID.

    Args:
        memory_id: UUID string of the memory record.
    """
    from core.models import Memory  # lazy import
    record = Memory.get_by_id(memory_id)
    if record is None:
        raise ValueError(f"Memory {memory_id!r} not found")
    return record.to_dict()


@mcp.tool()
def recent_observations(limit: int = 20) -> list[dict]:
    """Return the most recently created observations, newest first.

    Args:
        limit: How many to return (default 20, max 100).
    """
    from core.models import Memory  # lazy import
    limit = min(limit, 100)
    return [m.to_dict() for m in Memory.recent(limit)]
```

**Schema inference:** `str`, `int`, `float`, `bool`, `list[dict]`, `Optional[X]`, Pydantic `BaseModel` subclasses all work. The docstring's `Args:` section populates field descriptions in the generated schema. No hand-written JSONSchema needed.

**Confidence: HIGH** — confirmed by official FastMCP docs and multiple independent sources.

---

## 3. Return Shape

**For `search_memory` (ranked summaries) and `get_memory` (full detail):**

The simplest and most agent-readable approach is returning plain Python values (`str`, `dict`, `list`). FastMCP serializes them to `TextContent` blocks automatically, with the value JSON-encoded as the text payload.

```python
# Returning a plain dict → agent sees JSON string in TextContent
@mcp.tool()
def get_memory(memory_id: str) -> dict:
    ...
    return {"id": memory_id, "content": "...", "score": 0.9, ...}
```

**Protocol-level result shape** (what the client actually receives):
```json
{
  "content": [{"type": "text", "text": "{\"id\": \"...\", \"content\": \"...\"}"}],
  "isError": false
}
```

**For progressive disclosure specifically:**
- `search_memory`: return `list[dict]` where each dict has `id`, `summary` (~50 tokens), `score`. Agent can call `get_memory(id)` for full detail.
- `get_memory`: return `dict` with all fields. No truncation needed.

**You can also return `str`** (markdown, formatted text) — FastMCP wraps it as-is in `TextContent`. This is fine for human-readable output but harder for agents to parse programmatically. Prefer returning `dict`/`list[dict]` for machine consumption.

**For maximum spec compliance** (structured + text for backwards compat), you can return a tuple:
```python
return {"id": ..., "content": ...}, [TextContent(type="text", text=json.dumps(result))]
```
But this is only needed if clients must consume both `structuredContent` and `content`. For Claude Code usage, plain dict return is sufficient.

**Confidence: HIGH** — confirmed by MCP spec 2025-11-25 and SDK behavior.

---

## 4. Entry Point

**Invocation options (choose one):**

### A. Direct script (simplest)
```bash
python /abs/path/to/memesis/core/mcp_server.py
```
`~/.claude.json` / `claude_desktop_config.json` entry:
```json
{
  "mcpServers": {
    "memesis": {
      "command": "/abs/path/to/memesis/.venv/bin/python",
      "args": ["/abs/path/to/memesis/core/mcp_server.py"],
      "env": {
        "PYTHONPATH": "/abs/path/to/memesis"
      }
    }
  }
}
```

### B. uv run (recommended if project uses uv)
```json
{
  "mcpServers": {
    "memesis": {
      "command": "/abs/path/to/uv",
      "args": ["--directory", "/abs/path/to/memesis", "run", "python", "core/mcp_server.py"]
    }
  }
}
```

### C. Console script via pyproject.toml (cleanest for distributed use)
```toml
[project.scripts]
memesis-mcp = "core.mcp_server:main"
```
Then in config:
```json
{
  "mcpServers": {
    "memesis": {
      "command": "/abs/path/to/memesis/.venv/bin/memesis-mcp"
    }
  }
}
```

**Key rule:** Use absolute paths everywhere. Claude Code does not inherit your shell PATH. `which uv` / `which python` to confirm binary paths.

**For Claude Code specifically**, the config lives at `~/.claude.json` (not claude_desktop_config.json). The `mcpServers` key shape is identical.

**Confidence: HIGH** — from official docs and confirmed Claude Code MCP docs.

---

## 5. Cold-Start Performance

**SDK init cost: ~250–600ms on Python 3.12.** This is per-spawn (stdio servers are spawned fresh per session by Claude Code, not kept alive).

**Sources of latency to watch:**
1. `import mcp` itself — moderate cost, ~100ms
2. Your heavy imports: `peewee`, `apsw`, `sqlite-vec`, `RetrievalEngine` — can add 200–400ms
3. Database connection open at import time

**Mitigation: lazy imports inside tool functions** (already shown in the code shapes above). Import `RetrievalEngine` and `Memory` inside the tool body, not at module top. Python's import cache means the second call is free.

```python
# Module top — lean
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("memesis-memory")

# Inside tool — lazy, cached after first call
@mcp.tool()
def search_memory(query: str) -> list[dict]:
    from core.retrieval import RetrievalEngine  # imported on first tool call only
    ...
```

**Expected result:** With lazy imports, cold-start to first tool response should be comfortably under 500ms.

**Confidence: MEDIUM** — general guidance confirmed, exact numbers not benchmarked for this specific stack.

---

## 6. Error Handling

**Two mechanisms, use both correctly:**

**A. Tool execution errors (agent-recoverable) — raise exceptions, FastMCP handles them:**
```python
@mcp.tool()
def get_memory(memory_id: str) -> dict:
    record = Memory.get_by_id(memory_id)
    if record is None:
        raise ValueError(f"Memory {memory_id!r} not found")  # → isError: true in response
    return record.to_dict()
```
FastMCP catches exceptions from tool functions and converts them to `CallToolResult` with `isError: true` and the exception message as `TextContent`. The agent sees a recoverable error and can self-correct (e.g., try a different ID).

**B. Protocol errors (malformed requests) — do not catch, let them propagate as JSON-RPC errors.**

**Do NOT:**
- Print to stdout (breaks JSON-RPC framing)
- Return a dict with an `"error"` key and `isError: false` (agent won't know it's an error)
- Catch all exceptions and swallow them (agent gets wrong success signal)

**Do:**
```python
# Good: raise; FastMCP converts to isError: true
raise ValueError("explanation the agent can act on")

# Also acceptable for explicit control:
from mcp.types import CallToolResult, TextContent
return CallToolResult(
    content=[TextContent(type="text", text="error message")],
    isError=True
)
```

**Confidence: HIGH** — from MCP spec 2025-11-25 and SDK issue #396/#2153.

---

## 7. Testing Patterns

**FastMCP supports in-process testing via `Client(server_instance)` — no subprocess needed.**

However, `Client` is async-only. You need `anyio.run()` or `pytest-anyio`/`pytest-asyncio` to drive the test coroutines. This is the one unavoidable async seam.

**Option A: pytest-anyio (minimal dependency, no pytest-asyncio)**
```python
# tests/test_mcp_server.py
import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp import Client  # or: from fastmcp import Client

# import your server's mcp instance (or re-create in tests)
from core.mcp_server import mcp


@pytest.mark.anyio
async def test_recent_observations_returns_list():
    async with Client(mcp) as client:
        result = await client.call_tool("recent_observations", {"limit": 5})
        data = result[0].text  # JSON string
        import json
        items = json.loads(data)
        assert isinstance(items, list)
        assert len(items) <= 5
```

**Option B: test the underlying functions directly (zero async overhead)**

Since your actual logic lives in `RetrievalEngine.hybrid_search` and `Memory.get_by_id`, you can unit-test those directly without going through MCP at all. The MCP layer is just thin wrappers.

```python
# tests/test_retrieval.py — no async, no mcp dependency
def test_hybrid_search_returns_ranked_results():
    engine = RetrievalEngine(db_path=":memory:")
    results = engine.hybrid_search("test query", top_k=3)
    assert len(results) <= 3
```

**Recommendation for your codebase:** Use Option B for business logic coverage. Use Option A only for integration tests that verify the MCP wire format (schema, error propagation). Add `anyio` as a test dependency; it's lightweight and does not force `pytest-asyncio` patterns everywhere.

**Confidence: MEDIUM** — in-process Client pattern confirmed in search results and SDK issues; exact `anyio` fixture syntax may vary by version.

---

## 8. Pitfalls

| Pitfall | Detail | Fix |
|---------|--------|-----|
| **stdout pollution** | Any `print()` to stdout breaks JSON-RPC framing silently | Use `logging` to stderr; `print(..., file=sys.stderr)` |
| **Sync tools block the event loop** | FastMCP runs sync tool functions in the async loop. If `hybrid_search` does blocking I/O (SQLite reads), it can stall other requests | Fine for single-agent use; for concurrency wrap with `anyio.to_thread.run_sync(fn)` |
| **Python version** | SDK requires **Python 3.10+** (confirmed in official docs). `mcp` 1.2.0+ required for stdio. | You're on 3.10+ — no issue |
| **Forced async runtime** | `mcp.run()` uses `anyio` internally. Your tool *functions* can be plain `def`, but the server loop is always async. This does NOT require async in your business logic. | Write sync `def` tools; only the test Client calls are async |
| **Import ordering** | Importing `RetrievalEngine` at module top means database opens at server start, adding 100–400ms per spawn | Lazy-import inside each tool function |
| **Absolute paths in config** | Relative paths in `mcpServers` `args` fail silently — Claude spawns from an unknown cwd | Always use absolute paths; resolve with `pathlib.Path(__file__).parent` |
| **Version pinning** | The SDK has shipped breaking patches inside minor versions. Pin exactly: `mcp[cli]==1.8.0` or whatever current version. | Pin in `pyproject.toml` or `requirements.txt` |
| **`anyio` vs `asyncio` in tests** | If you use `asyncio.run()` directly in a test that already has an event loop (e.g., under pytest-asyncio), you get a `RuntimeError`. | Use `anyio.run()` or `pytest-anyio` markers |
| **FastMCP 2.0 (jlowin/fastmcp) confusion** | There is a third-party `fastmcp` package on PyPI (PrefectHQ/jlowin) that is separate from the SDK's `mcp.server.fastmcp`. The latter is what you want. | Import from `mcp.server.fastmcp`, not `fastmcp` |

---

## Recommended `core/mcp_server.py` Shape

```python
"""Memesis MCP server — stdio transport.

Usage:
    python core/mcp_server.py
    # or via mcpServers config with absolute path to this file
"""
import sys
import logging

from mcp.server.fastmcp import FastMCP

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

mcp = FastMCP("memesis-memory")


@mcp.tool()
def search_memory(query: str, top_k: int = 10, min_score: float = 0.0) -> list[dict]:
    """Hybrid semantic + keyword search over stored memories.

    Returns ranked summaries. Call get_memory(id) for full content.

    Args:
        query: Natural-language search query.
        top_k: Max results to return (default 10).
        min_score: Minimum relevance threshold 0.0–1.0.
    """
    from core.retrieval import RetrievalEngine
    return RetrievalEngine().hybrid_search(query, top_k=top_k, min_score=min_score)


@mcp.tool()
def get_memory(memory_id: str) -> dict:
    """Fetch full detail for a single memory by ID.

    Args:
        memory_id: UUID of the memory record (from search_memory results).
    """
    from core.models import Memory
    record = Memory.get_by_id(memory_id)
    if record is None:
        raise ValueError(f"Memory {memory_id!r} not found")
    return record.to_dict()


@mcp.tool()
def recent_observations(limit: int = 20) -> list[dict]:
    """Return the N most recently created observations, newest first.

    Args:
        limit: Number to return (default 20, capped at 100).
    """
    from core.models import Memory
    return [m.to_dict() for m in Memory.recent(min(limit, 100))]


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

**Gaps:**
- Exact in-process `Client` fixture API not verified against current `mcp` 1.8.0 (confirmed pattern, exact import path may be `from mcp import Client` or require `from mcp.client.fastmcp import FastMCPTransport`)
- Cold-start numbers for this specific stack (peewee + apsw + sqlite-vec) not benchmarked
- `structuredContent` field support varies by Claude Code version; plain `TextContent` with JSON string is the safe default
