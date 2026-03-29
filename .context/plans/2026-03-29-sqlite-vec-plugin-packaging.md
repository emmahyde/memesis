# sqlite-vec + Plugin Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace sentence-transformers with persistent sqlite-vec embeddings powered by Bedrock Titan v2, and package memesis as a proper Claude Code plugin with venv-based dependency management.

**Architecture:** Plugin install script finds a Python with `enable_load_extension` (or installs one via mise), creates a venv in `${CLAUDE_PLUGIN_DATA}`, installs all deps. All hooks run from the venv Python. Embeddings computed once at write time via Bedrock, stored in sqlite-vec's vec0 table inside index.db, queried via SQL KNN at read time. No apsw, no separate vec.db, no PyTorch.

**Tech Stack:** Python 3.12+ (with extension loading), stdlib sqlite3, sqlite-vec, boto3 (Bedrock Titan v2), nltk, scikit-learn

---

## Current State (what's messy)

- `core/storage.py` has partial apsw code: `_vec_conn()` references `apsw.Connection`, `_vec_db_path` points to separate `vec.db`
- `pyproject.toml` lists `apsw>=3.46` as a dependency
- `core/crystallizer.py`, `core/threads.py`, `core/relevance.py` have `_get_embeddings()` functions that import `sentence_transformers`
- `core/embeddings.py` exists with Bedrock Titan wrapper (good, keep it)
- `hooks/hooks.json` uses bare `python3` not venv Python
- No `install-deps.sh`, no `requirements.txt` for plugin packaging

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/install-deps.sh` | Create | Find good Python / install via mise, create venv, install deps |
| `requirements.txt` | Create | Runtime deps for venv |
| `hooks/hooks.json` | Modify | Add SessionStart dep-install hook, switch all commands to venv Python |
| `pyproject.toml` | Modify | Remove apsw, remove sentence-transformers optional |
| `core/storage.py` | Modify | Remove apsw refs, fix vec methods to use stdlib sqlite3 in same DB |
| `core/crystallizer.py` | Modify | Replace `_get_embeddings` (sentence-transformers) with stored vec lookup |
| `core/threads.py` | Modify | Same replacement |
| `core/relevance.py` | Modify | Replace `_find_semantic_matches` with `store.search_vector()` |
| `hooks/pre_compact.py` | Modify | Embed new memories after consolidation/crystallization |
| `hooks/consolidate_cron.py` | Modify | Same embedding at write time |
| `scripts/embed_backfill.py` | Create | One-time script to embed all existing memories |
| `tests/test_storage.py` | Modify | Remove apsw-specific tests, add stdlib vec tests |
| `tests/test_crystallizer.py` | Modify | Update mocks from sentence_transformers to embeddings/store |
| `tests/test_threads.py` | Modify | Same |
| `tests/test_relevance.py` | Modify | Same |

---

## Task 1: Clean up storage.py — remove apsw, fix vec methods

**Files:**
- Modify: `core/storage.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Remove apsw from pyproject.toml**

In `pyproject.toml`, remove `"apsw>=3.46",` from dependencies. Also remove the sentence-transformers optional extra if still present.

```toml
dependencies = [
    "anthropic>=0.40.0",
    "nltk>=3.8",
    "scikit-learn>=1.4",
    "sqlite-vec>=0.1.6",
    "boto3>=1.34",
]
```

- [ ] **Step 2: Remove apsw references from storage.py**

Replace the import block at line 20-24:

```python
# Before
try:
    import sqlite_vec
    _SQLITE_VEC_AVAILABLE = True
except ImportError:
    _SQLITE_VEC_AVAILABLE = False
```

This is already correct — no apsw import here. But `_vec_conn()` at line 269-274 still references apsw. Delete that method entirely.

- [ ] **Step 3: Add `_vec_connect` helper using stdlib sqlite3**

Replace the deleted `_vec_conn` with:

```python
def _vec_connect(self):
    """Open a sqlite3 connection with sqlite-vec loaded. Caller must close."""
    if not self._vec_available:
        return None
    conn = sqlite3.connect(self.db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn
```

- [ ] **Step 4: Fix `store_embedding` to use stdlib sqlite3 in same DB**

