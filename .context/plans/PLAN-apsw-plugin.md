# Plan: APSW Migration + Plugin Packaging

**Generated:** 2026-03-29

## Overview

Migrate from stdlib sqlite3 to apsw throughout. Single index.db with FTS5 + vec0. Package as proper Claude Code plugin with venv-based dependency management.

**Waves:** 3
**Total tasks:** 8

---

## Wave 1: Core apsw migration in storage.py

### Task 1.1: Migrate storage.py internals to apsw

- **Files owned:** `core/storage.py`
- **What to do:**
  - Replace all `sqlite3.connect(self.db_path)` with `apsw.Connection(str(self.db_path))`
  - Remove the separate vec.db — vec_memories goes back into index.db
  - Add a dict-row helper since apsw doesn't have row_factory:
    ```python
    def _dict_row(cursor, row):
        return {d[0]: v for d, v in zip(cursor.getdescription(), row)}
    ```
  - Replace `conn.row_factory = sqlite3.Row` with cursor rowtrace
  - apsw autocommits by default — remove explicit `conn.commit()` calls for individual statements. Use `with conn` for multi-statement transactions.
  - Replace `sqlite3.OperationalError` catches with `apsw.SQLError`
  - Load sqlite-vec in every connection via `_connect()` helper
  - Keep `import sqlite3` ONLY for `sqlite3.OperationalError` in the ALTER TABLE migration guards (or switch to apsw.SQLError)
  - Add public `store.connect()` → returns an apsw connection with vec loaded + row trace set. For other modules that need direct SQL.
- **AC:** All test_storage.py tests pass. Vec operations work in same DB. No sqlite3 imports remain (except maybe for OperationalError compat).

### Task 1.2: Route all store.db_path bypasses through store.connect()

- **Files owned:** `core/lifecycle.py`, `core/feedback.py`, `core/self_reflection.py`, `hooks/user_prompt_inject.py`
- **What to do:**
  - Replace `with sqlite3.connect(self.store.db_path) as conn:` with `conn = self.store.connect()` + `try/finally conn.close()`
  - Remove `conn.row_factory = sqlite3.Row` (handled by store.connect())
  - Remove `import sqlite3` from each file
- **AC:** No `sqlite3.connect(store.db_path)` in any core/ or hooks/ file.

### Task 1.3: Update scripts that access store.db_path

- **Files owned:** `scripts/heartbeat.py`, `scripts/diagnose.py`
- **What to do:**
  - heartbeat.py and diagnose.py open store.db_path directly for read queries. Update to use `store.connect()`.
  - scripts/reduce.py and scripts/consolidate.py use their OWN observations.db — leave as sqlite3 (separate DB, no vec needed).
- **AC:** heartbeat.py and diagnose.py use store.connect(). reduce.py and consolidate.py unchanged.

---

## Wave 2: Update all tests

### Task 2.1: Update test files that use sqlite3 directly

- **Files owned:** ALL test files in tests/
- **What to do:**
  - Tests that do `sqlite3.connect(store.db_path)` for assertions/setup → use `store.connect()` or apsw.Connection directly
  - Replace `conn.row_factory = sqlite3.Row` with dict comprehension or apsw rowtrace
  - Replace `sqlite3.OperationalError` catches with `apsw.SQLError` where applicable
  - test_scripts.py stays on sqlite3 (it tests observations.db, not index.db)
- **AC:** `pytest tests/ -q` all pass. No test uses `sqlite3.connect(store.db_path)`.

---

## Wave 3: Plugin packaging

### Task 3.1: Create install-deps.sh + requirements.txt

- **Files owned:** `scripts/install-deps.sh` (new), `requirements.txt`
- **What to do:**
  - requirements.txt with all runtime deps: anthropic, nltk, scikit-learn, sqlite-vec, apsw, boto3
  - install-deps.sh: creates venv in ${CLAUDE_PLUGIN_DATA}, pip installs, downloads NLTK data
- **AC:** Running install-deps.sh creates a working venv.

### Task 3.2: Update hooks.json for venv Python

- **Files owned:** `hooks/hooks.json`
- **What to do:**
  - Add SessionStart hook for dependency installation (diff + install pattern)
  - Update all hook commands to use `${CLAUDE_PLUGIN_DATA}/venv/bin/python3`
  - Ensure ${CLAUDE_PLUGIN_ROOT} references for script paths
- **AC:** hooks.json uses venv Python for all hooks.

### Task 3.3: Wire embeddings into write path

- **Files owned:** `hooks/pre_compact.py`, `hooks/consolidate_cron.py`, `core/consolidator.py`
- **What to do:**
  - After consolidation creates/promotes a memory → call embeddings.embed_for_memory() → store.store_embedding()
  - After crystallization creates a crystallized memory → embed + store
  - Graceful: embedding failures don't block consolidation
- **AC:** New memories get embeddings stored in vec_memories.

### Task 3.4: Replace sentence-transformers with sqlite-vec queries

- **Files owned:** `core/crystallizer.py`, `core/threads.py`, `core/relevance.py`
- **What to do:**
  - crystallizer._get_embeddings: query stored embeddings from store instead of SentenceTransformer
  - threads._get_embeddings: same
  - relevance._find_semantic_matches: use store.search_vector() KNN
  - Remove all sentence_transformers imports
  - Keep fallback to tag-overlap/FTS when no embeddings stored
- **AC:** No sentence_transformers imports anywhere. Vec queries work.

### Task 3.5: Embedding backfill script

- **Files owned:** `scripts/embed_backfill.py` (new)
- **What to do:**
  - Iterate all memories in store, call Bedrock Titan v2, store embedding
  - Progress reporting, rate limiting
- **AC:** Running script embeds all existing memories.
