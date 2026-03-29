# Roadmap: Memesis Memory Intelligence

## Overview

Twenty focused phases moving from tech debt cleanup through retrieval foundation, observation quality, memory lifecycle, and advanced retrieval — each phase delivers one complete, verifiable capability. Six future phases cover prospective memory, sensory fusion, and advanced learning. The project transforms a keyword-search memory plugin into a living system where retrieval feels like recognition.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3...): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

### v1 — Eval Framework

- [x] **Phase 0.5: AI Eval Harness** *(INSERTED)* - Build eval framework FIRST so every subsequent phase has a measurable baseline (completed 2026-03-29)

### v1 — Cleanup

- [x] **Phase 1: Commit ORM Migration** - Commit Peewee migration with atomic commits and research files on disk (completed pre-roadmap)
- [x] **Phase 2: Write Research Files** - Write all 5 research files to disk from agent output transcripts (completed pre-roadmap)
- [x] **Phase 3: Remove file_path Field** - Remove `file_path` from Memory model (no migration needed) (completed 2026-03-29)
- [x] **Phase 4: Stop Creating Stage Directories** - Stop creating stale consolidated/crystallized/instinctive dirs in database.py (completed 2026-03-29)
- [x] **Phase 5: NarrativeThread Timestamps** - Add timestamp defaults to NarrativeThread model (completed 2026-03-29)
- [x] **Phase 6: Remove Unused Imports** - Remove BooleanField, CharField, ForeignKeyField from models.py (completed 2026-03-29)

### v1 — Foundation

- [x] **Phase 7: Hybrid RRF Retrieval** - Fuse FTS5 BM25 + sqlite-vec KNN via Reciprocal Rank Fusion (completed 2026-03-29)
- [x] **Phase 8: Prompt-Aware Tier 2 Injection** - Wire user prompt text into Tier 2 injection (currently context-free) (completed 2026-03-29)
- [ ] **Phase 9: Thompson Sampling Selection** - Select memories via Beta(usage+1, unused+1) from stdlib random
- [ ] **Phase 10: Provenance Signals** - Add "established across N sessions over M weeks" metadata at injection time

### v1 — Observation Quality

- [ ] **Phase 11: OrientingDetector** - Rule-based detector for corrections, emphasis, error spikes, pacing breaks
- [ ] **Phase 12: Habituation Baseline** - Per-project event frequency model suppressing routine events
- [ ] **Phase 13: Somatic Markers** - Valence classification (neutral/friction/surprise/delight) with importance bump
- [ ] **Phase 14: Replay Priority** - Sort observations by salience before presenting to consolidation LLM

### v1 — Memory Lifecycle

- [ ] **Phase 15: SM-2 Spaced Injection** - Three new fields + hard suppression when next_injection_due is in the future
- [ ] **Phase 16: Reconsolidation at PreCompact** - Update injected memories when session confirms/contradicts/refines them
- [ ] **Phase 17: Saturation Decay** - Penalize high injection_count + low usage_count in relevance formula
- [ ] **Phase 18: Integration Factor** - Accelerate decay for isolated memories with no threads, tags, or reinforcement

### v1 — Advanced Retrieval

- [ ] **Phase 19: 1-Hop Graph Expansion** - Expand hybrid search results to thread neighbors via memory_edges table
- [ ] **Phase 20: Ghost Coherence Check** - Periodic LLM check comparing self-model claims against memory evidence

### v2 — Future Phases

- [ ] **Phase 21: Prospective Memory Table** - "When X happens, remind me Y" via prospective_memories table
- [ ] **Phase 22: Intent Detection** - Flag "next time / remind me / when we're working on" language in observations
- [ ] **Phase 23: ContextPercept Sensory Fusion** - Fuse user text + errors + tests + git state into joint signal
- [ ] **Phase 24: ActivePrimeSet** - Injected memories prime the observation gate with per-turn decay
- [ ] **Phase 25: Context-Conditioned Importance** - Stop penalizing memories for wrong-project injections
- [ ] **Phase 26: Constitutive Memory Tagging** - Identity/preference memories resist crystallization freezing

## Phase Details

