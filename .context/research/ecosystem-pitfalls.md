# Research: Python 3.10+ Ecosystem Pitfalls

**Confidence:** HIGH (SQLite/WAL, Anthropic SDK, rate limits — official docs + Context7); MEDIUM (pytest fixture scoping — Context7); MEDIUM (pyproject.toml — PEP 517/621 primary sources, Cloudflare blocked setuptools docs); MEDIUM (TOCTOU — well-established OS security literature, codebase review)

**Sources:**
- SQLite WAL: https://www.sqlite.org/wal.html
- SQLite FTS5: https://www.sqlite.org/fts5.html
- SQLite threading: https://docs.python.org/3/library/sqlite3.html
- Anthropic rate limits: https://platform.claude.com/docs/en/api/rate-limits
- Anthropic token counting: https://platform.claude.com/docs/en/docs/build-with-claude/token-counting
- Anthropic SDK: Context7 /anthropics/anthropic-sdk-python (reputation: High)
- pytest: Context7 /websites/pytest_en_stable (reputation: High)
- PEP 621: https://peps.python.org/pep-0621/
- PEP 517: https://peps.python.org/pep-0517/
- Codebase: /Users/emma.hyde/projects/memesis/core/storage.py, tests/conftest.py

---

## 1. SQLite — Locking and Concurrent Access

### WAL Mode Is Not a Silver Bullet

WAL mode (`PRAGMA journal_mode=WAL`) enables concurrent readers with a single writer, but several traps exist:

**Checkpoint starvation.** If readers continuously overlap — no gap where zero readers are active — the WAL file grows without bound and is never flushed to the main database. Query performance degrades proportionally to WAL file size. Mitigation: ensure periodic "reader gaps" and call `PRAGMA wal_checkpoint(RESTART)` after write-heavy batches. This codebase already does `wal_checkpoint(TRUNCATE)` in `MemoryStore.close()`, which is correct but only fires at explicit teardown, not after bulk ingest.

**WAL reset bug in SQLite 3.7.0–3.51.2.** A data race in WAL mode under concurrent writes+checkpoints can silently corrupt the database. Affects all Python versions that ship those SQLite builds. macOS Sequoia (system Python) typically ships 3.43.x. Check with `sqlite3.sqlite_version`. Upgrade to 3.51.3+ where possible; on macOS this means using a Homebrew-linked SQLite or a `pysqlite3` wheel.

```python
import sqlite3
print(sqlite3.sqlite_version)  # e.g. "3.43.2" — vulnerable range
```

**Anti-pattern: opening a new connection per operation under concurrent load.** Every `sqlite3.connect(self.db_path)` in `storage.py` opens, uses, and closes a connection. Under concurrent scripts (e.g., `consolidate_cron.py` + `pre_compact.py` running simultaneously), SQLite's default 5-second `timeout` applies. If that is exceeded, `OperationalError: database is locked` is raised and the write is silently dropped — there is no retry logic.

```python
# Anti-pattern (current codebase pattern under concurrent cron + hook):
with sqlite3.connect(self.db_path) as conn:  # timeout=5.0 default
    conn.execute("INSERT ...")

# Better: explicit timeout + WAL for multi-process resilience
with sqlite3.connect(self.db_path, timeout=30.0) as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT ...")
```

**`synchronous=NORMAL` vs `FULL`.** The codebase uses `PRAGMA synchronous=NORMAL`, which is correct for WAL mode (WAL provides crash safety up to the last committed transaction without full sync). Do not set `synchronous=OFF` — that sacrifices durability entirely and can corrupt the DB on power loss or OS crash.

**Multi-process vs multi-thread.** WAL mode handles multiple *processes* safely. For multi-*thread* use within one process, SQLite's `check_same_thread=True` (the default) blocks cross-thread access. Setting `check_same_thread=False` without a threading lock causes undefined behavior. The codebase creates a new connection per call (single-threaded pattern), which is safe but expensive; shared-connection pools require explicit locking.

---

## 2. SQLite FTS5 — Injection Vulnerabilities

### FTS5 Query Injection Is Real

FTS5 has its own query language with special characters and boolean operators. Unlike SQL injection (addressed by `?` binding), **FTS5 query injection** occurs when user-supplied strings are passed as the MATCH argument via a parameterized binding but the *content* of that string contains FTS5 operators.

