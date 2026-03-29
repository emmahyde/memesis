# Implementation Plan: NLP Library Integration + Batch Quality Fixes

**Date:** 2026-03-28
**Context doc:** `.context/CONTEXT-nlp-integration.md`
**Slug:** nlp-integration

---

## Summary

Three waves. Wave 1 fixes three live bugs and establishes a clean test baseline before any NLP code lands — the bugs corrupt the training signal, so NLP work built on top of them inherits the corruption. Wave 2 wires in NLTK and scikit-learn, which are safe to use in any hook budget tier. Wave 3 adds sentence-transformers behind lazy guarded imports, strictly confined to the PreCompact / cron contexts per D-05.

---

## Wave 1 — Bug Fixes + Test Baseline

### Task 1.1 — Substring match fix + threshold revalidation in `feedback.py`

**Summary:** Replace the bare `w in response_lower` substring check with word-boundary regex matching, and revalidate `_USAGE_THRESHOLD` against real representative test cases.

**Files owned:**
- `core/feedback.py`

**Depends on:** none

**Decisions:** D-02

**What to do:**

In `_compute_usage_score`, the inner loop currently reads:

```python
if w in response_lower:
    score += source_weight * cls._term_specificity(w)
```

A word like `"test"` matches `"testing"`, `"attest"`, and `"contest"` — all false positives. Replace with:

```python
import re
if re.search(rf'\b{re.escape(w)}\b', response_lower):
    score += source_weight * cls._term_specificity(w)
```

The `import re` should be moved to the module-level imports alongside `json`, `sqlite3`, etc. — it is not currently imported at module level. The `re.escape` guard is required because memory titles and summaries can contain regex-special characters (e.g., parentheses, brackets, dots in domain names).

After the fix, `_USAGE_THRESHOLD = 4.0` must be revalidated. The existing test suite drives this revalidation: run the full `tests/test_feedback.py` suite and confirm that the behavioral assertions (`was_used is True` / `was_used is False`) still hold. If `test_track_usage_marks_used_when_two_keywords_match` or `test_title_match_is_strongest_signal` fail with the boundary fix, the threshold needs adjustment — document the new value and the rationale as a code comment on `_USAGE_THRESHOLD`. The threshold must not be calibrated against raw scores; the tests assert on behavior (D-10).

Also remove `_extract_keywords` (currently at line 266-271) — it is a dead method per CONCERNS.md and the NLTK integration in Wave 2 replaces the hand-rolled keyword extraction logic anyway. Removing it now avoids confusion.

**Acceptance criteria:**
- `re.search(rf'\b{re.escape(w)}\b', ...)` replaces `w in response_lower` in `_compute_usage_score`.
- `import re` is present at module level.
- `_extract_keywords` static method is removed.
- All existing `tests/test_feedback.py` tests pass (behavioral assertions only — no score value assertions).
- `test_track_usage_marks_not_used_when_fewer_than_two_keywords` passes (the primary false-positive regression test for this fix).
- `_USAGE_THRESHOLD` value is documented with a calibration comment.

---

### Task 1.2 — `reduce.py` dedup boundary fix + `processed_sessions` table

**Summary:** Add a `processed_sessions` table to `observations.db` so sessions that only produce REINFORCEs are correctly tracked, fixing the boundary bug where they get reprocessed on the next run.

**Files owned:**
- `scripts/reduce.py`

**Depends on:** none

**Decisions:** D-03

**What to do:**

The current dedup logic in `main()` reconstructs the processed set by scanning all observation `sources` columns:

```python
processed = set()
rows = conn.execute("SELECT sources FROM observations").fetchall()
for r in rows:
    for s in json.loads(r[0]):
        processed.add(s)
```

This misses sessions that triggered only REINFORCEs with no CREATEs, because `apply_operations` updates the `sources` field of existing observations but may not produce a new row that captures the session_id in the way the scan expects. Specifically: if a session only reinforces an existing observation, `sources` is updated on that row. But sessions where the LLM returns `{"create": [], "reinforce": []}` (all-skip) are never recorded anywhere — they will be reprocessed every run.

Fix: add a `processed_sessions` table in `init_db`, and write the session_id to it unconditionally at the end of `apply_operations` regardless of whether any CREATEs or REINFORCEs occurred.

```python
# In init_db, add:
conn.execute("""
    CREATE TABLE IF NOT EXISTS processed_sessions (
        session_id TEXT PRIMARY KEY,
        processed_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
""")

# In apply_operations, after conn.commit():
conn.execute(
    "INSERT OR IGNORE INTO processed_sessions (session_id) VALUES (?)",
    (session_id,),
)
conn.commit()
```

Then replace the `main()` dedup logic to query `processed_sessions` directly:

```python
processed = set(
    row[0] for row in conn.execute("SELECT session_id FROM processed_sessions").fetchall()
)
```

The old `sources`-based approach can be kept as a fallback for databases created before the migration (no `processed_sessions` table yet), using the try/except migration guard pattern from CONVENTIONS.md:

```python
try:
    processed = set(
        row[0] for row in conn.execute("SELECT session_id FROM processed_sessions").fetchall()
    )
except sqlite3.OperationalError:
    # Legacy DB without processed_sessions — fall back to sources scan
    processed = set()
    for r in conn.execute("SELECT sources FROM observations").fetchall():
        for s in json.loads(r[0]):
            processed.add(s)
```

**Acceptance criteria:**
- `processed_sessions` table is created in `init_db`.
- Every call to `apply_operations` records the `session_id` in `processed_sessions`, including sessions with empty `create` and `reinforce` arrays.
- A session processed once does not appear in `remaining` on a second invocation of `main()`.
- The legacy fallback path handles databases without the `processed_sessions` table without crashing.
- New test class `TestReduceDedup` in `tests/test_scripts.py` covers these behaviors (see Task 1.3).

---

### Task 1.3 — Tighten consolidation gate prompt + add script tests

