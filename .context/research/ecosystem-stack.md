# Research: Python 3.10+ Ecosystem Stack

**Confidence:** HIGH (SQLite/WAL: official docs; FTS5: official docs; Anthropic SDK: Context7 + official; pytest: Context7 + official; atomic writes: official Python docs + codebase observation)
**Date:** 2026-03-28

**Sources:**
- https://www.sqlite.org/wal.html
- https://www.sqlite.org/fts5.html
- https://context7.com/anthropics/anthropic-sdk-python (HIGH reputation)
- https://context7.com/pytest-dev/pytest (HIGH reputation)
- https://context7.com/pytest-dev/pytest-asyncio (HIGH reputation)
- https://docs.python.org/3/library/tempfile.html
- Codebase: `/Users/emma.hyde/projects/memesis/core/storage.py`
- Codebase: `/Users/emma.hyde/projects/memesis/tests/conftest.py`

---

## 1. SQLite WAL Mode — Concurrency Patterns

### Enabling and Configuration

WAL mode is persistent once set — it survives connection restarts.

```python
import sqlite3

conn = sqlite3.connect("app.db")
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")   # Fewer fsyncs; safe with WAL
conn.execute("PRAGMA busy_timeout=5000")    # 5 s before raising OperationalError
conn.execute("PRAGMA wal_autocheckpoint=1000")  # Checkpoint after ~4 MB
```

`synchronous=NORMAL` is safe with WAL because durability is provided by the checkpoint fsync, not by every write. This is a meaningful throughput improvement over the default `FULL`.

### Concurrency Model

| Scenario | WAL behaviour |
|---|---|
| Multiple simultaneous readers | Fully concurrent — readers never block each other |
| Reader + active writer | Both proceed concurrently |
| Two simultaneous writers | Second writer blocks (single WAL file) |
| Reader blocking checkpoint | Checkpoint cannot complete until reader releases |

The main concurrency risk is **checkpoint starvation**: a long-running read transaction prevents the WAL from being truncated, causing unbounded file growth. Mitigate by:
- Keeping read transactions short
- Running `PRAGMA wal_checkpoint(TRUNCATE)` on explicit close/shutdown
- Using `busy_timeout` so writers back off gracefully

### Shutdown / Close Pattern

The codebase already implements the correct close pattern — checkpoint on explicit `close()` and again in `__del__`:

```python
def close(self) -> None:
    try:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass
```

`TRUNCATE` is preferred over `PASSIVE` when the goal is to reclaim disk space at shutdown. `PASSIVE` is preferred during normal operation (non-blocking).

### WAL Limitations

- **No network filesystems.** WAL requires shared-memory (`-shm` file); NFS/SMB/cloud storage will corrupt. Local disk only.
- **Page size is fixed** once WAL mode is enabled.
- **Transactions >1 GB** risk I/O errors. Not a concern for this codebase.
- **Read-only mounts** require write access to the `-shm` file (or a directory it can create one in). Relaxed in SQLite 3.22.0+.

### SQLITE_BUSY Handling

```python
import sqlite3
import time

def execute_with_retry(conn, sql, params=(), max_attempts=3):
    """Retry on SQLITE_BUSY with exponential backoff."""
    for attempt in range(max_attempts):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_attempts - 1:
                time.sleep(0.1 * (2 ** attempt))
                continue
            raise
```

Prefer `busy_timeout` (via PRAGMA) over manual retry for most cases — it handles the busy spin inside SQLite's C layer. Manual retry is appropriate only when you need custom backoff or circuit-breaker logic.

---

## 2. SQLite FTS5 — Ranking and Tokenization

### Tokenizer Selection

| Tokenizer | Best for | Notes |
|---|---|---|
| `unicode61` (default) | General multilingual text | Strips diacritics; respects Unicode 6.1 separators |
| `ascii` | ASCII-only content, code snippets | Faster; treats all non-ASCII as token chars |
| `porter` | English prose where stemming helps recall | Wraps another tokenizer; query `run` matches `running` |
| `trigram` | Substring/LIKE/GLOB queries | Each 3-char sequence is indexed; larger index |