The current `search_fts()` call is safe against SQL injection because `query` is passed as a bound parameter (`WHERE memories_fts MATCH ?`). However, **FTS5 query syntax errors raised by malformed user input will propagate as `OperationalError`**, and malicious input can broaden or break the search semantics.

**Special characters with FTS5 meaning:**

| Character/Token | Meaning |
|---|---|
| `AND`, `OR`, `NOT` | Boolean set operators (case-sensitive) |
| `*` | Prefix wildcard |
| `^` | Initial token marker |
| `:` | Column filter (`title: foo`) |
| `"..."` | Phrase query |
| `()` | Grouping / NEAR |
| `+` | Phrase concatenation |
| `-` | NOT / column exclusion |

**Injection example:**

```python
# User submits: python OR 1
store.search_fts("python OR 1")
# FTS5 interprets this as: rows containing "python" UNION rows containing "1"
# This is semantically wrong and leaks unintended results.

# User submits: * (bare asterisk)
store.search_fts("*")
# OperationalError: fts5: syntax error near "*"
```

**Correct mitigation — sanitize before passing to FTS5:**

```python
import re

def sanitize_fts_query(query: str) -> str:
    """
    Wrap user input in a phrase query to neutralize FTS5 operators.
    Double-quotes inside are escaped by doubling them.
    """
    escaped = query.replace('"', '""')
    return f'"{escaped}"'

# Then:
cursor.execute("SELECT ... WHERE memories_fts MATCH ?", (sanitize_fts_query(user_input),))
```

**Column filter injection.** If any caller constructs `"title: " + user_input` for column-scoped search, the user can inject arbitrary FTS5 expressions by terminating the column filter. Always validate column names against an allowlist before building column-scoped queries.

**Content-table consistency.** The codebase uses `content='memories'` with an external content FTS5 table and manages FTS sync manually (no triggers). If a crash occurs between the file write and the `conn.commit()` in `create()`, the FTS index will be out of sync with the `memories` table. The fix is running `INSERT INTO memories_fts(memories_fts) VALUES('rebuild')` as a recovery step, not as a regular operation (it is expensive — O(n) rows).

---

## 3. Anthropic Python SDK — Rate Limiting and Token Counting

### Rate Limit Architecture (as of 2026-03)

The API uses a **token bucket algorithm** — capacity replenishes continuously, not at fixed minute boundaries. Critically:

- Limits are enforced as *three independent dimensions*: RPM (requests/min), ITPM (input tokens/min), OTPM (output tokens/min).
- A 429 error includes a `retry-after` header and `anthropic-ratelimit-*` headers. Retrying before `retry-after` seconds will fail.
- **Burst behavior:** A 60 RPM limit may be enforced as 1 req/sec. A burst of 5 simultaneous requests will trigger 429s for 4 of them even though the per-minute budget has headroom.

**Anti-pattern: treating RPM as "60 per minute resets at :00":**

```python
# WRONG — this can burst and hit per-second enforcement
for memory in all_memories:
    client.messages.create(...)  # fires all at once

# RIGHT — respect the token bucket with back-off
import time
from anthropic import RateLimitError

for memory in all_memories:
    try:
        client.messages.create(...)
    except RateLimitError as e:
        retry_after = int(e.response.headers.get("retry-after", 60))
        time.sleep(retry_after)
        client.messages.create(...)  # retry once
```

The SDK's built-in retry logic handles transient 429s with exponential back-off by default (2 retries). For consolidation batch loops, the default is usually insufficient — set `max_retries` higher or implement application-level throttling.

### Token Counting Gotchas

**`count_tokens` is an estimate, not a guarantee.** The docs state: "the actual number of input tokens used when creating a message may differ by a small amount." Anthropic may add system optimization tokens that appear in the count but are not billed. Do not use `count_tokens` results for hard budget enforcement — use them for routing and soft warnings only.

**`input_tokens` in the response body is not total input.** When prompt caching is active:

```
total_input = cache_read_input_tokens + cache_creation_input_tokens + input_tokens
```

The `input_tokens` field in `message.usage` only reflects tokens *after the last cache breakpoint*. A common bug is logging `message.usage.input_tokens` as the total cost and seeing unexpectedly low numbers when a large system prompt is cached.