**Summary:** Add the frequency-floor instruction to `CONSOLIDATION_GATE_PROMPT` in `scripts/consolidate.py`, and create `tests/test_scripts.py` with tests for the reduce dedup fix and the gate prompt.

**Files owned:**
- `scripts/consolidate.py`
- `tests/test_scripts.py` (new file)

**Depends on:** none (prompt change is independent; test file is new)

**Decisions:** D-04, D-10

**What to do:**

**Prompt change:** In `CONSOLIDATION_GATE_PROMPT`, the existing `FREQUENCY SIGNAL` block ends with:

```
- Frequency is evidence, not proof. A bad observation seen 10 times is still bad.
```

Add an explicit frequency-floor instruction immediately after:

```
FREQUENCY FLOOR:
- Observations seen only once (freq=1) must pass a strict gate: "Would I actively do
  something wrong next session if I didn't have this?" If the answer is not a clear yes,
  PRUNE. Single-session observations are hypotheses, not patterns.
- Do not keep freq=1 observations for completeness, hedging, or "might be useful" reasons.
```

This targets the 98.7% keep rate. The instruction makes the prune criterion actionable and asymmetric — it puts the burden of proof on freq=1 observations to justify their existence.

**New test file:** Create `tests/test_scripts.py`. Tests should not call the Anthropic API (mock it). Follow the `with patch(...)` pattern from CONVENTIONS.md.

Structure:

```
class TestReduceDedup:
    test_apply_operations_records_session_id
    test_all_skip_session_still_recorded
    test_session_not_reprocessed_after_first_run
    test_legacy_db_without_processed_sessions_table

class TestConsolidateGatePrompt:
    test_prompt_contains_frequency_floor_instruction
    test_prompt_mentions_freq1_strict_gate
```

For `TestReduceDedup`, use an in-memory SQLite database or a `tmp_path` fixture — do not test against the real `backfill-output/observations.db`. Import `init_db`, `apply_operations` directly from `scripts.reduce` (or via `importlib` since scripts are not packages — use `sys.path.insert` matching the existing pattern in `scripts/reduce.py` itself).

For `TestConsolidateGatePrompt`, assert on the presence of the key instructions in the prompt string constant — no LLM call needed:

```python
def test_prompt_contains_frequency_floor_instruction(self):
    assert "FREQUENCY FLOOR" in CONSOLIDATION_GATE_PROMPT

def test_prompt_mentions_freq1_strict_gate(self):
    assert "freq=1" in CONSOLIDATION_GATE_PROMPT or "frequency" in CONSOLIDATION_GATE_PROMPT.lower()
```

D-10 applies: assert on behavior (was a session recorded? was a session skipped?) not on internal data shapes.

**Acceptance criteria:**
- `CONSOLIDATION_GATE_PROMPT` contains `"FREQUENCY FLOOR"` and the strict-gate instruction for freq=1 observations.
- `tests/test_scripts.py` exists with `TestReduceDedup` and `TestConsolidateGatePrompt` classes.
- `TestReduceDedup.test_all_skip_session_still_recorded` passes (the core bug regression test).
- `TestReduceDedup.test_session_not_reprocessed_after_first_run` passes.
- All `TestConsolidateGatePrompt` tests pass.
- No Anthropic API calls in any test.

---

## Wave 2 — NLTK + scikit-learn Integration

### Task 2.1 — Add NLTK + sklearn deps and NLTK integration in `feedback.py` and `relevance.py`

**Summary:** Add `nltk` and `scikit-learn` to `pyproject.toml`, integrate NLTK stopwords + stemmer into `_compute_usage_score` in `feedback.py`, and stem FTS query terms in `find_rehydration_by_observation` in `relevance.py`.

**Files owned:**
- `pyproject.toml`
- `core/feedback.py`
- `core/relevance.py`