### Phase 0.5: AI Eval Harness *(INSERTED)*
**Goal**: Build the measurement system before changing anything — so every subsequent phase has a baseline to improve against
**Depends on**: Nothing (first phase)
**Requirements**: EVAL-01
**Success Criteria** (what must be TRUE):
  1. LongMemEval 500-question benchmark is integrated and runnable against our retrieval pipeline
  2. Internal metrics harness computes: precision@k, MRR, consolidation prune accuracy, injection utility rate
  3. Baseline scores are captured against current FTS-only retrieval and logged to `.planning/eval-baseline.json`
  4. A verifier hook exists that runs the eval suite after each phase and logs the delta from baseline
  5. `pytest eval/ -q` runs the full suite in under 60 seconds
**Plans**: 3 plans

Plans:
- [ ] 00.5-01-PLAN.md — Internal metrics module (precision@k, MRR, prune accuracy, injection utility rate) + conftest guard
- [ ] 00.5-02-PLAN.md — LongMemEval adapter with 10-question fixture and scoring logic
- [ ] 00.5-03-PLAN.md — Baseline capture script + verifier hook + eval-baseline.json

### Phase 1: Commit ORM Migration
**Goal**: The Peewee ORM migration is committed to git in clean, atomic commits, and the agent output transcripts exist as research files on disk
**Depends on**: Nothing (first phase)
**Requirements**: CLEAN-01
**Success Criteria** (what must be TRUE):
  1. `git log` shows atomic commits covering each migration task (1, 2, 3) with clear messages
  2. Running `python -c "from memesis.models import Memory"` succeeds without import errors
  3. No uncommitted changes remain to ORM-related files after phase completes
**Plans**: TBD

### Phase 2: Write Research Files
**Goal**: All five research summaries produced during the March 28 session are persisted as markdown files in .context/research/
**Depends on**: Phase 1
**Requirements**: CLEAN-02
**Success Criteria** (what must be TRUE):
  1. Five files exist under `.context/research/` matching the filenames referenced in PROJECT.md
  2. Each file is non-empty and contains structured research content (not placeholder text)
  3. Files are tracked by git (not .gitignored)
**Plans**: TBD

### Phase 3: Remove file_path Field
**Goal**: The `file_path` column is removed from the Memory model and no code references it
**Depends on**: Phase 1
**Requirements**: CLEAN-03
**Success Criteria** (what must be TRUE):
  1. `Memory._meta.fields` does not contain `file_path`
  2. Grep across the codebase finds zero references to `memory.file_path` or `Memory.file_path`
  3. All existing tests pass after the removal
**Plans**: TBD

### Phase 4: Stop Creating Stage Directories
**Goal**: database.py no longer creates consolidated/, crystallized/, or instinctive/ directories at startup
**Depends on**: Phase 1
**Requirements**: CLEAN-04
**Success Criteria** (what must be TRUE):
  1. Running the plugin init code against an empty project does not create those three directories
  2. Any existing logic that wrote files to those paths is removed or redirected
  3. The database initializes cleanly without filesystem side effects
**Plans**: TBD

### Phase 5: NarrativeThread Timestamps
**Goal**: NarrativeThread model has `created_at` and `updated_at` with auto-populated defaults so new threads are never missing timestamps
**Depends on**: Phase 1
**Requirements**: CLEAN-05
**Success Criteria** (what must be TRUE):
  1. Creating a NarrativeThread without specifying timestamps produces a record with both fields populated
  2. `updated_at` reflects the last save time when a thread is modified
  3. Existing threads without timestamps are not broken (nullable or backfilled)
**Plans**: TBD

### Phase 6: Remove Unused Imports
**Goal**: models.py imports only what it uses — BooleanField, CharField, and ForeignKeyField are removed if no model uses them
**Depends on**: Phase 1
**Requirements**: CLEAN-06
**Success Criteria** (what must be TRUE):
  1. `python -m py_compile memesis/models.py` produces no warnings
  2. Running `rg "BooleanField|CharField|ForeignKeyField" memesis/models.py` returns no matches
  3. All model instantiation tests still pass
**Plans**: TBD

