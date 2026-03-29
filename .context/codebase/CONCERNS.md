# Concerns & Technical Debt

## Known Issues

- **SQLite version in WAL bug range**: `sqlite3.sqlite_version` reports `3.50.4` on this machine. The ecosystem-pitfalls research identifies a data race bug in SQLite WAL mode under concurrent writes+checkpoints affecting versions 3.7.0–3.51.2, which includes 3.50.4. This is silent corruption risk. No mitigation exists in the codebase. Check: `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`. Upgrade to 3.51.3+ via Homebrew-linked SQLite or a `pysqlite3` wheel to resolve.

- **`busy_timeout` not set on any connection**: `_init_db()` in `core/storage.py` enables WAL mode and `synchronous=NORMAL` but never calls `PRAGMA busy_timeout`. With the default 5-second timeout, any concurrent access between `consolidate_cron.py` and `pre_compact.py` (both open new connections per operation) can raise `OperationalError: database is locked` and silently drop the write. No retry logic exists anywhere in the codebase.

- **FTS5 query injection — unsanitized user input**: `search_fts()` in `core/storage.py` (line 575) passes the raw `query` string directly to `WHERE memories_fts MATCH ?`. This is safe against SQL injection but FTS5 has its own operator syntax (`AND`, `OR`, `NOT`, `*`, `^`, `:`, `"`, `+`, `-`). A bare `*` raises `OperationalError: fts5: syntax error near "*"`. The `user_prompt_inject.py` hook builds FTS queries from user prompt text using `" OR ".join(terms)` (line 113) — if any extracted term is an FTS keyword the query will misbehave. The `relevance.py` `find_rehydration_by_observation()` (line 285) does the same.

- **Four bare `read_text()` calls missing `encoding=`**: `core/storage.py` lines 386, 485, 520, and 588 call `file_path.read_text()` without `encoding="utf-8"`. On non-UTF-8 locales this silently corrupts content. All write paths specify UTF-8 explicitly, making this an asymmetry. Affected methods: `update()`, `delete()`, `get()`, `search_fts()`.

- **TOCTOU race in read paths**: `get()` (line 519), `delete()` (line 485), and `update()` (line 385) check `file_path.exists()` then call `read_text()` in a separate syscall. Another process (e.g., concurrent cron consolidation archiving the same memory) can delete the file between the two calls, producing `FileNotFoundError`. The correct pattern is to catch `FileNotFoundError` directly rather than pre-checking.

- **FTS index desync on crash**: `create()` writes the file atomically, then inserts into the DB and calls `_fts_insert()` in the same connection. If the process crashes after the file rename but before `conn.commit()`, the markdown file exists but the FTS index has no entry. No startup recovery or `rebuild_fts()` utility exists to detect or repair this state.

- **`_session_usage` attribute accessed externally in `pre_compact.py`**: `pre_compact.py` line 159 accesses `feedback._session_usage` directly — a private attribute — to count used memories for the summary string. This is a fragile coupling; if `FeedbackLoop`'s internal state changes, the hook silently produces wrong summary counts.

- **`eval/` package not distributed**: `pyproject.toml` includes only `"core*"` and `"hooks*"` in `[tool.setuptools.packages.find]`. The `eval/` directory has an `__init__.py` and is importable in development but will not be present in an installed package. This is inconsistent — either exclude `eval/__init__.py` or add `"eval*"` to the include list.

- **Stage promotion bug in `update()`**: When a memory is promoted to a new stage, `update()` writes the content to the original file path first (via `shutil.move(tmp_path, file_path)`), then updates the DB with the new `file_path`, then moves the file again (line 459: `shutil.move(file_path, updates['file_path'])`). Between the DB commit and the second file move, the DB's `file_path` column points to a location where the file does not yet exist. A crash in this window leaves DB and filesystem out of sync.

## Technical Debt