Replace current apsw-based implementation:

```python
def store_embedding(self, memory_id: str, embedding: bytes) -> None:
    if not self._vec_available:
        return
    conn = self._vec_connect()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
            (memory_id, embedding),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Fix `search_vector` to use single-DB JOIN**

Since vec_memories and memories are in the same index.db, we can JOIN directly:

```python
def search_vector(self, query_embedding: bytes, k: int = 10, exclude_ids: set = None) -> list[dict]:
    if not self._vec_available or query_embedding is None:
        return []
    conn = self._vec_connect()
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT m.*, v.distance
            FROM vec_memories v
            JOIN memories m ON m.id = v.memory_id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (query_embedding, k),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if exclude_ids and d.get("id") in exclude_ids:
                continue
            results.append(d)
        return results
    finally:
        conn.close()
```

- [ ] **Step 6: Fix `get_embedding` to use stdlib**

```python
def get_embedding(self, memory_id: str) -> bytes | None:
    if not self._vec_available:
        return None
    conn = self._vec_connect()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT embedding FROM vec_memories WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()
```

- [ ] **Step 7: Remove `_vec_db_path` references**

Search for `_vec_db_path` in storage.py and remove all references. The vec table now lives in `self.db_path` (index.db).

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_storage.py -q`

Note: Vec-enabled tests will skip on this machine (no `enable_load_extension`). That's expected — the plugin venv solves this. The graceful-fallback tests (`_vec_available = False` paths) must still pass.

- [ ] **Step 9: Commit**

```bash
git add core/storage.py pyproject.toml
git commit -m "refactor: remove apsw, use stdlib sqlite3 for sqlite-vec (plugin venv provides extension loading)"
```

---

## Task 2: Plugin packaging — install-deps.sh + requirements.txt + hooks.json

**Files:**
- Create: `scripts/install-deps.sh`
- Create: `requirements.txt`
- Modify: `hooks/hooks.json`

- [ ] **Step 1: Create requirements.txt**

```
anthropic>=0.40.0
nltk>=3.8
scikit-learn>=1.4
sqlite-vec>=0.1.6
boto3>=1.34
```

- [ ] **Step 2: Create scripts/install-deps.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="${1:?Usage: install-deps.sh <PLUGIN_ROOT> <PLUGIN_DATA>}"
PLUGIN_DATA="${2:?Usage: install-deps.sh <PLUGIN_ROOT> <PLUGIN_DATA>}"
VENV_DIR="$PLUGIN_DATA/venv"
REQUIREMENTS="$PLUGIN_ROOT/requirements.txt"
STAMP="$PLUGIN_DATA/requirements.txt"

# Skip if deps are current
if diff -q "$REQUIREMENTS" "$STAMP" >/dev/null 2>&1; then
    exit 0
fi

echo "[memesis] Installing dependencies..." >&2

# Find a Python with enable_load_extension support
find_good_python() {
    for candidate in python3 python3.12 python3.13 python3.14 /opt/homebrew/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c "import sqlite3; sqlite3.connect(':memory:').enable_load_extension(True)" 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_good_python) || {
    echo "[memesis] No Python with sqlite extension loading found. Installing via mise..." >&2
    if command -v mise >/dev/null 2>&1; then
        # mise-installed Python compiles from source with Homebrew SQLite
        # which includes enable_load_extension
        mise install python@3.13 >&2 2>&1 || true
        PYTHON=$(mise which python 2>/dev/null) || PYTHON="python3"
        # Verify it worked
        if ! "$PYTHON" -c "import sqlite3; sqlite3.connect(':memory:').enable_load_extension(True)" 2>/dev/null; then
            echo "[memesis] WARNING: Could not find Python with extension loading. Vector search will be disabled." >&2
            PYTHON="python3"
        fi
    else
        echo "[memesis] WARNING: mise not installed. Vector search will be disabled." >&2
        PYTHON="python3"
    fi
}

echo "[memesis] Using Python: $PYTHON ($($PYTHON --version 2>&1))" >&2

# Create venv
"$PYTHON" -m venv "$VENV_DIR"

# Install deps
"$VENV_DIR/bin/pip" install -q -r "$REQUIREMENTS"