### Phase 7: Hybrid RRF Retrieval
**Goal**: Memory retrieval fuses FTS5 BM25 keyword ranks and sqlite-vec KNN vector ranks using Reciprocal Rank Fusion — no score normalization required
**Depends on**: Phases 1-6
**Requirements**: FOUND-01
**Success Criteria** (what must be TRUE):
  1. A retrieval query returns results ranked by RRF score, not raw BM25 or cosine alone
  2. Memories that rank high in FTS but low in vector (or vice versa) are not penalized — they appear in results
  3. Retrieval completes within 500ms on a corpus of 1000 memories
  4. The implementation adds zero new dependencies beyond what is already in the venv
**Plans**: 2 plans

Plans:
- [ ] 07-01-PLAN.md — Core hybrid_search() RRF algorithm + unit tests
- [ ] 07-02-PLAN.md — Wire hybrid search into all retrieval paths + performance test

### Phase 8: Prompt-Aware Tier 2 Injection
**Goal**: Tier 2 injection uses the actual user prompt text as a retrieval signal — not just project name matching
**Depends on**: Phase 7
**Requirements**: FOUND-02
**Success Criteria** (what must be TRUE):
  1. The UserPromptSubmit hook extracts the prompt text and passes it to the Tier 2 retrieval path
  2. A query about "sqlite-vec" surfaces memories tagged with vector/embedding content that project-only matching would miss
  3. The 500ms latency budget is still met with prompt text in the retrieval path
**Plans**: 1 plan

Plans:
- [ ] 08-01-PLAN.md — Wire Tier 2 crystallized retrieval into UserPromptSubmit hook + tests

### Phase 9: Thompson Sampling Selection
**Goal**: Memory selection from ranked candidates uses Thompson sampling (Beta distribution over usage/non-usage counts) from Python stdlib — no external ML library needed
**Depends on**: Phase 7
**Requirements**: FOUND-03
**Success Criteria** (what must be TRUE):
  1. Each candidate memory has Beta(usage_count+1, unused_count+1) sampled to determine final selection order
  2. A memory with zero usage_count still has a non-zero chance of selection (cold-start handled by Beta(1,1) prior)
  3. Over 100 injections, memories with higher usage_count are selected more frequently (probabilistic, not deterministic)
**Plans**: TBD

### Phase 10: Provenance Signals
**Goal**: Injected memories include human-readable provenance metadata — "established across N sessions over M weeks" — computed from session and timestamp data at injection time
**Depends on**: Phase 7
**Requirements**: FOUND-04
**Success Criteria** (what must be TRUE):
  1. Every injected memory block includes a provenance line when session_count > 1
  2. The N (sessions) and M (weeks) values are computed from actual RetrievalLog or ConsolidationLog data
  3. Single-session memories show appropriate provenance ("first observed" or similar) rather than fabricated counts
**Plans**: TBD

### Phase 11: OrientingDetector
**Goal**: A rule-based OrientingDetector fires on high-signal moments — user corrections, explicit emphasis, error spikes, and pacing breaks — before observations reach the ephemeral buffer
**Depends on**: Phases 1-6
**Requirements**: OBSV-01
**Success Criteria** (what must be TRUE):
  1. Text matching "no, that's wrong", "remember this", "actually" triggers an orienting flag on the observation
  2. Three or more errors in a short window triggers an error-spike orienting flag
  3. Orienting-flagged observations have a higher base importance score than non-flagged observations
  4. The detector runs without any LLM call (pure rule-based, fast path)
**Plans**: TBD

### Phase 12: Habituation Baseline
**Goal**: A per-project event frequency model suppresses routine events — events the project sees frequently are de-weighted before they reach the consolidation LLM
**Depends on**: Phase 11
**Requirements**: OBSV-02
**Success Criteria** (what must be TRUE):
  1. Routine events (e.g. test run, file save) seen 10+ times in a project have habituation_factor < 0.5
  2. Novel events (first occurrence in project) have habituation_factor = 1.0
  3. The frequency model is persisted per-project and survives session restarts
  4. Suppressed observations are filtered out before the consolidation LLM call (reducing token waste)
**Plans**: TBD

### Phase 13: Somatic Markers
**Goal**: Each observation is tagged with emotional valence (neutral / friction / surprise / delight) at write time, and non-neutral valence bumps the importance score
**Depends on**: Phase 11
**Requirements**: OBSV-03
**Success Criteria** (what must be TRUE):
  1. Every new observation record has a `valence` field populated with one of the four categories
  2. Observations tagged `friction` or `surprise` have a higher importance score than equivalent `neutral` observations
  3. Valence classification runs without a synchronous LLM call (rule-based or lightweight keyword model)