| Area | Description | Files | Severity |
|---|---|---|---|
| SQLite WAL bug | Version 3.50.4 is in the known-corrupt range (3.7.0–3.51.2). Silent corruption under concurrent writes+checkpoints. | `core/storage.py` (all DB calls) | high |
| Missing `busy_timeout` | No `PRAGMA busy_timeout` set anywhere; concurrent cron+hook writes silently fail with `OperationalError`. | `core/storage.py:_init_db()` | high |
| FTS5 query injection | Raw strings passed to `MATCH ?`; FTS operator keywords in user prompts cause errors or wrong results. | `core/storage.py:556`, `hooks/user_prompt_inject.py:113`, `core/relevance.py:285` | high |
| Bare `read_text()` calls | Four calls in `storage.py` omit `encoding="utf-8"`; writes all specify it. | `core/storage.py:386,485,520,588` | medium |
| FTS desync on crash | No rebuild-on-startup or recovery mechanism for FTS index when crash occurs mid-write. | `core/storage.py:create(),update()` | medium |
| TOCTOU file reads | `exists()` + `read_text()` two-syscall pattern in `get()`, `update()`, `delete()`. | `core/storage.py:385,485,519` | medium |
| `_session_usage` private access | `pre_compact.py` reaches into `FeedbackLoop._session_usage` directly. | `hooks/pre_compact.py:159`, `core/feedback.py:29` | medium |
| Stage transition atomicity | File moved twice during stage promotion; DB points to non-existent path between commit and second move. | `core/storage.py:441-459` | medium |
| No `max_retries` / `timeout` on Anthropic clients | All LLM calls use SDK defaults (2 retries, 10 min timeout). In `consolidate_cron.py` iterating many buffers, a single hung call blocks the whole run. | `core/consolidator.py`, `core/crystallizer.py`, `core/threads.py`, `core/self_reflection.py` | medium |
| Per-call SQLite connections | Every method opens a fresh `sqlite3.connect()`. Under concurrent cron+hook overlap this compounds the missing `busy_timeout` risk and adds connection overhead. | All methods in `core/storage.py` | medium |
| `eval` package not in dist | `eval/__init__.py` exists but not included in `pyproject.toml` packages; not installed in package form. | `pyproject.toml`, `eval/__init__.py` | low |
| No `os.fsync()` before rename | `create()` and `update()` write via `os.write(tmp_fd, ...)` without `os.fsync()`. Kernel buffers may not reach disk before rename; data loss on power loss is possible for instinctive/crystallized memories. | `core/storage.py:326`, `core/manifest.py:186` | low |
| `pyproject.toml` missing `license` | No `license` field; causes `unknown license` metadata in any package distribution. | `pyproject.toml` | low |
| `_extract_keywords()` unused | `FeedbackLoop._extract_keywords()` in `core/feedback.py` (line 266) is a static method that is never called — `_compute_usage_score()` replaced it but the old helper was not removed. | `core/feedback.py:266-271` | low |

## Security Considerations

- **FTS5 operator injection via user prompts**: `user_prompt_inject.py` extracts terms from user-supplied prompt text and builds `" OR ".join(terms)` queries. Because `extract_query_terms()` only filters for `MIN_WORD_LENGTH >= 4` and stop words, a user prompt containing `AND`, `NEAR`, `NOT`, or column filters like `title:` will produce malformed or semantically-wrong FTS queries. Mitigation: wrap each term in double-quotes (`"term"`) and escape internal double-quotes by doubling them. See `core/relevance.py:285` (same pattern) and `core/storage.py:556`.

- **`HOME` environment variable mutation in tests**: `tests/conftest.py` `project_memory_store` fixture (line 41) sets `os.environ['HOME']` as a process-global side effect. Although it restores it in `finally`, this is unsafe under `pytest-xdist` parallel execution. Any test that happens to read `Path.home()` while this fixture is active gets the wrong path. All hooks use `Path.home()` at startup — if a hook-related integration test runs in parallel, it would write to the wrong directory.

- **AWS credentials hardcoded by default**: `hooks/consolidate_cron.py` lines 37-39 call `os.environ.setdefault("AWS_PROFILE", "bedrock-users")` and `os.environ.setdefault("CLAUDE_CODE_USE_BEDROCK", "true")`. If these environment variables are not set before the cron runs, the cron silently enables Bedrock with the `bedrock-users` profile. A misconfigured AWS profile could either fail silently (no consolidation) or use unexpected credentials.

- **Content hash (MD5) used for deduplication**: `_compute_content_hash()` in `core/storage.py` (line 233) uses MD5. MD5 is not collision-resistant and is considered cryptographically weak. For deduplication this is not a security vulnerability in the traditional sense, but a crafted input could produce a hash collision that triggers a false "duplicate" rejection, denying memory creation. SHA-256 costs negligibly more and removes this concern.

## Performance

- **`search_by_tags()` does a full table scan**: `core/storage.py:594` fetches all memories with `SELECT * FROM memories` and filters in Python. For a store with hundreds of memories this is fine; above ~1000 memories this becomes noticeably slow. Tags are stored as JSON strings; there is no SQLite index on the `tags` column. Mitigation: add an FTS-style OR query per tag using `json_each()`, or maintain a separate `memory_tags` junction table.