**Depends on:** Wave 1 Task 1.1 (feedback.py is stable after the substring fix; building on the corrected scorer is D-01's intent)

**Decisions:** D-05, D-06, D-07, D-08

**What to do:**

**`pyproject.toml`:** Add `nltk>=3.8` and `scikit-learn>=1.4` to `dependencies` (not `dev` — these are runtime deps needed in hooks and scripts):

```toml
dependencies = [
    "anthropic>=0.40.0",
    "nltk>=3.8",
    "scikit-learn>=1.4",
]
```

Per D-05, NLTK and sklearn are safe to import at module top-level in `core/` modules.

**`core/feedback.py` — NLTK in `_compute_usage_score`:**

Add top-level imports:

```python
import nltk
from nltk.corpus import stopwords
from nltk.stem import PorterStemmer
```

Add a module-level lazy NLTK data initializer (called once, not per-score):

```python
def _ensure_nltk_data():
    """Download NLTK stopwords corpus on first use if not present."""
    try:
        nltk.data.find('corpora/stopwords')
    except LookupError:
        try:
            nltk.download('stopwords', quiet=True)
        except Exception:
            pass  # Network unavailable — fall back to no stopword filtering

_STOPWORDS: set[str] | None = None
_STEMMER: PorterStemmer | None = None

def _get_nltk_tools() -> tuple[set[str], PorterStemmer | None]:
    """Return (stopwords_set, stemmer), initializing lazily."""
    global _STOPWORDS, _STEMMER
    if _STOPWORDS is None:
        _ensure_nltk_data()
        try:
            _STOPWORDS = set(stopwords.words('english'))
            _STEMMER = PorterStemmer()
        except Exception:
            _STOPWORDS = set()
            _STEMMER = None
    return _STOPWORDS, _STEMMER
```

Update `_compute_usage_score` to strip stopwords and stem both source and response tokens. The stemming approach: stem each source word and build a stemmed-response set once per call (not per word), so `O(S + R)` rather than `O(S × R)`:

```python
stop_words, stemmer = _get_nltk_tools()
# Pre-stem all response tokens once
if stemmer:
    response_words = re.findall(r'\b[a-z]{4,}\b', response_lower)
    stemmed_response = {stemmer.stem(w) for w in response_words if w not in stop_words}
else:
    stemmed_response = None
```

Then in the inner loop, check word-boundary match first (existing fix from Task 1.1), and if NLTK is available also check stem match:

```python
matched = bool(re.search(rf'\b{re.escape(w)}\b', response_lower))
if not matched and stemmer and w not in stop_words and stemmed_response is not None:
    matched = stemmer.stem(w) in stemmed_response
if matched:
    score += source_weight * cls._term_specificity(w)
```

Note: the word-boundary check remains the primary path. Stemming is an additional fallback, not a replacement. This prevents stemming from widening the match window too aggressively.

Per D-08, the existing `_term_specificity` remains for now. The TF-IDF specificity replacement is deferred to Task 2.2 (`scripts/` scope) because changing term weights in the core scorer mid-wave creates threshold calibration risk that is better isolated to the script context first.

**`core/relevance.py` — NLTK in `find_rehydration_by_observation`:**

Add top-level imports (reuse the same lazy pattern):

```python
import nltk
from nltk.corpus import stopwords as nltk_stopwords
from nltk.stem import PorterStemmer as NltkStemmer
```

In `find_rehydration_by_observation`, replace the current word extraction:

```python
words = [w for w in observation.split() if len(w) >= 4 and w.isalpha()]
```

With stopword-filtered and stemmed extraction:

```python
try:
    stop = set(nltk_stopwords.words('english'))
    stemmer = NltkStemmer()
    raw_words = [w.lower() for w in observation.split() if len(w) >= 4 and w.isalpha()]
    words = list({stemmer.stem(w) for w in raw_words if w not in stop})
except Exception:
    # NLTK unavailable — fall back to original extraction
    words = [w.lower() for w in observation.split() if len(w) >= 4 and w.isalpha()]
```

This deduplications stems before passing them to the FTS query, reducing query complexity and improving recall for inflected terms (e.g., `"payment"` and `"payments"` become `"payment"`).

Per CONCERNS.md, the FTS5 injection risk is pre-existing and out of scope, but note that stemmed words are all lowercase alpha tokens so they are inherently safe FTS inputs.

**Acceptance criteria:**
- `pyproject.toml` has `nltk>=3.8` and `scikit-learn>=1.4` in `dependencies`.
- `_compute_usage_score` uses NLTK stopwords + stemmer with graceful fallback when NLTK data is unavailable.
- `find_rehydration_by_observation` uses stemmed query terms with graceful fallback.
- All existing `tests/test_feedback.py` and `tests/test_relevance.py` tests pass without modification.
- New tests in `tests/test_feedback.py` (Task 2.3) cover the NLTK paths.

---

### Task 2.2 — TF-IDF near-dedup in `reduce.py` and pre-clustering in `consolidate.py`

**Summary:** Add TF-IDF cosine similarity near-duplicate detection to `scripts/reduce.py` before LLM calls, and add TF-IDF pre-clustering to `scripts/consolidate.py` to inform the LLM's merge decisions.

**Files owned:**
- `scripts/reduce.py`
- `scripts/consolidate.py`

**Depends on:** Wave 1 Tasks 1.2 and 1.3 (reduce.py and consolidate.py are stable after bug fixes; Wave 2 builds on the corrected dedup logic)

**Decisions:** D-05, D-06, D-08

**What to do:**

**`scripts/reduce.py` — TF-IDF near-duplicate detection:**

Before calling `reduce_session`, check if the session's summary is nearly identical to any recently created observation using TF-IDF cosine similarity. This is a safety net against the LLM creating near-duplicates when REINFORCEs would be more appropriate.

Add a helper function (not inside `main`, not inside `apply_operations`):

```python
def _find_near_duplicates(
    conn: sqlite3.Connection,
    new_content: str,
    threshold: float = 0.85,
) -> list[int]:
    """
    Return observation IDs with cosine similarity >= threshold to new_content.

    Uses TF-IDF on title + content. Returns [] if sklearn unavailable or
    the store has < 2 observations (degenerate case for TF-IDF).
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return []

    rows = conn.execute("SELECT id, title, content FROM observations").fetchall()
    if len(rows) < 2:
        return []

    existing_texts = [f"{r[1]} {r[2]}" for r in rows]
    all_texts = existing_texts + [new_content]

    vectorizer = TfidfVectorizer(min_df=1, stop_words='english')
    try:
        tfidf = vectorizer.fit_transform(all_texts)
    except ValueError:
        return []  # Empty vocabulary (e.g., all stop words)

    new_vec = tfidf[-1]
    existing_vecs = tfidf[:-1]
    sims = cosine_similarity(new_vec, existing_vecs).flatten()

    return [rows[i][0] for i, sim in enumerate(sims) if sim >= threshold]
```

In `apply_operations`, before inserting a new CREATE, check for near-duplicates and convert to REINFORCE if found:

```python
for obs in creates:
    text = f"{obs.get('title', '')} {obs.get('content', '')}"
    dupes = _find_near_duplicates(conn, text)
    if dupes:
        # Treat as reinforcement of the closest match instead of creating
        for oid in dupes[:1]:  # Only reinforce the closest one
            row = conn.execute("SELECT sources FROM observations WHERE id = ?", (oid,)).fetchone()
            if row:
                sources = json.loads(row[0])
                if session_id not in sources:
                    sources.append(session_id)
                conn.execute(
                    "UPDATE observations SET count = count + 1, sources = ? WHERE id = ?",
                    (json.dumps(sources), oid),
                )
        continue  # Skip the INSERT
    # Original INSERT logic follows
    conn.execute(...)
```

Print a stderr diagnostic when near-duplicates are detected (per CONVENTIONS.md stderr-for-diagnostics rule):

```python
print(f"  [tfidf] near-duplicate detected, reinforcing #{dupes[0]} instead", file=sys.stderr)
```

**`scripts/consolidate.py` — TF-IDF pre-clustering:**

Add a pre-clustering step that annotates the observation list with cluster IDs before sending to the LLM. The LLM sees the cluster annotation and can use it as a hint for merge decisions.

Add a helper:

```python
def _cluster_by_tfidf(observations: list[dict], threshold: float = 0.70) -> dict[int, int]:
    """
    Cluster observations by TF-IDF cosine similarity.

    Returns {obs_id: cluster_id}. Observations below threshold form singleton clusters.
    Returns {} if sklearn is unavailable or fewer than 2 observations.
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return {}

    if len(observations) < 2:
        return {}

    texts = [f"{o['title']} {o['content']}" for o in observations]
    vectorizer = TfidfVectorizer(min_df=1, stop_words='english')
    try:
        tfidf = vectorizer.fit_transform(texts)
    except ValueError:
        return {}

    sims = cosine_similarity(tfidf)

    # Simple union-find clustering
    parent = list(range(len(observations)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(observations)):
        for j in range(i + 1, len(observations)):
            if sims[i, j] >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    return {observations[i]['id']: find(i) for i in range(len(observations))}
```

In `format_observations`, accept an optional `clusters` dict and append a cluster annotation:

```python
def format_observations(observations: list[dict], clusters: dict[int, int] = None) -> str:
    lines = []
    for obs in observations:
        cluster_hint = ""
        if clusters and obs['id'] in clusters:
            cid = clusters[obs['id']]
            cluster_hint = f" [cluster:{cid}]"
        # ... existing formatting with cluster_hint appended to the ID line
```

Call `_cluster_by_tfidf` in `consolidate()` before building `obs_text`, and pass the result to `format_observations`. This is purely informational for the LLM — the gate prompt already handles merge decisions; the cluster hint reduces ambiguity about which observations are candidates for merging.

**Acceptance criteria:**
- `_find_near_duplicates` returns `[]` gracefully when sklearn is not installed.
- A CREATE that is ≥0.85 cosine-similar to an existing observation is converted to a REINFORCE in `apply_operations`.
- TF-IDF near-duplicate detection is logged to stderr when triggered.
- `_cluster_by_tfidf` returns `{}` gracefully when sklearn is not installed or there are < 2 observations.
- Cluster annotations appear in the formatted observations passed to the LLM in `consolidate.py`.
- New tests in `tests/test_scripts.py` (added in this task to the file started in Task 1.3) cover the TF-IDF paths.

---

### Task 2.3 — Update tests for NLTK + sklearn paths

**Summary:** Add test coverage for NLTK stopword/stemmer paths in `test_feedback.py` and `test_relevance.py`, and extend `test_scripts.py` with TF-IDF dedup tests.

**Files owned:**
- `tests/test_feedback.py`
- `tests/test_relevance.py`
- `tests/test_scripts.py`

**Depends on:** Wave 2 Tasks 2.1 and 2.2 (tests cover new code paths from those tasks)

**Decisions:** D-07, D-10

**What to do:**

Per D-10: assert on behavior (was_used, was_retrieved, was_pruned), not on raw scores or internal NLTK/sklearn data structures. NLTK stopwords and stemmer do not need mocking — they are fast and deterministic. The fallback paths (NLTK unavailable) can be tested by temporarily pointing `nltk.data.path` to a nonexistent directory.

**`tests/test_feedback.py` — add `TestNLTKUsageScoring` class:**

```
class TestNLTKUsageScoring:
    test_stemmed_variant_triggers_usage
    test_stopword_in_title_does_not_inflate_score
    test_nltk_fallback_when_data_unavailable
```

- `test_stemmed_variant_triggers_usage`: create a memory with title `"Authentication Middleware"`, then check `track_usage` with response text containing `"authenticating"` (inflected form). Assert `was_used is True`. This tests that `stemmer.stem("authentication") == stemmer.stem("authenticating")`.
- `test_stopword_in_title_does_not_inflate_score`: create a memory with title `"The Payment System"`. The word `"the"` is a stopword. Verify a response mentioning unrelated content does not trigger usage solely due to `"the"` match.
- `test_nltk_fallback_when_data_unavailable`: monkeypatch `nltk.data.find` to raise `LookupError`. Call `track_usage`. Assert no exception is raised and the return value is a valid `{memory_id: bool}` dict (graceful degradation).

**`tests/test_relevance.py` — add `TestNLTKRehydration` class:**

```
class TestNLTKRehydration:
    test_stemmed_observation_finds_archived_memory
    test_rehydration_fallback_when_nltk_unavailable
```

- `test_stemmed_observation_finds_archived_memory`: create an archived memory with title `"Payment Pipeline Locking"`. Call `find_rehydration_by_observation("payments pipeline deadlock")`. Assert the memory is returned. The stem of `"payments"` is `"payment"` — same as the stem of `"payment"` in the title. This validates that stemming improves recall for inflected forms.
- `test_rehydration_fallback_when_nltk_unavailable`: monkeypatch `nltk.data.find` to raise `LookupError`. Call `find_rehydration_by_observation(...)`. Assert no exception is raised and the return type is a list.

**`tests/test_scripts.py` — add `TestTFIDFDedup` class** (extend the file from Task 1.3):

```
class TestTFIDFDedup:
    test_near_duplicate_create_becomes_reinforce
    test_dissimilar_content_is_not_deduplicated
    test_dedup_graceful_when_sklearn_absent
```

- `test_near_duplicate_create_becomes_reinforce`: seed the DB with one observation. Construct a near-duplicate text (same content, minor variation). Call `apply_operations` with a CREATE for that text. Assert the observation count incremented (REINFORCE) and no new row was inserted.
- `test_dissimilar_content_is_not_deduplicated`: seed the DB with observations about `"Python testing"`. Call `apply_operations` with a CREATE about `"AWS Bedrock client setup"`. Assert a new row was inserted.
- `test_dedup_graceful_when_sklearn_absent`: patch the `sklearn` import to raise `ImportError`. Call `_find_near_duplicates(conn, "some text")`. Assert it returns `[]` without raising.

**Acceptance criteria:**
- All new test classes pass.
- No existing tests in any of the three files are modified or broken.
- Tests assert on behavior (D-10), not on TF-IDF vectors or NLTK internal state.
- NLTK fallback tests exercise the `except Exception` paths without requiring network access.

---

## Wave 3 — sentence-transformers Integration

### Task 3.1 — Lazy import wrapper + embedding-based grouping in `crystallizer.py`

**Summary:** Add a `_get_embeddings` lazy guarded import helper and replace tag-overlap grouping in `_group_candidates` with embedding cosine similarity clustering, falling back to the existing tag-overlap logic if embeddings are unavailable.

**Files owned:**
- `core/crystallizer.py`
- `pyproject.toml`

**Depends on:** Wave 2 Task 2.1 (pyproject.toml has NLTK/sklearn deps landed; adding sentence-transformers as an optional extra follows the same file)

**Decisions:** D-05, D-06, D-09

**What to do:**

**`pyproject.toml`:** Add a `nlp` optional extra for sentence-transformers. This is an optional extra (not a hard dependency) because model load time is 2-5s and it is only safe in PreCompact/cron contexts:

```toml
[project.optional-dependencies]
nlp = [
    "sentence-transformers>=2.7",
]
```

**`core/crystallizer.py` — lazy import wrapper:**

Per D-06, the canonical pattern is:

```python
def _get_embeddings(texts: list[str]):
    """
    Encode texts with sentence-transformers (all-MiniLM-L6-v2).

    Returns numpy array of shape (len(texts), 384), or None if
    sentence-transformers is unavailable. Caller falls back to tag-overlap.
    """
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        return model.encode(texts)
    except Exception:
        import sys
        print(
            "[crystallizer] sentence-transformers unavailable, falling back to tag-overlap",
            file=sys.stderr,
        )
        return None
```

This is a module-level function (not a method), placed just before the `Crystallizer` class definition. It follows the stderr diagnostic convention from CONVENTIONS.md.

**`_group_candidates` — embedding-based clustering:**

Replace the current greedy union-find with a two-phase approach: try embeddings first, fall back to the existing tag-overlap logic if `_get_embeddings` returns `None`. The existing tag-overlap code is preserved verbatim as the fallback — it must not be deleted.

```python
def _group_candidates(self, candidates: list[dict]) -> list[list[dict]]:
    if len(candidates) <= 2:
        return [[c] for c in candidates]

    # Phase 1: Try embedding-based clustering
    texts = [f"{c.get('title', '')} {c.get('content', '')[:200]}" for c in candidates]
    embeddings = _get_embeddings(texts)

    if embeddings is not None:
        return self._group_by_embeddings(candidates, embeddings, threshold=0.75)

    # Phase 2: Fall back to tag-overlap (original logic)
    return self._group_by_tags(candidates)
```

Extract the existing tag-overlap code into `_group_by_tags(self, candidates)` (no behavioral change, just a rename/extract). Add `_group_by_embeddings(self, candidates, embeddings, threshold)` which uses `numpy` cosine similarity (available as a transitive dep of sentence-transformers) with the same union-find pattern as the existing tag clustering:

```python
def _group_by_embeddings(
    self,
    candidates: list[dict],
    embeddings,  # numpy array (N, 384)
    threshold: float,
) -> list[list[dict]]:
    import numpy as np
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normed = embeddings / np.maximum(norms, 1e-9)
    sims = normed @ normed.T  # (N, N) cosine similarity matrix

    n = len(candidates)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(candidates[i])
    return list(groups.values())
```

Note: `numpy` is not a new dependency — it is a transitive dependency of both scikit-learn (Wave 2) and sentence-transformers, so `import numpy` is safe here.

**Acceptance criteria:**
- `pyproject.toml` has `sentence-transformers>=2.7` under `[project.optional-dependencies] nlp`.
- `_get_embeddings` uses the lazy call-site import pattern (no top-level import).
- `_group_candidates` calls `_get_embeddings` and falls back to `_group_by_tags` when it returns `None`.
- The original tag-overlap logic is preserved as `_group_by_tags` — all existing `test_crystallizer.py` tests that exercise grouping continue to pass.
- New tests in `tests/test_crystallizer.py` (Task 3.3) cover the embedding path with mocked embeddings.
- No import of `sentence_transformers` at module top-level (verified by grepping the file).

---

### Task 3.2 — Embedding-based thread detection in `threads.py` + semantic rehydration in `relevance.py`

**Summary:** Add embedding-based cluster detection as a fallback-first alternative to tag-overlap in `ThreadDetector`, and add semantic similarity matching for archived memory rehydration in `RelevanceEngine`.

**Files owned:**
- `core/threads.py`
- `core/relevance.py`

**Depends on:** Wave 2 Task 2.1 (relevance.py has NLTK changes from Task 2.1; Task 3.2 builds on top of them)

**Decisions:** D-05, D-06, D-09

**What to do:**

**`core/threads.py` — embedding-based clustering:**

Add the same `_get_embeddings` lazy import helper used in `crystallizer.py`. Per D-06 this is a per-file call-site pattern — do not import from `crystallizer`. The helper is identical:

```python
def _get_embeddings(texts: list[str]):
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        return model.encode(texts)
    except Exception:
        import sys
        print(
            "[threads] sentence-transformers unavailable, falling back to tag-overlap",
            file=sys.stderr,
        )
        return None
```

In `ThreadDetector._cluster_by_tags`, this method currently does pure tag-overlap union-find. Rename it to `_cluster_by_tags` (keep name) and add a new `_cluster_by_embeddings` method with the same union-find structure as `Crystallizer._group_by_embeddings`.

In `detect_threads`, before calling `_cluster_by_tags`, try embedding-based clustering first:

```python
# Try embedding clustering first
texts = [f"{m.get('title', '')} {m.get('content', '')[:200]}" for m in full_candidates]
embeddings = _get_embeddings(texts)

if embeddings is not None:
    clusters = self._cluster_by_embeddings(full_candidates, embeddings, threshold=0.70)
else:
    clusters = self._cluster_by_tags(full_candidates)
```

The threshold for threads (0.70) is lower than for crystallization (0.75) because threads require topical overlap but not necessarily the same level of content convergence as crystallization candidates.

**`core/relevance.py` — semantic rehydration:**

Add a `_find_semantic_matches` private method that uses sentence-transformers to find archived memories semantically similar to a new observation. This augments (does not replace) the FTS-based `find_rehydration_by_observation`.

```python
def _find_semantic_matches(
    self,
    observation: str,
    archived_memories: list[dict],
    threshold: float = 0.65,
) -> list[dict]:
    """
    Find archived memories semantically similar to the observation.

    Uses sentence-transformers (all-MiniLM-L6-v2). Returns [] if
    embeddings are unavailable or the archived list is empty.

    Only called from find_rehydration_by_observation as a supplement
    to FTS results. Caller deduplicates by memory ID.
    """
    if not archived_memories:
        return []

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        model = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        import sys
        print(
            "[relevance] sentence-transformers unavailable, skipping semantic rehydration",
            file=sys.stderr,
        )
        return []

    texts = [f"{m.get('title', '')} {m.get('summary', '')}" for m in archived_memories]
    try:
        all_texts = [observation] + texts
        embeddings = model.encode(all_texts)
        obs_vec = embeddings[0:1]
        mem_vecs = embeddings[1:]

        norms_obs = np.linalg.norm(obs_vec, axis=1, keepdims=True)
        norms_mem = np.linalg.norm(mem_vecs, axis=1, keepdims=True)
        sims = (obs_vec / np.maximum(norms_obs, 1e-9)) @ (mem_vecs / np.maximum(norms_mem, 1e-9)).T
        sims = sims.flatten()
    except Exception:
        return []

    matches = []
    for i, sim in enumerate(sims):
        if sim >= threshold:
            memory = archived_memories[i]
            memory["semantic_similarity"] = float(sim)
            matches.append(memory)

    return matches
```

Update `find_rehydration_by_observation` to call `_find_semantic_matches` in addition to FTS, then merge and deduplicate by memory ID. The FTS path runs first; semantic results are appended only if not already present:

```python
# Existing FTS matches
matches = [m for m in fts_results if m.get("archived_at") and not m.get("subsumed_by")]
seen_ids = {m["id"] for m in matches}

# Supplement with semantic matches
archived_pool = [
    m for m in self.store.list_archived()
    if not m.get("subsumed_by") and m["id"] not in seen_ids
]
semantic = self._find_semantic_matches(observation, archived_pool)
for m in semantic:
    if m["id"] not in seen_ids:
        relevance = self.compute_relevance(m)
        m["relevance"] = relevance
        matches.append(m)
        seen_ids.add(m["id"])
```

Per D-05: `find_rehydration_by_observation` is called from `Consolidator.consolidate_session` which runs in PreCompact (30s budget) and cron (no budget). It is safe to use sentence-transformers here. However, `find_rehydration_by_observation` must not be called from `SessionStart` or `UserPromptSubmit`. The existing call sites (only `pre_compact.py` line ~140 via the consolidator) are already in the correct context — no hook boundary changes are needed.

**Acceptance criteria:**
- `threads.py` has `_get_embeddings` as a module-level function with lazy call-site import.
- `detect_threads` tries embedding clustering first, falls back to `_cluster_by_tags`.
- `_cluster_by_tags` is unchanged in behavior (all existing `TestThreadDetector` tests pass).
- `relevance.py` has `_find_semantic_matches` with lazy call-site import.
- `find_rehydration_by_observation` supplements FTS results with semantic matches.
- No `sentence_transformers` import at module top-level in either file (verified by grep).
- New tests in Task 3.3 cover both new code paths with mocked embeddings.

---

### Task 3.3 — Tests for sentence-transformers paths

**Summary:** Add mocked embedding tests to `test_crystallizer.py`, `test_threads.py`, and `test_relevance.py` covering the new embedding-based code paths.

**Files owned:**
- `tests/test_crystallizer.py`
- `tests/test_threads.py`
- `tests/test_relevance.py`

**Depends on:** Wave 3 Tasks 3.1 and 3.2 (tests cover the new embedding code paths)

**Decisions:** D-09, D-10

**What to do:**

Per D-10: mock `SentenceTransformer.encode` at the call site, returning deterministic numpy arrays. The mock target path is `sentence_transformers.SentenceTransformer` (the class itself), with `return_value.encode` returning the deterministic array.

**Deterministic embedding helper** (define once in each test file or in a shared fixture in `conftest.py`):

```python
import numpy as np

def _fake_embeddings(n: int, dim: int = 384, seed: int = 42) -> np.ndarray:
    """Return seeded random embeddings normalized to unit vectors."""
    rng = np.random.default_rng(seed)
    raw = rng.standard_normal((n, dim))
    norms = np.linalg.norm(raw, axis=1, keepdims=True)
    return raw / np.maximum(norms, 1e-9)

def _cluster_embeddings(n: int, cluster_size: int = 2) -> np.ndarray:
    """Return embeddings where the first cluster_size items are highly similar (cosine > 0.9)."""
    rng = np.random.default_rng(0)
    base = rng.standard_normal(384)
    base /= np.linalg.norm(base)
    similar = base + rng.standard_normal((cluster_size, 384)) * 0.05
    dissimilar = rng.standard_normal((n - cluster_size, 384))
    all_vecs = np.vstack([similar, dissimilar])
    norms = np.linalg.norm(all_vecs, axis=1, keepdims=True)
    return all_vecs / np.maximum(norms, 1e-9)
```

**`tests/test_crystallizer.py` — add `TestEmbeddingGrouping` class:**

```
class TestEmbeddingGrouping:
    test_similar_candidates_grouped_by_embeddings
    test_dissimilar_candidates_not_grouped
    test_embedding_fallback_when_unavailable
```

- `test_similar_candidates_grouped_by_embeddings`: patch `sentence_transformers.SentenceTransformer` with a mock whose `.encode()` returns `_cluster_embeddings(3, cluster_size=2)`. Create 3 candidates. Call `_group_candidates`. Assert that the two similar candidates (indices 0-1) land in the same group.
- `test_dissimilar_candidates_not_grouped`: patch `.encode()` to return `_fake_embeddings(3)` (random, all dissimilar). Assert each candidate forms its own group (3 singleton groups).
- `test_embedding_fallback_when_unavailable`: patch `sentence_transformers.SentenceTransformer` to raise `ImportError`. Call `_group_candidates` with 3 candidates with matching tags. Assert grouping still works via the tag-overlap fallback (same result as the existing tag-based tests).

**`tests/test_threads.py` — add `TestEmbeddingClustering` class:**

```
class TestEmbeddingClustering:
    test_semantically_similar_memories_cluster
    test_semantically_dissimilar_memories_do_not_cluster
    test_clustering_fallback_when_unavailable
```

- `test_semantically_similar_memories_cluster`: create 3 memories with temporal spread. Patch `.encode()` to return `_cluster_embeddings(3, cluster_size=3)` (all similar). Call `detect_threads`. Assert at least one cluster is returned.
- `test_semantically_dissimilar_memories_do_not_cluster`: patch `.encode()` to return `_fake_embeddings(3)`. Create memories with temporal spread but no tag overlap. Assert no cluster forms (all singleton).
- `test_clustering_fallback_when_unavailable`: patch `sentence_transformers.SentenceTransformer` to raise `ImportError`. Create memories with tag overlap and temporal spread. Assert `detect_threads` still returns clusters via the tag-overlap fallback.

**`tests/test_relevance.py` — add `TestSemanticRehydration` class:**

```
class TestSemanticRehydration:
    test_semantic_match_supplements_fts
    test_semantic_match_deduplicates_fts_results
    test_semantic_rehydration_fallback_when_unavailable
```

- `test_semantic_match_supplements_fts`: create an archived memory with title `"Obscure Zymurgical Process"` (will not match FTS for a software observation). Patch `.encode()` to return `_cluster_embeddings(2, cluster_size=2)` (observation and memory are similar). Call `find_rehydration_by_observation("software deployment pipeline")`. Assert the memory is returned (semantic path found it even though FTS would not).
- `test_semantic_match_deduplicates_fts_results`: create an archived memory that FTS would match. Patch embeddings to also mark it as similar. Call `find_rehydration_by_observation`. Assert the memory appears exactly once in the results.
- `test_semantic_rehydration_fallback_when_unavailable`: patch `sentence_transformers.SentenceTransformer` to raise `ImportError`. Create an archived memory that FTS matches. Assert `find_rehydration_by_observation` still returns it (FTS path is unaffected by the embedding fallback).

**Acceptance criteria:**
- All new test classes pass.
- Mocks use `patch("sentence_transformers.SentenceTransformer")` targeting the import call site within the module (per D-10 and TESTING.md mock patterns).
- No test hits a real sentence-transformers model or network.
- All existing tests in `test_crystallizer.py`, `test_threads.py`, and `test_relevance.py` continue to pass.

---

## File Ownership Map

| File | Wave | Task | Mode |
|------|------|------|------|
| `core/feedback.py` | 1 | 1.1 | modify (substring fix, remove dead method) |
| `core/feedback.py` | 2 | 2.1 | modify (NLTK stopwords + stemmer) |
| `scripts/reduce.py` | 1 | 1.2 | modify (processed_sessions table + dedup fix) |
| `scripts/reduce.py` | 2 | 2.2 | modify (TF-IDF near-dedup in apply_operations) |
| `scripts/consolidate.py` | 1 | 1.3 | modify (frequency floor in prompt) |
| `scripts/consolidate.py` | 2 | 2.2 | modify (TF-IDF pre-clustering in consolidate()) |
| `tests/test_scripts.py` | 1 | 1.3 | create (TestReduceDedup + TestConsolidateGatePrompt) |
| `tests/test_scripts.py` | 2 | 2.2, 2.3 | modify (extend with TestTFIDFDedup) |
| `pyproject.toml` | 2 | 2.1 | modify (add nltk + sklearn to dependencies) |
| `pyproject.toml` | 3 | 3.1 | modify (add sentence-transformers to optional nlp extra) |
| `core/relevance.py` | 2 | 2.1 | modify (NLTK stems in find_rehydration_by_observation) |
| `core/relevance.py` | 3 | 3.2 | modify (add _find_semantic_matches + supplement FTS) |
| `core/crystallizer.py` | 3 | 3.1 | modify (_get_embeddings helper + _group_by_embeddings) |
| `core/threads.py` | 3 | 3.2 | modify (_get_embeddings helper + _cluster_by_embeddings) |
| `tests/test_feedback.py` | 1 | 1.1 | modify (re-run as validation; update if threshold changes) |
| `tests/test_feedback.py` | 2 | 2.3 | modify (add TestNLTKUsageScoring) |
| `tests/test_relevance.py` | 2 | 2.3 | modify (add TestNLTKRehydration) |
| `tests/test_relevance.py` | 3 | 3.3 | modify (add TestSemanticRehydration) |
| `tests/test_crystallizer.py` | 3 | 3.3 | modify (add TestEmbeddingGrouping) |
| `tests/test_threads.py` | 3 | 3.3 | modify (add TestEmbeddingClustering) |

---

## Cross-Wave Ownership Handoffs

| File | Earlier task | What earlier task does | Later task | What later task does | Constraint |
|------|--------------|------------------------|------------|----------------------|------------|
| `core/feedback.py` | Wave 1 Task 1.1 | Fixes substring match, removes `_extract_keywords`, adds `import re` at module level | Wave 2 Task 2.1 | Adds NLTK imports, `_get_nltk_tools`, updates `_compute_usage_score` | Wave 2 must build on the corrected word-boundary match — must not revert to `w in response_lower`. The NLTK stem check is additive to the `re.search` check, not a replacement. |
| `scripts/reduce.py` | Wave 1 Task 1.2 | Adds `processed_sessions` table + records session_id in `apply_operations` | Wave 2 Task 2.2 | Adds `_find_near_duplicates` and TF-IDF dedup logic inside `apply_operations` | Wave 2 adds to `apply_operations` before the existing INSERT logic. Must preserve the `processed_sessions` recording added in Wave 1 — it must still fire even when a CREATE is converted to a REINFORCE. |
| `scripts/consolidate.py` | Wave 1 Task 1.3 | Adds FREQUENCY FLOOR to `CONSOLIDATION_GATE_PROMPT` | Wave 2 Task 2.2 | Adds `_cluster_by_tfidf` and cluster annotations in `format_observations` | Wave 2 must not remove or weaken the FREQUENCY FLOOR instruction added in Wave 1. |
| `tests/test_scripts.py` | Wave 1 Task 1.3 | Creates file with `TestReduceDedup` + `TestConsolidateGatePrompt` | Wave 2 Tasks 2.2, 2.3 | Appends `TestTFIDFDedup` class | Wave 2 appends; must not remove or modify Wave 1's test classes. |
| `pyproject.toml` | Wave 2 Task 2.1 | Adds `nltk>=3.8` and `scikit-learn>=1.4` to `dependencies` | Wave 3 Task 3.1 | Adds `sentence-transformers>=2.7` to `[project.optional-dependencies] nlp` | Wave 3 adds a new optional-dependencies table key — must not remove the Wave 2 `dependencies` additions. |
| `core/relevance.py` | Wave 2 Task 2.1 | Adds NLTK imports + stemming in `find_rehydration_by_observation` | Wave 3 Task 3.2 | Adds `_find_semantic_matches` + supplements FTS results in `find_rehydration_by_observation` | Wave 3 builds on the Wave 2 stemmed FTS query. The semantic supplement adds to the `matches` list after FTS — must not replace the FTS path or the NLTK stemming. |
| `tests/test_relevance.py` | Wave 2 Task 2.3 | Adds `TestNLTKRehydration` class | Wave 3 Task 3.3 | Adds `TestSemanticRehydration` class | Wave 3 appends. Must not modify `TestNLTKRehydration` tests. |
| `tests/test_feedback.py` | Wave 1 Task 1.1 | Re-validates existing tests after substring fix; adjusts `_USAGE_THRESHOLD` comment if needed | Wave 2 Task 2.3 | Appends `TestNLTKUsageScoring` class | Wave 2 must not change existing test assertions. If Wave 1 adjusted the threshold constant, Wave 2 must use the updated constant value in any score-adjacent reasoning, though tests still assert on behavior not score values. |

---

## Decision Traceability

| Decision | Implemented in | Notes |
|----------|----------------|-------|
| D-01 Bug fixes land first, NLP second | Wave 1 (all tasks) | All three bugs fixed and test baseline established before any NLTK/sklearn/sentence-transformers code. |
| D-02 Substring match fix + threshold revalidation | Wave 1 Task 1.1 | `re.search(rf'\b{re.escape(w)}\b', ...)` replaces `w in response_lower`. `_USAGE_THRESHOLD` revalidated against behavioral tests. |
| D-03 reduce.py dedup — `processed_sessions` table | Wave 1 Task 1.2 | New `processed_sessions` table; `apply_operations` always records session_id; legacy fallback for old DBs. |
| D-04 Consolidation gate — frequency floor | Wave 1 Task 1.3 | `FREQUENCY FLOOR` instruction added to `CONSOLIDATION_GATE_PROMPT` with explicit strict-gate wording for freq=1. |
| D-05 Library tiering by hook budget | Wave 2 Tasks 2.1–2.3 (NLTK/sklearn); Wave 3 Tasks 3.1–3.3 (sentence-transformers) | NLTK and sklearn imported at module top-level in `core/`. sentence-transformers only in `core/crystallizer.py`, `core/threads.py`, `core/relevance.py` (all called from PreCompact/cron only). No sentence-transformers import in `hooks/session_start.py` or `hooks/user_prompt_inject.py`. |
| D-06 Lazy guarded imports for heavy libraries | Wave 3 Tasks 3.1, 3.2 | All sentence-transformers imports use the call-site `try/except` pattern. Module-level `import sentence_transformers` is forbidden in all files. |
| D-07 NLTK integration points | Wave 2 Task 2.1 | NLTK stopwords + PorterStemmer in `feedback.py` `_compute_usage_score` and in `relevance.py` `find_rehydration_by_observation`. |
| D-08 scikit-learn integration points | Wave 2 Task 2.2 | TF-IDF cosine dedup in `reduce.py`; TF-IDF pre-clustering in `consolidate.py`. `_term_specificity` replacement deferred — TF-IDF specificity in the core scorer is a separate calibration risk. |
| D-09 sentence-transformers integration points | Wave 3 Tasks 3.1, 3.2 | Embedding-based grouping in `crystallizer.py`; embedding thread detection in `threads.py`; semantic rehydration in `relevance.py`. Model: `all-MiniLM-L6-v2`. Fallback to TF-IDF/tag-overlap when unavailable. |
| D-10 Test conventions — assert on behavior not scores | Wave 1 Task 1.1 (threshold validation); Wave 2 Task 2.3; Wave 3 Task 3.3 | All tests assert `was_used is True/False`, `was_retrieved`, `was_pruned` — not raw float values. Embedding mocks return deterministic numpy arrays. NLTK not mocked (fast + deterministic). |