**Plans**: TBD

### Phase 14: Replay Priority
**Goal**: Observations are sorted by salience (correction > pushback > novelty > recency) before being presented to the consolidation LLM — highest-signal content leads
**Depends on**: Phases 11-13
**Requirements**: OBSV-04
**Success Criteria** (what must be TRUE):
  1. Consolidation receives observations in salience-descending order, not insertion order
  2. A correction observation always ranks above a recency-only observation in the same batch
  3. The salience score is computable from fields already on the observation record (no new API calls)
**Plans**: TBD

### Phase 15: SM-2 Spaced Injection
**Goal**: Memories the user has engaged with are suppressed from injection until their next_injection_due date — implementing SM-2 spaced repetition to prevent over-injection
**Depends on**: Phases 7-10
**Requirements**: LIFE-01
**Success Criteria** (what must be TRUE):
  1. Memory model has three new fields: `next_injection_due`, `injection_ease_factor`, `injection_interval_days`
  2. A memory with `next_injection_due` in the future is excluded from injection candidates, regardless of relevance score
  3. When `was_used=1` is recorded, the memory's interval is extended using the SM-2 formula
  4. Memories with no prior injection history are not suppressed (no due date = always eligible)
**Plans**: TBD

### Phase 16: Reconsolidation at PreCompact
**Goal**: At PreCompact, injected memories that appear in the session are compared against session content — confirmations, contradictions, and refinements update the memory before the session closes
**Depends on**: Phase 15
**Requirements**: LIFE-02
**Success Criteria** (what must be TRUE):
  1. PreCompact identifies which injected memories were referenced in the session
  2. Contradicted memories are flagged or updated with the contradicting evidence
  3. Confirmed memories have their confidence or session_count incremented
  4. The reconsolidation LLM call occurs at most once per PreCompact (batched, not per-memory)
**Plans**: TBD

### Phase 17: Saturation Decay
**Goal**: Memories that are injected frequently but never engaged with (high injection_count, low usage_count) are penalized in the relevance formula — preventing stale memories from crowding out fresh ones
**Depends on**: Phase 15
**Requirements**: LIFE-03
**Success Criteria** (what must be TRUE):
  1. Relevance formula includes a saturation_penalty term: `min(0.3, unused_injections * 0.05)`
  2. A memory injected 10 times with 0 usage has a lower effective relevance than a freshly-added memory with equal semantic score
  3. A memory that gets used (was_used=1) sees its saturation_penalty reset or reduced
**Plans**: TBD

### Phase 18: Integration Factor
**Goal**: Isolated memories — no thread membership, no tag co-occurrence, no reinforcement after 30 days — experience accelerated decay relative to well-connected memories
**Depends on**: Phase 17
**Requirements**: LIFE-04
**Success Criteria** (what must be TRUE):
  1. Relevance formula includes an integration_factor that is < 1.0 for memories with no thread and no tag overlap
  2. After 30 days without reinforcement, an isolated memory's effective relevance drops measurably versus a connected memory with the same raw score
  3. Joining a thread or gaining a tag overlap restores the integration_factor toward 1.0
**Plans**: TBD

### Phase 19: 1-Hop Graph Expansion
**Goal**: After hybrid search returns seed memories, the retrieval path expands one hop to thread neighbors and topical edges via a pre-computed memory_edges table
**Depends on**: Phases 7-10
**Requirements**: RETR-01
**Success Criteria** (what must be TRUE):
  1. A new `memory_edges` table exists with source_id, target_id, and edge_type columns
  2. A nightly job (or on-demand trigger) pre-computes edges from thread membership and tag co-occurrence
  3. After hybrid search, any seed memory with edges in memory_edges has its neighbors added to the candidate pool
  4. The expanded candidate pool is then re-ranked before final injection selection
**Plans**: TBD