# Download NLTK data into plugin data dir
NLTK_DATA="$PLUGIN_DATA/nltk_data"
"$VENV_DIR/bin/python3" -c "
import os, nltk
os.environ['NLTK_DATA'] = '$NLTK_DATA'
nltk.download('stopwords', download_dir='$NLTK_DATA', quiet=True)
"

# Stamp
cp "$REQUIREMENTS" "$STAMP"
echo "[memesis] Dependencies installed." >&2
```

- [ ] **Step 3: Make install-deps.sh executable**

```bash
chmod +x scripts/install-deps.sh
```

- [ ] **Step 4: Update hooks/hooks.json**

Add SessionStart dep-install hook. Switch all commands to venv Python. Set NLTK_DATA env var.

```json
{
  "SessionStart": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/install-deps.sh ${CLAUDE_PLUGIN_ROOT} ${CLAUDE_PLUGIN_DATA}",
          "timeout": 120,
          "statusMessage": "Installing memesis dependencies..."
        },
        {
          "type": "command",
          "command": "NLTK_DATA=${CLAUDE_PLUGIN_DATA}/nltk_data ${CLAUDE_PLUGIN_DATA}/venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py",
          "timeout": 5
        }
      ]
    }
  ],
  "PreCompact": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "NLTK_DATA=${CLAUDE_PLUGIN_DATA}/nltk_data ${CLAUDE_PLUGIN_DATA}/venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pre_compact.py",
          "timeout": 30
        }
      ]
    }
  ],
  "UserPromptSubmit": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "NLTK_DATA=${CLAUDE_PLUGIN_DATA}/nltk_data ${CLAUDE_PLUGIN_DATA}/venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/hooks/user_prompt_inject.py",
          "timeout": 3
        }
      ]
    }
  ]
}
```

- [ ] **Step 5: Commit**

```bash
git add scripts/install-deps.sh requirements.txt hooks/hooks.json
git commit -m "feat: plugin packaging with venv deps + mise fallback for extension loading"
```

---

## Task 3: Wire embeddings into write path

**Files:**
- Modify: `hooks/pre_compact.py`
- Modify: `hooks/consolidate_cron.py`

- [ ] **Step 1: Add embedding import to pre_compact.py**

At the top of `pre_compact.py`, add:

```python
from core.embeddings import embed_for_memory
```

- [ ] **Step 2: Embed after consolidation keeps**

In `pre_compact.py`, after `result = consolidator.consolidate_session(...)` (line 110), add embedding for each kept memory:

```python
# Embed newly kept memories
for memory_id in result.get("kept", []):
    try:
        mem = store.get(memory_id)
        embedding = embed_for_memory(
            mem.get("title", ""),
            mem.get("summary", ""),
            mem.get("content", "")[:500],
        )
        if embedding:
            store.store_embedding(memory_id, embedding)
    except Exception as e:
        print(f"Embedding error (non-fatal): {e}", file=sys.stderr)
```

- [ ] **Step 3: Embed after crystallization**

After `crystallized = crystallizer.crystallize_candidates()` (line 117), add:

```python
for crystal in crystallized:
    try:
        cid = crystal.get("crystallized_id")
        if cid:
            mem = store.get(cid)
            embedding = embed_for_memory(
                mem.get("title", ""),
                crystal.get("insight", ""),
            )
            if embedding:
                store.store_embedding(cid, embedding)
    except Exception as e:
        print(f"Crystal embedding error (non-fatal): {e}", file=sys.stderr)