For this codebase (English markdown memories), `unicode61` is the right default. Add `porter` as a wrapper if fuzzy recall matters more than precision:

```sql
CREATE VIRTUAL TABLE memories_fts USING fts5(
    title, summary, tags, content,
    content='memories',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 1'
);
```

The `content=` and `content_rowid=` options create a **content table** — the FTS index stores only the inverted index, not the text, keeping the DB compact. The tradeoff is that the index must be manually kept in sync with the content table (which `storage.py` already does via `_fts_insert` / `_fts_delete`).

### Ranking with bm25()

`rank` in FTS5 queries maps directly to `bm25()`. Lower is better (it returns a negative value scaled by match quality). The existing query pattern is correct:

```sql
SELECT m.*, rank
FROM memories_fts
JOIN memories m ON memories_fts.rowid = m.rowid
WHERE memories_fts MATCH ?
ORDER BY rank          -- ascending = best matches first
LIMIT ?
```

**Column weighting** lets you boost title/tag matches over body matches:

```sql
ORDER BY bm25(memories_fts, 10.0, 5.0, 3.0, 1.0)
-- weights:              title  summary  tags  content
```

### Auxiliary Functions

```sql
-- Highlighted snippet (good for UI display)
SELECT snippet(memories_fts, 3, '<b>', '</b>', '…', 20)
FROM memories_fts WHERE memories_fts MATCH ?;

-- Highlight full column
SELECT highlight(memories_fts, 0, '[', ']')
FROM memories_fts WHERE memories_fts MATCH ?;
```

### Optimization After Bulk Inserts

```sql
INSERT INTO memories_fts(memories_fts) VALUES('optimize');
```

Run this after bulk loading (e.g., a full re-index). It merges internal b-tree segments and reduces query latency.

### detail Level Tradeoffs

| detail | Index size | NEAR/phrase queries |
|---|---|---|
| `full` (default) | 100% | Supported |
| `column` | ~50% | No position-sensitive queries |
| `none` | ~18% | Only term existence |

For this use case (semantic relevance, not exact phrase matching), `detail=column` is a worthwhile optimization if index size becomes a concern.

### FTS5 Content Table Sync — Known Gap

The current schema uses `content='memories'` but does **not** have database triggers keeping FTS in sync automatically. Instead, sync is done manually in Python (`_fts_insert`, `_fts_delete`). This is correct and intentional (triggers require `rowid` access that standard Python sqlite3 builds may not expose cleanly), but means any direct SQL writes to `memories` (e.g., in migrations or scripts) that bypass the Python layer will leave FTS stale. Document this constraint or add a `rebuild` helper:

```python
def rebuild_fts(self) -> None:
    """Rebuild FTS index from scratch — use after bulk migrations."""
    with sqlite3.connect(self.db_path) as conn:
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        conn.commit()
```

---

## 3. Anthropic Python SDK — Async Patterns and Error Handling

### Client Instantiation

| Client | Use case |
|---|---|
| `anthropic.Anthropic()` | Synchronous (scripts, hooks, cron jobs) |
| `anthropic.AsyncAnthropic()` | Async applications, concurrent requests |
| `anthropic.AnthropicBedrock()` | AWS Bedrock routing (as used in this codebase) |

The existing codebase uses synchronous `Anthropic()` / `AnthropicBedrock()`. This is correct for the hook/cron context where calls are sequential and there is no event loop.

### Async Pattern (for future async consumers)

```python
import asyncio
from anthropic import AsyncAnthropic

client = AsyncAnthropic()  # Reads ANTHROPIC_API_KEY from env

async def call_claude(prompt: str) -> str:
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text

asyncio.run(call_claude("Hello"))
```

