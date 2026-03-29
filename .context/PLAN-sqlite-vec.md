# Plan: Replace sentence-transformers with sqlite-vec + Bedrock Titan

**Source:** sqlite-vec research + CONTEXT-nlp-integration.md
**Generated:** 2026-03-29
**Status:** Ready for execution

## Overview

Replace the sentence-transformers dependency (500MB PyTorch, re-encodes every call) with sqlite-vec (2MB, persistent vectors) + Bedrock Titan v2 (API embeddings). Embeddings computed once at write time, stored in SQLite, queried via KNN SQL.

**Waves:** 2
**Total tasks:** 5

---

## Wave 1: Foundation — embeddings module + storage integration

### Task 1.1: Add deps + create core/embeddings.py

- **Files owned:** `pyproject.toml`, `core/embeddings.py` (new)
- **Depends on:** None
- **Acceptance criteria:**
  - [ ] pyproject.toml: add `sqlite-vec>=0.1.6` and `boto3>=1.34` to dependencies. Remove `sentence-transformers` from optional nlp extra.
  - [ ] core/embeddings.py: `embed_text(text, dimensions=512) -> bytes` wrapping Bedrock Titan v2
  - [ ] Uses `boto3.client("bedrock-runtime")` with lazy client init
  - [ ] Returns raw float32 bytes (struct.pack) ready for sqlite-vec INSERT
  - [ ] Respects CLAUDE_CODE_USE_BEDROCK env var for region/profile
  - [ ] Graceful fallback: returns None if boto3 unavailable or API fails

### Task 1.2: sqlite-vec integration in storage.py

- **Files owned:** `core/storage.py`
- **Depends on:** Task 1.1
- **Acceptance criteria:**
  - [ ] `_init_db()` loads sqlite-vec extension and creates `vec_memories` virtual table (float[512])
  - [ ] `store_embedding(memory_id, embedding_bytes)` inserts/replaces in vec_memories
  - [ ] `search_vector(query_bytes, k=10, exclude_ids=None)` returns ranked memory dicts
  - [ ] `get_embedding(memory_id)` returns stored embedding bytes or None
  - [ ] sqlite-vec loaded via `sqlite_vec.load(conn)` with enable_load_extension
  - [ ] Graceful: if sqlite-vec not installed, vector methods return empty/None (don't crash FTS paths)

### Task 1.3: Embed at write time

- **Files owned:** `hooks/pre_compact.py`, `hooks/consolidate_cron.py`
- **Depends on:** Tasks 1.1, 1.2
- **Acceptance criteria:**
  - [ ] After consolidation creates a new memory, embed its title+summary and store in vec_memories
  - [ ] After crystallization creates a crystallized memory, embed and store
  - [ ] Embedding failures don't block the consolidation pipeline (try/except, stderr diagnostic)

---

## Wave 2: Replace sentence-transformers callsites + tests

### Task 2.1: Replace _get_embeddings in crystallizer, threads, relevance

- **Files owned:** `core/crystallizer.py`, `core/threads.py`, `core/relevance.py`
- **Depends on:** Wave 1
- **Acceptance criteria:**
  - [ ] crystallizer._get_embeddings: query vec_memories for stored embeddings instead of calling SentenceTransformer
  - [ ] threads._get_embeddings: same — lookup from vec_memories
  - [ ] relevance._find_semantic_matches: use store.search_vector() KNN instead of encoding on-the-fly
  - [ ] Remove all `from sentence_transformers import` lines
  - [ ] Fallback to tag-overlap/FTS when no embeddings stored (same as before)

### Task 2.2: Update tests + backfill script

- **Files owned:** `tests/test_storage.py`, `tests/test_crystallizer.py`, `tests/test_threads.py`, `tests/test_relevance.py`, `scripts/embed_backfill.py` (new)
- **Depends on:** Task 2.1
- **Acceptance criteria:**
  - [ ] test_storage.py: add TestVectorStorage class (store/retrieve/search vectors)
  - [ ] Update embedding test mocks to patch core.embeddings.embed_text instead of SentenceTransformer
  - [ ] scripts/embed_backfill.py: iterate existing memories, call Bedrock, store in vec_memories
  - [ ] All 491+ tests pass

---

## File Ownership Map

| File | Task |
|------|------|
| pyproject.toml | 1.1 |
| core/embeddings.py (new) | 1.1 |
| core/storage.py | 1.2 |
| hooks/pre_compact.py | 1.3 |
| hooks/consolidate_cron.py | 1.3 |
| core/crystallizer.py | 2.1 |
| core/threads.py | 2.1 |
| core/relevance.py | 2.1 |
| tests/test_storage.py | 2.2 |
| tests/test_crystallizer.py | 2.2 |
| tests/test_threads.py | 2.2 |
| tests/test_relevance.py | 2.2 |
| scripts/embed_backfill.py (new) | 2.2 |

## Cross-Wave Handoffs

| File | Wave 1 | Wave 2 |
|------|--------|--------|
| core/storage.py | 1.2 adds vec methods | 2.1 consumers call them |
| core/embeddings.py | 1.1 creates it | 1.3 + 2.1 use it |
