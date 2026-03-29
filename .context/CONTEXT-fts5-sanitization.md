# Context: FTS5 Query Sanitization

**Date:** 2026-03-28
**Slug:** fts5-sanitization

## Work Description

Fix FTS5 query injection — raw user/observation text passed to FTS5 MATCH without sanitizing operator keywords (`AND`, `OR`, `NOT`, `NEAR`, `*`, `^`, `:`, `"`, `+`, `-`).

## Locked Decisions

### D1: Quote each term with double-quotes
Wrap each search term in `"term"` before passing to FTS5. Double-quotes inside terms are escaped by doubling (`"""`). This makes FTS5 treat the term as a literal phrase, neutralizing operators.

### D2: Sanitize at the query-building site, not in search_fts()
Apply sanitization in `extract_query_terms()` (user_prompt_inject.py) and `find_rehydration_by_observation()` (relevance.py) — where queries are constructed. Don't modify `search_fts()` itself since it's a general-purpose method that may receive pre-formed FTS queries.

### D3: Add sanitize_fts_term() to core/storage.py
Put the helper in storage.py alongside search_fts() since it's FTS-related. Public function — callers import it.

## Files
- `core/storage.py` — add `sanitize_fts_term()` helper
- `hooks/user_prompt_inject.py` — apply sanitization in `extract_query_terms()`
- `core/relevance.py` — apply sanitization in `find_rehydration_by_observation()`
- `tests/test_storage.py` — add tests for sanitize_fts_term
- `tests/test_user_prompt_inject.py` — add FTS operator injection test