- **`get_instinctive_memories()` does N+1 queries**: `core/retrieval.py:185` calls `store.list_by_stage("instinctive")` (one query) then calls `store.get(record["id"])` for each result (N queries) to load file content. Same pattern in `get_crystallized_for_context()` (line 259). For small memory counts this is fine, but for a large instinctive layer each session start pays N round-trips.

- **`_exclude_already_threaded()` in `ThreadDetector`**: `core/threads.py:180` calls `store.get_threads_for_memory(c["id"])` for every candidate memory in sequence. Each call is an individual query. With 50 candidate memories this is 50 queries. A single `SELECT memory_id FROM thread_members` query would cover all candidates.

- **`list_threads()` does N+1 queries**: `core/storage.py:938` fetches all threads then queries `thread_members` for each one individually. A single JOIN query would be more efficient.

- **Consolidation batch loop: no rate limiting**: `consolidate_cron.py` iterates buffers sequentially but each `consolidator.consolidate_session()` call fires two Anthropic API requests (main + optional retry). Contradiction resolution fires additional requests per conflict. No application-level throttling exists; the SDK's default 2 retries + exponential backoff handles transient 429s but not sustained rate pressure from processing many buffers at once.

- **`estimate_token_budget()` reads files from disk**: `core/manifest.py:142` reads all instinctive file contents and top-10 crystallized files from disk every time it is called. `pre_compact.py` calls `manifest.write_manifest()` which calls `generate()` which calls `estimate_token_budget()` every consolidation run. These are synchronous disk reads in the hot path.

## Fragile Areas

- **Lock file pattern (`ephemeral/.lock`)**: `pre_compact.py` and `consolidate_cron.py` both use `fcntl.flock()` on `ephemeral/.lock` to coordinate buffer access. `append_observation.py` does the same. This pattern works correctly for two-process mutual exclusion on POSIX. Fragility: `fcntl` is POSIX-only — this code will fail on Windows (not currently a concern but worth noting for portability). The lock file is recreated with `open(lock_path, "w")` each time, which truncates it; if two processes simultaneously call `open(..., "w")` before either calls `flock`, a race exists at the kernel level on file creation. On Linux `open(O_CREAT)` is atomic; on macOS APFS this is safe in practice.

- **Snapshot file collision**: `pre_compact.py` (line 91) and `consolidate_cron.py` (line 100) both write a snapshot to `.processing-{ephemeral_path.name}`. If both run simultaneously against the same buffer (possible if cron fires during a PreCompact), they will overwrite each other's snapshot silently. The lock only protects the read+clear of the original buffer, not the snapshot file.

- **Crystallizer `_fallback_promote()` silently swallows LLM errors**: `core/crystallizer.py:239` catches bare `except Exception` and calls `_fallback_promote()`. A network error, rate limit, or malformed response triggers a no-synthesis promotion — the memory is promoted but not synthesized. This is intentional but the log entry is only at `DEBUG` level, making it hard to notice how often synthesis is silently skipped.

- **Contradiction resolution: `superseded` and `scoped` branches do identical things**: `core/consolidator.py:478-497` — both `resolution_type == "superseded"` and the else branch (scoped/coexist) call exactly the same `store.update()` with the same arguments. The branching is a no-op. Whatever distinction was intended is not implemented.

- **`_merge_reflection()` deprecated-tendency detection is fragile**: `core/self_reflection.py:484` uses a regex to find `### {deprecated}` section headers in the self-model content, then inserts a deprecation note. If the tendency name contains regex-special characters or spans multiple lines, the match fails silently — no error, no deprecation note.

- **`_increment_consolidation_count()` is not atomic**: `hooks/pre_compact.py:46-50` reads the counter, increments in Python, then writes back. If two PreCompact processes ran concurrently (unlikely but possible with parallel test workers or unusual hook invocation), the counter could be double-incremented or lost to a read-modify-write race. This counter controls when self-reflection runs.

- **`consolidate_cron.py` rogue-directory check is a heuristic**: Line 58 skips project directories that don't start with `-`. This correctly filters directories whose path hash came from a leading `/` in the project path. But `MemoryStore._hash_project_path()` uses `re.sub(r'[^a-zA-Z0-9-]', '-', path)`, so any path starting with an alphanumeric character (e.g., a relative path or a path created by `base_dir=` parameter) produces a hash without a leading dash. Such stores will be silently ignored by cron forever.

- **`project_memory_store` fixture leaks `store.close()` not called**: `tests/conftest.py` `project_memory_store` fixture (line 37) does not call `store.close()` in its teardown. The WAL and SHM files accumulate; on macOS open file handles may prevent `tmp_path` cleanup. The base `memory_store` fixture (line 29) correctly calls `store.close()`.