```python
# BUG — undercounts total input when caching is active:
print(f"Input tokens: {message.usage.input_tokens}")

# CORRECT:
total = (
    message.usage.input_tokens
    + getattr(message.usage, 'cache_creation_input_tokens', 0)
    + getattr(message.usage, 'cache_read_input_tokens', 0)
)
print(f"Total input tokens: {total}")
```

**ITPM rate limits and caching.** For most current models (without the `†` marker in the rate limit tables), `cache_read_input_tokens` do NOT count toward ITPM. This means a large cached system prompt (e.g., the consolidation prompt in `core/prompts.py`) effectively multiplies your throughput. Older deprecated models (Haiku 3, Haiku 3.5) count cached reads against ITPM — migrating away from them increases effective throughput.

**`count_tokens` does not apply caching.** Calling `client.messages.count_tokens()` with `cache_control` blocks in the messages will return a higher count than the actual billed tokens during a real request where the cache is warm. The endpoint ignores caching state by design.

**Extended thinking and context window accounting.** Thinking blocks from *previous* assistant turns are ignored and do not count toward input tokens for subsequent turns. Only the *current* assistant turn's thinking counts. Passing previous thinking blocks forward in a multi-turn conversation inflates `count_tokens` but not the actual billed call.

**`max_tokens` does not affect OTPM rate limits.** Setting a high `max_tokens` (e.g., 8192) as a ceiling does not consume your OTPM budget — only actual generated tokens do. There is no cost to setting `max_tokens` higher than you expect to use.

### Deprecated SDK Patterns

The `client.beta.messages.count_tokens()` path (requiring `?beta=true`) is the old beta API. The stable path is `client.messages.count_tokens()` as of `anthropic>=0.40.0`. Using the beta path requires the `anthropic-beta` header and the response schema may differ.

---

## 4. pytest — Fixture Scoping and Database State

### Scope Hierarchy and State Leakage

pytest scopes in ascending lifetime order: `function` < `class` < `module` < `package` < `session`. A fixture can only request fixtures of equal or broader scope. The trap: **a `session`-scoped fixture that creates a database will have its state accumulate across all tests that share it.**

**Anti-pattern: session-scoped MemoryStore shared across tests:**

```python
# BAD — database state leaks between tests
@pytest.fixture(scope="session")
def memory_store(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("db")
    store = MemoryStore(base_dir=str(tmp))
    yield store
    store.close()

def test_a(memory_store):
    memory_store.create("a.md", "content A", {...})

def test_b(memory_store):
    # This test sees the record created by test_a — ORDER DEPENDENT
    results = memory_store.search_fts("content A")
    assert len(results) == 0  # FAILS if test_a ran first
```

The current `conftest.py` uses `function`-scoped `memory_store` (correct), but also `temp_dir` via `tempfile.mkdtemp()`. Note that `tempfile.mkdtemp()` does not integrate with pytest's cleanup tracking — the `shutil.rmtree` in the yield teardown is the only cleanup. If the test process is killed mid-run, temp dirs accumulate. Prefer `tmp_path` (built-in, function-scoped, auto-cleaned after 3 runs) for file-based fixtures.

**The `project_memory_store` fixture modifies `os.environ['HOME']`.** This is a process-global side effect. If tests run in parallel (e.g., with `pytest-xdist`), multiple workers will race on `HOME`. The fixture has a correct try/finally restore, but under parallelism the fixture setup and teardown from different tests interleave. Mitigation: scope this fixture to `function` only (already done) and avoid `pytest-xdist` unless the HOME mutation is moved to a monkeypatched scope.

**Scope mismatch raises an error at collection time.** A narrow-scoped fixture requesting a broader-scoped fixture works fine; the reverse raises:

```python
@pytest.fixture(scope="session")
def session_db():
    ...

@pytest.fixture(scope="function")  # function cannot use session — this is fine
def per_test_cursor(session_db):   # FINE: function requests session
    ...

# But this fails at collection:
@pytest.fixture(scope="session")
def session_helper(per_test_cursor):  # ERROR: session cannot use function
    ...
```

**Autouse fixtures can silently apply to unintended tests.** An `autouse=True` fixture at module scope activates for every test in the module, including tests that explicitly set up their own state. This leads to double-initialization bugs that are hard to trace. Prefer explicit fixture requests over autouse for database fixtures.

