# Panel Wave 1: Initial Positions — Thread Retrieval

## Kai (Retrieval/Search Engineer)

**Approach:** Add `narrative_threads_fts` FTS5 table, `search_threads_fts()` storage method, replace N+1 piggyback with direct FTS query using tier2 titles + project context. Separate thread budget (THREAD_BUDGET_CHARS = ~2% of context window, ~16K chars).

**Tasks:** S1 schema migration, S2 storage query, S3 budget constant + retrieval refactor, S4 FTS query sanitization, S5 tests.

**Top concern:** Thread narratives are unbounded — a single thread could be 10K+ chars. Without explicit budget, three threads silently push inject 20-30K over budget.

**Must follow:** Per-operation SQLite connections. No shared conn state.

**Reuse:** search_fts JOIN pattern, _fts_insert/_fts_delete helpers, get_crystallized_for_context's greedy budget loop.

---

## Mira (Lifecycle Architect)

**Approach:** Threads need their own relevance score — composite of member relevance weighted toward most recent, plus independent last_surfaced_at decay. New get_threads_for_context() that scores/filters all threads directly, not via tier-2 membership.

**Tasks:** Schema migration (add last_surfaced_at, surface_count, relevance_score), thread relevance scoring in RelevanceEngine, get_threads_for_context() in RetrievalEngine, record_thread_surface() in MemoryStore, thread archival, tests.

**Top concern:** Thread relevance must not just shadow member archival — threads synthesize durable arcs that outlive individual memories. Needs a floor so archived members don't kill the thread.

**Must follow:** Per-operation connections, log surfacing before building context string, ValueError for not-found.

**Reuse:** RelevanceEngine._days_since_last_activity(), archive_stale() pattern, record_injection() as model for record_thread_surface().

---

## Dev (Performance Pragmatist)

**Approach:** Replace N+1 loop with batch query (get_threads_for_memories_batch). FTS on threads is fine but must NOT be in inject_for_session hot path — only in active_search (Tier 3). Independent matchability via FTS feeds search, not injection.

**Tasks:** T1 FTS table, T2 batch query method, T3 replace loop in _get_thread_narratives, T4 search_threads_fts for Tier 3 only, T5 FTS sync, T6 tests with query count assertions.

**Top concern:** Batch method must return full thread rows including narrative, not stubs — otherwise falls back to N+1. Return contract must be explicit.

**Must follow:** Per-operation connections, parameterized IN clause (not string interpolation), manual FTS sync pattern.

**Reuse:** get_threads_for_memory SQL as batch base, search_fts as template, _fts_insert/_fts_delete helpers.