For streaming (token-by-token output, long generations):

```python
async def stream_claude(prompt: str) -> str:
    async with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for text in stream.text_stream:
            print(text, end="", flush=True)
    final = await stream.get_final_message()
    return final.usage.output_tokens
```

### Error Handling — Full Hierarchy

```python
from anthropic import (
    Anthropic,
    APIError,            # Base for all API errors
    APIConnectionError,  # Network-level failure
    RateLimitError,      # 429 — back off and retry
    APIStatusError,      # 4xx/5xx not otherwise classified
    AuthenticationError, # 401 — bad or missing API key
    BadRequestError,     # 400 — invalid request parameters
)

def call_with_handling(client, prompt: str) -> str | None:
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except AuthenticationError:
        raise  # Configuration error; don't retry
    except RateLimitError:
        raise  # Caller should back off; SDK auto-retries by default
    except APIConnectionError as e:
        # Transient network failure; SDK will retry automatically
        raise
    except BadRequestError as e:
        # Prompt too long, bad parameters, etc — not retriable
        raise ValueError(f"Bad request: {e.message}") from e
    except APIStatusError as e:
        raise RuntimeError(f"API error {e.status_code}: {e.message}") from e
```

The SDK performs **automatic retries** with exponential backoff for transient errors (429, 5xx, network errors) by default. You can configure retry count:

```python
client = Anthropic(max_retries=3)  # default is 2
```

### Retry and Timeout Configuration

```python
import httpx
from anthropic import Anthropic, DefaultHttpxClient

client = Anthropic(
    timeout=httpx.Timeout(60.0, connect=5.0),  # 60s read, 5s connect
    max_retries=3,
    http_client=DefaultHttpxClient(
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
    ),
)
```

### JSON Response Extraction Pattern

The codebase's `_parse_decisions` / retry-on-malformed-JSON pattern is a documented production pattern. Codify it as a reusable utility:

```python
import json
import re

def extract_json(text: str) -> dict | list:
    """Strip optional markdown fences and parse JSON."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*\n", "", text)
    text = re.sub(r"\n```$", "", text.strip())
    return json.loads(text)
```

---

## 4. pytest — Fixtures and Database Testing

### Fixture Scope Strategy

| Scope | When to use | Notes |
|---|---|---|
| `function` (default) | Per-test isolation; mutating state | Most tests; safe default |
| `module` | Shared read-only setup within a file | Schema creation, seeding |
| `session` | Expensive one-time setup | Test infrastructure, external services |

The codebase uses `function`-scoped `memory_store` — correct because tests write to the store and need isolation.

### Recommended DB Test Fixtures

```python
# conftest.py
import shutil
import tempfile
from pathlib import Path
import pytest
from core.storage import MemoryStore

@pytest.fixture
def temp_dir(tmp_path):
    """Use pytest's built-in tmp_path — cleaner than mkdtemp."""
    return tmp_path

@pytest.fixture
def memory_store(tmp_path):
    """Isolated MemoryStore per test. WAL checkpoint on teardown."""
    store = MemoryStore(base_dir=str(tmp_path))
    yield store
    store.close()  # Checkpoint WAL, release file handles

@pytest.fixture(scope="session")
def read_only_store(tmp_path_factory):
    """Session-scoped store for read-only tests — faster when seeding is expensive."""
    base = tmp_path_factory.mktemp("readonly_store")
    store = MemoryStore(base_dir=str(base))
    # Seed once here
    store.create("seed.md", "Seed content", {"stage": "consolidated", "title": "Seed"})
    yield store
    store.close()
```

**Why `tmp_path` over `tempfile.mkdtemp`:** `tmp_path` is managed by pytest (auto-cleanup, unique per test, pathlib.Path already), reducing boilerplate. The existing conftest uses `mkdtemp` manually — migrating to `tmp_path` removes the manual `shutil.rmtree` in the fixture.

### Parameterization Patterns

```python
import pytest