**Transaction rollback as an isolation pattern.** For tests that write to a shared SQLite database (e.g., integration tests using a pre-populated fixture DB), a common pattern is wrapping each test in a transaction that is rolled back at teardown:

```python
@pytest.fixture
def db_transaction(shared_db_connection):
    shared_db_connection.execute("BEGIN")
    yield shared_db_connection
    shared_db_connection.execute("ROLLBACK")
```

This does not work cleanly with `sqlite3.connect()` context managers (`with conn:` auto-commits on exit). It requires holding a persistent connection object across setup and teardown, which conflicts with the per-call connection pattern in `storage.py`.

---

## 5. Markdown File I/O — TOCTOU Race Conditions

### The TOCTOU Window

Time-of-check to time-of-use (TOCTOU) races occur when a file's state is checked in one syscall and acted upon in a separate syscall, with another process able to modify the file between them.

**Anti-pattern: check-then-act on files:**

```python
# VULNERABLE — another process can rename/delete between check and read:
if file_path.exists():
    content = file_path.read_text()  # FileNotFoundError if file deleted in the gap

# VULNERABLE — another process can create the file between check and write:
if not file_path.exists():
    file_path.write_text(content)  # Overwrites if created in the gap
```

The codebase already avoids the write-side TOCTOU correctly by using atomic rename (`tempfile.mkstemp` + `shutil.move`), which is the standard POSIX idiom. However, the read side in `get()`, `update()`, and `delete()` reads files with `file_path.read_text()` after checking `file_path.exists()` — a two-syscall pattern with a TOCTOU window.

**Correct read pattern (handle the race as an exception):**

```python
# SAFE — treat FileNotFoundError as the authoritative signal, not exists()
try:
    content = file_path.read_text(encoding="utf-8")
except FileNotFoundError:
    content = ""  # or raise, depending on caller semantics
```

**Cross-filesystem atomicity.** `shutil.move()` is atomic only when source and destination are on the same filesystem. `tempfile.mkstemp(dir=file_path.parent)` ensures the temp file is in the same directory as the destination, which guarantees same-filesystem rename atomicity on POSIX. This is done correctly in `storage.py`. Do not use `tempfile.mkstemp()` without specifying `dir=` — the default `/tmp` may be a different filesystem (common on macOS with APFS volumes).

**Encoding consistency.** `file_path.read_text()` without an explicit `encoding` argument uses the platform locale default. On most macOS/Linux systems this is UTF-8, but it is not guaranteed. Always specify `encoding="utf-8"` for both reads and writes. The codebase specifies encoding on writes (`os.write(tmp_fd, full_content.encode('utf-8'))`) but uses bare `read_text()` on several read paths in `storage.py` — this is a latent cross-platform bug.

**File descriptor leak on interrupted writes.** The `create()` and `update()` methods correctly close `tmp_fd` before the rename and handle exceptions by closing + unlinking. However, `os.write()` to a low-level fd and then `shutil.move()` bypasses Python's buffered I/O flush guarantees. The content is passed to the OS write buffer but not necessarily to the disk. For durability on crash, call `os.fsync(tmp_fd)` before `os.close(tmp_fd)`:

```python
os.write(tmp_fd, full_content.encode('utf-8'))
os.fsync(tmp_fd)  # flush to disk before renaming
os.close(tmp_fd)
shutil.move(tmp_path, file_path)
```

Note: `fsync` adds latency (~1–10ms per write on SSD). For high-throughput ingest, this is a trade-off; `synchronous=NORMAL` on SQLite WAL already provides crash safety at the DB level, so the markdown files are secondary truth.

---

## 6. pyproject.toml — Packaging Mistakes

### Required Fields and Fatal Mistakes

Per PEP 621, only `name` is unconditionally required. `version` is required unless listed in `[project.dynamic]` (for version-from-VCS workflows). The current `pyproject.toml` specifies both statically — correct.

**Fatal: specifying `name` in `dynamic`.** PEP 621 mandates that build backends raise an error if `name` appears in `dynamic`. This is a common copy-paste mistake when adopting dynamic versioning.

```toml
# FATAL — build backend will error:
[project]
dynamic = ["version", "name"]  # name cannot be dynamic
```

**Fatal: specifying a field both statically and in `dynamic`.**

```toml
# FATAL — double-specification:
[project]
version = "0.1.0"
dynamic = ["version"]  # version is already static above
```