### Phase 20: Ghost Coherence Check
**Goal**: A periodic LLM call compares self-model claims (instinctive-tier memories) against actual evidence in the memory store — flagging divergences as contradictions for human review
**Depends on**: Phase 19
**Requirements**: RETR-02
**Success Criteria** (what must be TRUE):
  1. A coherence check can be triggered manually (CLI command or hook) and runs without blocking normal injection
  2. Claims in instinctive-tier memories are compared against consolidated-tier evidence
  3. Divergences are written to a contradictions log or flagged on the affected memory record
  4. The check runs at most once per day per project (rate-limited to control Bedrock costs)
**Plans**: TBD

---

## Future Phases (v2)

### Phase 21: Prospective Memory Table
**Goal**: Users can register "when X happens, remind me Y" triggers stored in a prospective_memories table, surfaced when trigger conditions match
**Depends on**: Phase 20
**Requirements**: PROS-01
**Success Criteria**: TBD (v2)
**Plans**: TBD

### Phase 22: Intent Detection
**Goal**: Observations containing "next time," "remind me," or "when we're working on" language are automatically flagged as prospective intent and routed to the prospective memory system
**Depends on**: Phase 21
**Requirements**: PROS-02
**Success Criteria**: TBD (v2)
**Plans**: TBD

### Phase 23: ContextPercept Sensory Fusion
**Goal**: User text, error output, test results, and git state are fused into a joint observation signal using a temporal binding window
**Depends on**: Phase 20
**Requirements**: SENS-01
**Success Criteria**: TBD (v2)
**Plans**: TBD

### Phase 24: ActivePrimeSet
**Goal**: Injected memories lower the observation gate threshold for topically-related events, with the priming effect decaying per turn
**Depends on**: Phase 23
**Requirements**: SENS-02
**Success Criteria**: TBD (v2)
**Plans**: TBD

### Phase 25: Context-Conditioned Importance
**Goal**: Memory importance updates are conditioned on whether the injection was in the correct project context — wrong-project injections no longer penalize a memory's score
**Depends on**: Phase 20
**Requirements**: LEARN-01
**Success Criteria**: TBD (v2)
**Plans**: TBD

### Phase 26: Constitutive Memory Tagging
**Goal**: Memories tagged as identity or preference ("constitutive") are excluded from crystallization freezing — they stay alive and updatable
**Depends on**: Phase 20
**Requirements**: LEARN-02
**Success Criteria**: TBD (v2)
**Plans**: TBD

---

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → ... → 20, then v2 phases 21-26

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 0.5. AI Eval Harness (INSERTED) | 0/3 | Complete    | 2026-03-29 |
| 1. Commit ORM Migration | - | Complete | pre-roadmap |
| 2. Write Research Files | - | Complete | pre-roadmap |
| 3. Remove file_path Field | - | Complete | 2026-03-29 |
| 4. Stop Creating Stage Directories | - | Complete | 2026-03-29 |
| 5. NarrativeThread Timestamps | - | Complete | 2026-03-29 |
| 6. Remove Unused Imports | - | Complete | 2026-03-29 |
| 7. Hybrid RRF Retrieval | 2/2 | Complete   | 2026-03-29 |
| 8. Prompt-Aware Tier 2 Injection | 1/1 | Complete   | 2026-03-29 |
| 9. Thompson Sampling Selection | 0/? | Not started | - |
| 10. Provenance Signals | 0/? | Not started | - |
| 11. OrientingDetector | 0/? | Not started | - |
| 12. Habituation Baseline | 0/? | Not started | - |
| 13. Somatic Markers | 0/? | Not started | - |
| 14. Replay Priority | 0/? | Not started | - |
| 15. SM-2 Spaced Injection | 0/? | Not started | - |
| 16. Reconsolidation at PreCompact | 0/? | Not started | - |
| 17. Saturation Decay | 0/? | Not started | - |
| 18. Integration Factor | 0/? | Not started | - |
| 19. 1-Hop Graph Expansion | 0/? | Not started | - |
| 20. Ghost Coherence Check | 0/? | Not started | - |
| 21. Prospective Memory Table (v2) | 0/? | Not started | - |
| 22. Intent Detection (v2) | 0/? | Not started | - |
| 23. ContextPercept Sensory Fusion (v2) | 0/? | Not started | - |
| 24. ActivePrimeSet (v2) | 0/? | Not started | - |
| 25. Context-Conditioned Importance (v2) | 0/? | Not started | - |
| 26. Constitutive Memory Tagging (v2) | 0/? | Not started | - |
