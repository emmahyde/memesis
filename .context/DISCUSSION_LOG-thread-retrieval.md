# Discussion Log: Wire Narrative Threads into Retrieval Pipeline

**Date:** 2026-03-28
**Mode:** Panel discussion (--auto)
**Stakeholders:**

- Kai, retrieval/search engineer (owns retrieval.py + storage.py) — bias: query precision + budget discipline
- Mira, memory lifecycle architect (designed 4-stage progression) — bias: architectural coherence
- Dev, performance pragmatist (maintains hooks) — bias: simplicity + zero latency regression

---

## Wave 1: Initial Positions

### Kai
**Approach:** Add narrative_threads_fts FTS5 table, search_threads_fts() method, replace N+1 with direct FTS query using tier2 titles + project context. Separate THREAD_BUDGET_CHARS (~2% context, ~16K chars).
**Top concern:** Thread narratives unbounded — three threads could silently push inject 20-30K over budget.
**Tasks:** Schema migration, storage query, budget constant, FTS query sanitization, tests.

### Mira
**Approach:** Threads need own relevance score (composite of member relevance + independent last_surfaced_at decay). New get_threads_for_context() that scores/filters all threads independently.
**Top concern:** Thread relevance must not shadow member archival — arcs outlive individual memories.
**Tasks:** Schema migration (last_surfaced_at, surface_count, relevance_score), thread relevance in RelevanceEngine, get_threads_for_context(), record_thread_surface(), thread archival, tests.

### Dev
**Approach:** Replace N+1 loop with batch query. FTS on threads NOT in injection hot path — only active_search (Tier 3).
**Top concern:** Batch method must return full rows including narrative, or falls back to N+1.
**Tasks:** FTS table, batch query, replace loop, search_threads_fts for Tier 3 only, FTS sync, tests with query count assertions.

---

## Wave 2: Debate and Convergence

### Kai (revised)
**Concedes:** FTS in injection path not needed. Batch query is the right fix.
**Agrees:** last_surfaced_at worth adding (timestamp only, no scoring weight).
**Still disagrees:** No independent thread scoring. Threads without member memories in context is "reading the epilogue without the book."
**Revised approach:** Batch query + separate thread budget + optional FTS in active_search only.

### Mira (revised)
**Concedes:** Independent thread scoring premature. No stored relevance_score. FTS on threads scoped to Tier 3 only.
**Agrees:** Batch query fixes N+1. Budget cap needed (~16K).
**Still disagrees:** Thread budget sort should be shortest-first (maximize distinct arcs). Active_search should surface thread membership alongside memory results.
**Revised approach:** Batch query + separate budget (shortest-first) + last_surfaced_at + remove dead piggyback code.

### Dev (revised)
**Concedes:** N+1 worth fixing now (not just "at scale"). last_surfaced_at worth adding.
**Agrees:** Membership-based injection is correct signal. No independent scoring.
**Still disagrees:** Budget should be subordinate to Tier-2, not additive. Per-narrative truncation (800 chars) is simpler than a separate budget constant.
**Revised approach:** Batch query + 800-char per-narrative cap + last_surfaced_at (lazy update).

---

## Consensus

**Unanimous agreement:**
1. Fix N+1 with batch query (`get_threads_for_memories_batch`)
2. Membership-based injection preserved (not independent scoring)
3. Add `last_surfaced_at` column to narrative_threads
4. No stored `relevance_score` — premature without feedback data
5. No FTS on threads in this iteration
6. Per-operation SQLite connections throughout

**Majority agreement (2-1):**
7. Separate thread budget (Kai+Mira) vs subordinate truncation (Dev) — resolved as separate budget at conservative 8K chars with 1K per-narrative cap (compromise)

## Unresolved Disagreements

**D1 (resolved via compromise): Budget mechanism.** Kai wanted 16K separate, Dev wanted 800-char truncation subordinate to Tier-2, Mira wanted 16K with shortest-first. Compromise: 8K separate budget + 1K per-narrative cap + shortest-first sort. Conservative enough for Dev, structured enough for Kai, arc-maximizing for Mira.

**D2 (deferred): Thread FTS in active_search.** All agree it belongs in Tier 3 only. Specifics of the API (opt-in parameter vs always-on, thread-alongside-members vs standalone results) deferred to a future iteration when there's usage data.

**D3 (deferred): Thread archival.** All agree threads should eventually be archivable. Whether via LifecycleManager (Dev) or RelevanceEngine (Mira) is deferred. The `last_surfaced_at` column enables either approach.