**`build-system.requires` must include the backend package.** If `setuptools` is not listed in `requires`, a fresh `pip install --no-build-isolation` will fail in CI because the build environment is bootstrapped without it.

```toml
# CORRECT (current codebase):
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

# WRONG — build backend package missing from requires:
[build-system]
requires = ["wheel"]
build-backend = "setuptools.build_meta"  # setuptools not installed!
```

**Optional dependency groups must be installable independently.** All packages in `[project.optional-dependencies]` must be resolvable against the base `dependencies`. The `eval` group in this project (`inspect-ai`, `ragas`, `deepeval`) should be validated that their version constraints don't conflict with `anthropic>=0.40.0`. These are heavy ML packages with complex dependency trees.

**Missing `requires-python` upper bound.** The current spec `requires-python = ">=3.10"` has no upper bound. This is a common deliberate choice, but means the package will be offered to Python 4.x when it exists. If you depend on CPython-specific behavior, add an upper bound like `>=3.10,<4`.

**`tool.setuptools.packages.find` with `include` patterns.** The current config:

```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["core*", "hooks*"]
```

This correctly includes `core` and `hooks` packages. However, `eval` and `scripts` are not included. If `eval/__init__.py` or `scripts/` should be importable from an installed package, they need to be added. If they are standalone scripts only, this is correct — but `eval/` has an `__init__.py` which may suggest it is intended as an importable package.

**`dev` dependencies belong in `[project.optional-dependencies]`, not `[tool.poetry.dev-dependencies]` or a separate `requirements-dev.txt`.** The current `dev` extra with `pytest>=8.0` is correct PEP 621 style. Do not also maintain a `requirements-dev.txt` that duplicates these — they will diverge.

**Version specifiers: use `>=` for direct dependencies, avoid `==`.** Pinning with `==` in `[project.dependencies]` is correct for lockfiles (`requirements.txt`) but wrong for library/application `pyproject.toml` metadata — it prevents resolution for any downstream user. The current `anthropic>=0.40.0` is correct.

**Missing `license` field.** PEP 621 makes `license` optional but its absence causes `unknown license` in PyPI metadata and may block some organizational package policies. Even for internal packages, adding `license = {text = "MIT"}` (or appropriate SPDX) avoids warnings.

---

## Summary Table

| Area | Risk | Severity | Codebase Status |
|---|---|---|---|
| SQLite WAL bug (3.7.0–3.51.2) | Silent corruption under concurrent writes | HIGH | Check `sqlite3.sqlite_version`; unmitigated |
| WAL checkpoint starvation | Unbounded WAL growth, query slowdown | MEDIUM | `close()` truncates, but not after bulk writes |
| Per-call connection + 5s timeout | Silent write drops under concurrent processes | MEDIUM | All methods open fresh connections; no retry |
| FTS5 query injection (operator smuggling) | Wrong results / OperationalError from user input | HIGH | `search_fts()` passes raw query — unmitigated |
| FTS5 content-table desync on crash | Stale search index | LOW | Manual FTS sync; no rebuild-on-startup |
| Anthropic `input_tokens` undercounting | Wrong cost attribution when caching is active | MEDIUM | No caching currently; risk grows if added |
| Rate limit burst (token bucket) | 429s in consolidation batch loops | MEDIUM | No application-level throttling in consolidator |
| pytest `project_memory_store` env mutation | Parallel test failures | LOW | No xdist currently; risk if parallelism added |
| File read TOCTOU (exists then read) | FileNotFoundError in concurrent scenarios | LOW | Low concurrency currently; pattern is fragile |
| Missing `encoding=` on `read_text()` | Cross-platform encoding mismatch | LOW | Multiple read paths affected |
| Missing `os.fsync()` before rename | Data loss on crash before OS flushes buffer | LOW | Trade-off against write latency |
| pyproject.toml `eval` package not included | `eval/` importable in dev but not installed | LOW | `eval/__init__.py` exists but not in `find` |

**Gaps:** The setuptools and pytest readthedocs.io documentation was Cloudflare-blocked; pytest fixture scoping details were sourced from Context7's mirror of stable docs. The Anthropic SDK `anthropic>=0.40.0` changelog for exact deprecation timelines of beta endpoints was not independently verified beyond SDK source and official docs.