# Simple parametrize
@pytest.mark.parametrize("stage", ["ephemeral", "consolidated", "crystallized", "instinctive"])
def test_create_in_stage(memory_store, stage):
    mid = memory_store.create("test.md", "content", {"stage": stage, "title": "T"})
    result = memory_store.get(mid)
    assert result["stage"] == stage

# Indirect parametrize — fixture receives param
@pytest.fixture
def staged_store(request, tmp_path):
    store = MemoryStore(base_dir=str(tmp_path))
    store.create("seed.md", "content", {"stage": request.param, "title": "Seed"})
    yield store
    store.close()

@pytest.mark.parametrize("staged_store", ["ephemeral", "consolidated"], indirect=True)
def test_list_by_stage(staged_store):
    results = staged_store.list_by_stage("ephemeral")
    # ...

# ID-labelled params for readable test names
@pytest.mark.parametrize("query,expected_count", [
    pytest.param("python", 1, id="single-term"),
    pytest.param("python AND memory", 1, id="and-query"),
    pytest.param("nonexistent_xyzzy", 0, id="no-match"),
])
def test_fts_search(memory_store, query, expected_count):
    ...
```

### Async Test Pattern (pytest-asyncio)

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

```python
import pytest_asyncio
from anthropic import AsyncAnthropic

@pytest_asyncio.fixture
async def async_client():
    client = AsyncAnthropic()
    yield client
    await client.close()

async def test_async_call(async_client):
    # With asyncio_mode=auto, no @pytest.mark.asyncio needed
    response = await async_client.messages.create(...)
    assert response.content
```

### Mocking Anthropic in Tests

The existing conftest strips `CLAUDE_CODE_USE_BEDROCK` to prevent accidental real API calls. For unit tests that exercise LLM-calling code paths, use `unittest.mock`:

```python
from unittest.mock import MagicMock, patch

def test_consolidate_calls_llm(memory_store):
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"decisions": []}')]

    with patch("anthropic.Anthropic.messages") as mock_messages:
        mock_messages.create.return_value = mock_response
        # test Consolidator...
```

Or use `pytest-httpx` (already resolvable via Context7) to intercept at the HTTP layer for more realistic tests.

### Test Isolation for WAL Databases

Key: always call `store.close()` in fixture teardown. Without it:
- `-wal` and `-shm` files accumulate in `tmp_path`
- On macOS, open file handles may prevent `tmp_path` cleanup
- Under heavy parallel test runs, EMFILE (too many open files) can occur

The existing `conftest.py` already calls `store.close()` — maintain this pattern.

---

## 5. Atomic File Writes in Python

### The Problem

`Path.write_text()` and `open(..., 'w')` are **not atomic**. A crash mid-write leaves a truncated or corrupt file. For memory files (markdown), a corrupt write could silently lose data.

### Canonical Pattern: mkstemp + os.replace

This is what the codebase uses, and it is the correct approach:

```python
import os
import tempfile

def atomic_write(target_path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to target_path atomically via tmp file + rename."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=target_path.parent,  # Same filesystem — guarantees atomic rename
        suffix=".tmp",
    )
    try:
        os.write(tmp_fd, content.encode(encoding))
        os.close(tmp_fd)
        os.replace(tmp_path, target_path)  # Atomic on POSIX; best-effort on Windows
    except Exception:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

Critical requirements:
1. **`dir=target_path.parent`** — temp file must be on the same filesystem as the target. Cross-device renames are not atomic.
2. **`os.replace()`** — POSIX-atomic (rename syscall). Overwrites the destination in a single syscall. `shutil.move()` works but calls `os.rename` internally, which also works on same-filesystem — the codebase's use of `shutil.move` is functionally equivalent on POSIX.
3. **Always `os.close(tmp_fd)` before `os.replace()`** — the file descriptor must be closed first to flush OS buffers.