```

- [ ] **Step 4: Same pattern in consolidate_cron.py**

Add the same embedding calls after consolidation and crystallization in `consolidate_buffer()` (around line 112-121).

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_hooks.py -q`
Expected: All pass (embedding calls are try/excepted and Bedrock won't be available in tests).

- [ ] **Step 6: Commit**

```bash
git add hooks/pre_compact.py hooks/consolidate_cron.py
git commit -m "feat: embed memories at write time via Bedrock Titan v2"
```

---

## Task 4: Replace sentence-transformers with stored vector lookups

**Files:**
- Modify: `core/crystallizer.py`
- Modify: `core/threads.py`
- Modify: `core/relevance.py`

- [ ] **Step 1: Replace `_get_embeddings` in crystallizer.py**

Replace the current function (lines 71-88) which imports sentence_transformers with one that looks up stored embeddings:

```python
def _get_embeddings(store, candidates: list[dict]):
    """
    Retrieve stored embeddings for candidates from vec_memories.

    Returns numpy array of shape (N, 512), or None if embeddings
    are not available for all candidates.
    """
    try:
        import struct
        import numpy as np
    except ImportError:
        return None

    embeddings = []
    for c in candidates:
        raw = store.get_embedding(c["id"])
        if raw is None:
            return None  # Missing embedding — fall back to tag-overlap
        vec = struct.unpack(f"{len(raw)//4}f", raw)
        embeddings.append(vec)

    return np.array(embeddings, dtype=np.float32)
```

- [ ] **Step 2: Update `_group_candidates` call site**

Change the call from `_get_embeddings(texts)` to `_get_embeddings(self.store, candidates)`:

```python
embeddings = _get_embeddings(self.store, candidates) if min_text_len >= 10 else None
```

- [ ] **Step 3: Same replacement in threads.py**

Replace `_get_embeddings` (lines 26-44) with the stored-vector lookup version. Update the call site in `detect_threads` (line 170).

```python
def _get_embeddings(store, memories: list[dict]):
    """Retrieve stored embeddings from vec_memories."""
    try:
        import struct
        import numpy as np
    except ImportError:
        return None

    embeddings = []
    for m in memories:
        raw = store.get_embedding(m["id"])
        if raw is None:
            return None
        vec = struct.unpack(f"{len(raw)//4}f", raw)
        embeddings.append(vec)

    return np.array(embeddings, dtype=np.float32)
```

- [ ] **Step 4: Replace `_find_semantic_matches` in relevance.py**

Replace the current implementation (which imports sentence_transformers at line 368) with a `store.search_vector()` call:

```python
def _find_semantic_matches(self, observation: str, archived_memories: list[dict], threshold: float = 0.65) -> list[dict]:
    """Find archived memories semantically similar to the observation using stored vectors."""
    from .embeddings import embed_text

    query_embedding = embed_text(observation)
    if query_embedding is None:
        return []

    # Use KNN search — gets nearest from ALL stored vectors
    results = self.store.search_vector(query_embedding, k=20)

    # Filter to only archived + not subsumed
    archived_ids = {m["id"] for m in archived_memories}
    return [r for r in results if r.get("id") in archived_ids]
```

- [ ] **Step 5: Verify no sentence_transformers imports remain**

```bash
grep -r "sentence_transformers\|SentenceTransformer" core/ hooks/
```

Expected: No output.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_crystallizer.py tests/test_threads.py tests/test_relevance.py -q`

Note: Tests that mock `sentence_transformers.SentenceTransformer` need updating (Task 5). Some may fail here — that's expected.

- [ ] **Step 7: Commit**

```bash
git add core/crystallizer.py core/threads.py core/relevance.py
git commit -m "feat: replace sentence-transformers with stored sqlite-vec vector lookups"
```

---

## Task 5: Update tests for new vector architecture

**Files:**
- Modify: `tests/test_crystallizer.py`
- Modify: `tests/test_threads.py`
- Modify: `tests/test_relevance.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Update test_crystallizer.py TestEmbeddingGrouping**

Change mocks from `patch("core.crystallizer._get_embeddings", return_value=embeddings)` to mock `store.get_embedding` returning raw bytes:

```python
import struct

def _make_embedding(values):
    """Convert a list of floats to raw float32 bytes."""
    return struct.pack(f"{len(values)}f", *values)

class TestEmbeddingGrouping:
    def test_similar_candidates_grouped_by_embeddings(self, ...):
        # Create candidates and store fake embeddings
        embs = _cluster_embeddings(3, cluster_size=2)
        for i, mid in enumerate(candidate_ids):
            store.store_embedding(mid, _make_embedding(embs[i].tolist()))

        # No mock needed — _get_embeddings reads from store
        groups = crystallizer._group_candidates(candidates)
        assert ...  # two similar in same group

    def test_embedding_fallback_when_unavailable(self, ...):
        # Don't store any embeddings → _get_embeddings returns None → tag fallback
        groups = crystallizer._group_candidates(candidates)
        assert ...  # falls back to tag-overlap
```

If `_vec_available` is False on this machine, mock `store.get_embedding` directly to return bytes.

- [ ] **Step 2: Update test_threads.py TestEmbeddingClustering**

Same pattern — mock `store.get_embedding` or actually store embeddings if vec is available.

- [ ] **Step 3: Update test_relevance.py TestSemanticRehydration**

Mock `core.embeddings.embed_text` to return fake query bytes, and mock `store.search_vector` to return known results.

- [ ] **Step 4: Update test_storage.py**

Remove apsw-specific fallback tests. Keep the `TestVecUnavailableFallback` tests (they test `_vec_available = False` paths which still apply when extension loading isn't available).

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test: update embedding tests for sqlite-vec stored vector architecture"
```

---

## Task 6: Embedding backfill script

**Files:**
- Create: `scripts/embed_backfill.py`

- [ ] **Step 1: Create the script**

```python
#!/usr/bin/env python3
"""
Embed all existing memories via Bedrock Titan v2 and store in vec_memories.

Usage:
    python3 scripts/embed_backfill.py                    # Embed all
    python3 scripts/embed_backfill.py --dry-run           # Count only
    python3 scripts/embed_backfill.py --project-context /path  # Project-specific
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.embeddings import embed_for_memory
from core.storage import MemoryStore


def main():
    project_context = None
    dry_run = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--project-context" and i + 1 < len(args):
            project_context = args[i + 1]; i += 2
        elif args[i] == "--dry-run":
            dry_run = True; i += 1
        else:
            print(f"Unknown: {args[i]}", file=sys.stderr); sys.exit(1)

    store = MemoryStore(project_context=project_context)

    if not store._vec_available:
        print("sqlite-vec not available. Run from plugin venv.", file=sys.stderr)
        sys.exit(1)

    # Count memories needing embeddings
    total = 0
    need_embedding = []
    for stage in ("consolidated", "crystallized", "instinctive"):
        memories = store.list_by_stage(stage)
        for mem in memories:
            total += 1
            existing = store.get_embedding(mem["id"])
            if existing is None:
                need_embedding.append(mem)

    print(f"Total memories: {total}", file=sys.stderr)
    print(f"Need embedding: {len(need_embedding)}", file=sys.stderr)

    if dry_run:
        store.close()
        return

    embedded = 0
    failed = 0
    for i, mem in enumerate(need_embedding):
        full = store.get(mem["id"])
        title = full.get("title", "")
        summary = full.get("summary", "")
        content = full.get("content", "")

        print(f"  [{i+1}/{len(need_embedding)}] {title[:50]}... ", end="", file=sys.stderr, flush=True)

        embedding = embed_for_memory(title, summary, content)
        if embedding:
            store.store_embedding(mem["id"], embedding)
            embedded += 1
            print("OK", file=sys.stderr)
        else:
            failed += 1
            print("FAILED", file=sys.stderr)

        time.sleep(0.1)  # Light rate limiting

    print(f"\nEmbedded: {embedded}, Failed: {failed}", file=sys.stderr)
    store.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/embed_backfill.py
```

- [ ] **Step 3: Commit**

```bash
git add scripts/embed_backfill.py
git commit -m "feat: add embedding backfill script for existing memories"
```

---

## Verification

After all tasks, verify end-to-end:

1. `pytest tests/ -q` — all tests pass
2. `grep -r "apsw\|sentence_transformers\|SentenceTransformer" core/ hooks/ scripts/` — no apsw or sentence-transformers references in production code (scripts/reduce.py and scripts/consolidate.py may still have sqlite3 for observations.db — that's fine, it's a separate DB)
3. `cat hooks/hooks.json` — all hooks use `${CLAUDE_PLUGIN_DATA}/venv/bin/python3`
4. `bash scripts/install-deps.sh . /tmp/test-plugin-data` — installs deps into a test venv
5. Run embed_backfill.py from the venv to verify Bedrock connectivity
