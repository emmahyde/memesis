# Context: NLP Library Integration + Batch Quality Fixes

**Date:** 2026-03-28
**Mode:** Panel discussion (--auto)

## Scope

Integrate NLTK (stopwords + stemmer), scikit-learn (TF-IDF), and sentence-transformers (all-MiniLM-L6-v2) into the memesis memory system. Fix three batch quality bugs: feedback.py substring match, reduce.py dedup boundary, consolidation gate threshold.

## Decisions

### D-01: Bug fixes land first, NLP second
Fix all three bugs and update tests before any NLP integration. The bugs corrupt the training signal — NLP improvements built on broken foundations will inherit the corruption.

### D-02: Substring match fix + threshold revalidation
Replace `w in response_lower` with word-boundary matching (`re.search(rf'\b{re.escape(w)}\b', response_lower)`). After fix, revalidate `_USAGE_THRESHOLD = 4.0` — it was calibrated against the broken scorer. Memories previously counted as "used" may start appearing "unused."

### D-03: reduce.py dedup — track processed sessions explicitly
Add a `processed_sessions` table to observations.db. The current approach builds `processed` from observation sources, but sessions that only trigger REINFORCEs never get recorded. Also add programmatic near-duplicate detection (TF-IDF cosine similarity) before LLM calls as a safety net.

### D-04: Consolidation gate — add frequency floor
The 98.7% keep rate means the gate is rubber-stamping. Add to the prompt: explicit instruction to prune freq=1 observations unless they pass a strict behavioral gate ("would I do something wrong without this?"). Target: 60-70% keep rate.

### D-05: Library tiering by hook budget
- **NLTK + scikit-learn:** Safe to import anywhere. ~80MB combined. Negligible runtime.
- **sentence-transformers:** ONLY in PreCompact (30s budget) and cron/scripts (no budget). NEVER imported in SessionStart (5s) or UserPromptSubmit (3s). One misplaced import kills the hook silently.

### D-06: Lazy guarded imports for heavy libraries
All sentence-transformers imports use the call-site pattern:
```python
def _get_embeddings(texts):
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        return model.encode(texts)
    except Exception:
        return None  # caller falls back to TF-IDF
```
NLTK and sklearn can use top-level imports in core/ modules.

### D-07: NLTK integration points
- `core/feedback.py` `_compute_usage_score`: Strip stopwords, stem both source and response tokens
- `core/relevance.py` `find_rehydration_by_observation`: Strip stopwords, stem FTS query terms
- NLTK data (stopwords corpus) downloaded lazily on first use with try/except fallback

### D-08: scikit-learn integration points
- `scripts/reduce.py`: TF-IDF cosine similarity for near-duplicate detection before LLM call
- `scripts/consolidate.py`: TF-IDF pre-clustering to help LLM merge decisions
- `core/feedback.py`: Optional TF-IDF specificity weights (replace hand-rolled `_term_specificity`)

### D-09: sentence-transformers integration points
- `core/crystallizer.py` `_group_candidates`: Embed title+content, cluster by cosine similarity instead of tag overlap
- `core/threads.py` thread detection: Embed memories, cluster by semantic similarity instead of tag overlap
- `core/relevance.py` rehydration: Semantic match for archived memory rehydration
- Model: `all-MiniLM-L6-v2` (384-dim, ~80MB model, fast CPU inference)
- Fallback: If embeddings unavailable, fall back to existing tag-overlap + TF-IDF

### D-10: Test conventions
- Assert on behavior (was_used, was_retrieved, was_pruned), not raw score values
- Mock sentence-transformers at `SentenceTransformer.encode` call site, returning deterministic numpy arrays
- NLTK stopwords/stemmer don't need mocking (fast, deterministic)
- Add test coverage for scripts/reduce.py and scripts/consolidate.py (currently zero)

## Conventions to Enforce

- Per-operation SQLite connections (no holding connections across NLP computation)
- Lazy imports for sentence-transformers (never at module top-level)
- stderr for NLP fallback diagnostics (no stdout in hooks)
- ValueError for domain errors (consistent with existing pattern)

## Concerns to Watch

- Hook timeout budgets (3s/5s/30s) — sentence-transformers model load is 2-5s
- _USAGE_THRESHOLD drift after substring fix
- NLTK data download on first use (network dependency)
- FTS5 query injection still not fully sanitized (out of scope but noted)

## Recommended Wave Structure

### Wave 1: Bug fixes + test baseline
- Fix substring match in feedback.py
- Fix reduce.py dedup boundary bug
- Tighten consolidation gate prompt
- Update all affected tests
- Revalidate _USAGE_THRESHOLD

### Wave 2: NLTK + scikit-learn integration
- Add deps to pyproject.toml
- NLTK stopwords + stemmer in feedback.py and relevance.py
- TF-IDF dedup in reduce.py
- TF-IDF pre-clustering in consolidate.py
- Tests for new NLP paths

### Wave 3: sentence-transformers integration
- Add dep to pyproject.toml (optional extra)
- Lazy import wrapper with fallback
- Embedding-based grouping in crystallizer.py
- Embedding-based thread detection in threads.py
- Semantic rehydration in relevance.py
- Tests with mocked embeddings

## Canonical References

- ARCHITECTURE.md: Hook timeout budgets, data flow paths
- CONVENTIONS.md: Error handling, import patterns, LLM call conventions
- CONCERNS.md: FTS5 injection, busy_timeout, hook latency
- TESTING.md: Mock patterns, fixture conventions, coverage gaps