### Python 3.12+ Alternative (delete_on_close)

```python
import tempfile, os

def atomic_write_312(target_path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=target_path.parent,
        suffix=".tmp",
        delete_on_close=False,   # Python 3.12+
        encoding="utf-8",
    ) as f:
        f.write(content)
        temp_name = f.name
    os.replace(temp_name, target_path)
```

`delete_on_close=False` means the file persists after `close()` within the `with` block, enabling the subsequent `os.replace`. The file is still deleted if the context manager exits abnormally without reaching `os.replace`.

### Codebase Note

The current `storage.py` implementation writes bytes via the `tmp_fd` file descriptor (`os.write(tmp_fd, content.encode('utf-8'))`), then uses `shutil.move`. This is correct. One minor improvement: add `os.fsync(tmp_fd)` before `os.close(tmp_fd)` if durability against power loss matters:

```python
os.write(tmp_fd, content.encode('utf-8'))
os.fsync(tmp_fd)   # Flush kernel buffers to disk
os.close(tmp_fd)
os.replace(tmp_path, target_path)
```

`os.fsync` has a performance cost (~1–5 ms per write on SSD). For memory files where data loss at the granularity of a power failure is acceptable, it can be omitted. For instinctive/crystallized memories that are hard to regenerate, `fsync` is advisable.

### Markdown File I/O Best Practices

- Always specify `encoding="utf-8"` explicitly — do not rely on locale-default encoding (varies on Windows).
- Use `pathlib.Path` throughout; avoid mixing `str`-path and `Path` operations.
- For reading, `Path.read_text(encoding="utf-8")` is fine (reads are inherently atomic at the OS page-cache level for normal file sizes).
- Store files in the **same directory as the target** when creating temp files, not in `/tmp`, to guarantee same-filesystem atomicity.

---

## 6. Cross-Cutting Patterns and Gaps

### Connection-Per-Operation vs. Connection Pool

The current pattern (`with sqlite3.connect(...)`) opens a new connection per operation. This is safe and correct for WAL mode (each connection gets a consistent snapshot), but has overhead for high-frequency write paths. If write throughput becomes a bottleneck, consider a thread-local connection pool:

```python
import threading

_local = threading.local()

def _get_conn(db_path: Path) -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(db_path, check_same_thread=False)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn
```

This is only needed if profiling shows connection overhead. For the current use case (hooks run infrequently), per-operation connections are appropriate.

### Missing: pytest.ini / pyproject.toml Test Config

The project does not have `[tool.pytest.ini_options]` in `pyproject.toml`. Recommended additions:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
# If using pytest-asyncio in future:
# asyncio_mode = "auto"
```

### Missing: FTS Rebuild Utility

Direct SQL writes (migrations, seed scripts) bypass `_fts_insert`. Add a `rebuild_fts()` method that issues `INSERT INTO memories_fts(memories_fts) VALUES('rebuild')` to resync from the content table. FTS5's `rebuild` command re-reads from `content='memories'` and regenerates the entire index.

### Missing: Explicit busy_timeout

`storage.py` enables WAL mode and `synchronous=NORMAL` but does not set `busy_timeout`. Under concurrent test runs or if a cron job and a hook overlap, writes without a timeout will immediately raise `OperationalError: database is locked`. Add:

```python
conn.execute("PRAGMA busy_timeout=5000")
```

to `_init_db()`.

**Gaps:**
- FTS5 porter stemmer impact on recall has not been empirically measured for this codebase's actual memory content.
- Anthropic SDK `max_retries` and `timeout` are not explicitly configured in `consolidator.py` — relies on SDK defaults (2 retries, 10 min timeout).
- No benchmark exists for `detail=column` vs `detail=full` index size on real data.
- `pytest-asyncio` and async test patterns are not yet needed but should be added to `dev` deps if async Anthropic calls are introduced.
