# Architecture

_Last updated: 2026-05-06. Prior version dated 2026-04-10._

## Pattern

Event-driven plugin architecture layered over a dual-connection persistence core. The system attaches to Claude Code as a hooks plugin — three hooks fire at well-defined lifecycle points (SessionStart, UserPromptSubmit, PreCompact). Each hook is a standalone Python script that instantiates a fresh object graph, processes, and exits. There is no long-running process or daemon; all state lives in a single SQLite database file plus markdown files on disk.

The internal structure follows a layered service pattern: a Peewee ORM model layer (`core/models.py`) and database init (`core/database.py`) sit at the center, with a dual-connection design — Peewee for relational CRUD/FTS5 and apsw for sqlite-vec KNN. Domain services compose on top. LLM calls are isolated behind `core/llm.py`; no storage code touches the Anthropic SDK. Feature flags (`core/flags.py`) gate optional behaviors system-wide.

The system models a biological memory lifecycle: observations are captured ephemerally, curated into consolidated memories, crystallized into semantic knowledge through LLM synthesis, and optionally promoted to always-injected instinctive status. Reconsolidation compares injected memories against new session evidence; a graph layer tracks causal relationships and contradiction tensions between memories.

**T1/T2/T3 waves (landed post 2026-04-10)** added a Stage 1.5 extraction pipeline that reorganizes flat observations into structured issue cards, a closed-loop self-reflection framework that feeds extraction-process metacognition back into the next run's parameters, and a rule registry that converts confirmed heuristic-rule fires into concrete parameter overrides.

## Layers & Boundaries

| Layer | Purpose | Key Files |
| --- | --- | --- |
| Hook entry points | Thin orchestration — read env, init DB, call services, write stdout | `hooks/pre_compact.py`, `hooks/session_start.py`, `hooks/user_prompt_inject.py`, `hooks/consolidate_cron.py`, `hooks/append_observation.py` |
| Domain services | Business logic: curation, promotion, scoring, synthesis, reconsolidation | `core/consolidator.py`, `core/crystallizer.py`, `core/reconsolidation.py`, `core/relevance.py`, `core/feedback.py`, `core/lifecycle.py`, `core/threads.py`, `core/self_reflection.py`, `core/graph.py` |
| Retrieval / injection | Session-context memory selection, hybrid RRF ranking, token-budgeted formatting | `core/retrieval.py`, `hooks/user_prompt_inject.py` |
| Cognitive subsystems | Affect tracking, somatic markers, habituation, orienting, replay priority, coherence | `core/affect.py`, `core/somatic.py`, `core/habituation.py`, `core/orienting.py`, `core/replay.py`, `core/coherence.py` |
| **Stage 1.5 extraction** | Issue-card synthesis over hierarchically-extracted observations; affect aggregation over transcript windows | `core/issue_cards.py`, `core/card_validators.py`, `core/extraction_affect.py`, `core/transcript_ingest.py` |
| **Self-reflection (extraction)** | Per-session metacognition: rules over run stats → SelfObservation → self_model.md → next run context | `core/self_reflection_extraction.py` |
| **Rule registry** | Confirmed-rule → ParameterOverrides: translates fired heuristics into concrete extraction knobs | `core/rule_registry.py` |
| Scheduling | SM-2 spaced repetition for injection eligibility | `core/spaced.py` |
| ORM + persistence | Peewee models, deferred DB init, FTS5 sync, schema migrations | `core/models.py`, `core/database.py` |
| Vector store | sqlite-vec via apsw — KNN search, embedding storage | `core/vec.py`, `core/embeddings.py` |
| Graph layer | Pre-computed and incremental edges, 1-hop expansion for retrieval | `core/graph.py` |
| Feature flags | JSON-based A/B toggle for all optional behaviors | `core/flags.py` |
| LLM transport | Client selection (Anthropic direct vs Bedrock), markdown fence stripping | `core/llm.py` |
| Ingestion bridge | One-way sync from native Claude Code memory format into memesis lifecycle | `core/ingest.py` |
| Manifest | Regenerates human-readable MEMORY.md from SQLite metadata | `core/manifest.py` |
| Prompts | All LLM prompt templates in one place | `core/prompts.py` |
| Eval harness | Offline quality metrics (needle test, continuity, curation audit, judge evals) | `eval/` |
| Scripts | Developer utilities (scan, seed, diagnose, heartbeat, consolidate, reduce, dashboard, cost, compare) | `scripts/` |

## Data Flow

### Session start (injection path)

1. `hooks/session_start.py` fires; reads `CLAUDE_SESSION_ID` and `cwd` from env.
2. `init_db(project_context)` resolves path `~/.claude/projects/<hash>/memory/index.db`, binds the deferred `SqliteDatabase(None)`, creates tables, runs migrations, initializes `VecStore`.
3. `SelfReflector.ensure_instinctive_layer()` seeds self-model, observation habit, and compaction guidance if not present.
4. `NativeMemoryIngestor.ingest()` scans native `.md` files and deduplicates into the store.
5. `RelevanceEngine.rehydrate_for_context()` unarchives memories whose relevance has recovered.
6. `RetrievalEngine.inject_for_session()` builds a multi-tier context block:
   - **Tier 1**: All instinctive memories (always injected, no filtering).
   - **Tier 2**: Token-budgeted crystallized + consolidated memories via hybrid RRF (FTS5 BM25 + sqlite-vec KNN), with SM-2 eligibility filtering, Thompson sampling re-rank, project-context boost, and 1-hop graph expansion.
   - **Tier 2.5**: Narrative thread arcs for injected memories, with affect-aware ordering (frustration > 0.3 prioritizes `frustration_to_mastery` threads).
   - **Tier 2.6**: Active contradiction tensions — unresolved `contradicts` edges, greedy budget packed.
7. All injections logged in `retrieval_log`. Ephemeral buffer created. Context string written to stdout.

### Per-prompt injection (just-in-time path)

1. `hooks/user_prompt_inject.py` reads the user prompt from stdin.
2. `extract_query_terms()` strips markdown, produces up to 10 significant words.
3. Embedding computed once via Bedrock Titan v2 (graceful fallback to FTS-only if unavailable).
4. Two retrieval paths run: Tier 2 crystallized-only (project-context boost, token budget) and Tier 3 JIT all-stage hybrid search. Results merged, deduplicated against already-injected IDs.
5. Up to 3 results within 2000-character budget. Formatted `[Memory: title] summary` lines go to stdout.
6. Affect analyzer updates interaction state; coherence probe fires if degradation detected.

### Observation capture

`hooks/append_observation.py` appends a timestamped observation to the day's ephemeral buffer, holding a POSIX advisory lock (`fcntl.flock`) during the write.

### Consolidation / sleep consolidation (PreCompact + cron)

1. Either `hooks/pre_compact.py` (on context compaction) or `hooks/consolidate_cron.py` (hourly cron) fires.
2. Lock-snapshot-clear: exclusive lock held while reading + resetting ephemeral buffer, released before LLM calls.
3. Session affect state loaded from `core/affect.py`.
4. `FeedbackLoop.track_usage()` scores injected memories against conversation text (weighted keyword heuristic).
5. **Reconsolidation** (`core/reconsolidation.py`): One batched LLM call compares all injected memories against session content. Actions: confirmed (bump `reinforcement_count`), contradicted (add `contradiction_flagged` tag), refined (append refinement to content). When `causal_edges` flag is on, creates directed `caused_by`/`refined_from` edges using sqlite-vec cosine similarity to select targets. When `contradiction_tensors` flag is on, creates bidirectional `contradicts` edges with resolved/unresolved status.
6. `Consolidator.consolidate_session()` runs habituation filter, replay priority sort, builds manifest summary, sends structured prompt to Claude, executes keep/prune/promote decisions. For card-shaped decisions (those carrying `scope` or `evidence_quotes`), `extract_card_memory_fields()` maps card fields to Memory columns: `temporal_scope`, `confidence`, `affect_valence`, `actor`, `criterion_weights` (JSON), `rejected_options` (JSON). The Kensinger +0.05 friction bump is applied here (sole site) when `affect_valence == "friction"`. Contradictions resolved with second LLM call. Newly kept memories trigger rehydration check.
7. `Crystallizer.crystallize_candidates()` finds promotion-eligible memories (reinforcement >= 3, temporally spaced), groups by embedding cosine similarity (fallback: tag overlap), synthesizes each group into denser insight via LLM, archives sources with `subsumed_by`. Creates `subsumed_into` graph edges.
8. `build_threads()` clusters non-threaded memories, narrates arcs via LLM, computes affect signatures.
9. Auto-promotion check: crystallized memories meeting instinctive criteria are promoted.
10. `RelevanceEngine.run_maintenance()` archives stale memories, rehydrates relevant ones.
11. Every `REFLECTION_INTERVAL = 5` consolidations, `SelfReflector.reflect()` reviews the consolidation log and updates the self-model.
12. `ManifestGenerator.write_manifest()` regenerates MEMORY.md.
13. Newly kept and crystallized memories are embedded via Bedrock Titan v2 and stored in `vec_memories`.

### Transcript extraction pipeline (Stage 1 → 1.5)

`core/transcript_ingest.py` implements the full extraction pipeline; the relevant portion is:

1. **ParameterOverrides resolution**: `resolve_overrides_from_root()` (`core/rule_registry.py`) reads the self-reflection audit JSONL and applies any confirmed-rule overrides before the run begins. Overrides include `max_windows`, `affect_pre_filter`, `synthesis_strict`, `max_tokens_stage1`, `importance_gate`, and `recurrent_failure_patterns`.
2. **Window chunking**: `select_chunking()` (`core/self_reflection_extraction.py`) chooses `stride` or `user_anchored` strategy, consulting the confirmed `chunking_suboptimal` rule from prior runs.
3. **Affect pre-pass per window** (`core/extraction_affect.py`): `aggregate_window_affect()` scans `[USER...]` lines in each rendered window, applies the somatic lexicon, and produces a `WindowAffect` with `valence`, `max_boost`, `has_repetition`, `has_pushback`. `apply_affect_prior()` bumps observation importance (capped +0.20). `format_affect_hint()` injects a compact hint block into the Stage 1 LLM prompt.
4. **Stage 1 extraction**: LLM call per window; outputs flat observations with `kind`, `knowledge_type`, `importance`, `facts`.
5. **Dedup + drop-gate**: MD5 content-hash dedup; observations with `importance < 0.3` sharing no entity are dropped.
6. **Stage 1.5 — issue-card synthesis** (`core/issue_cards.py`): `synthesize_issue_cards()` reorganizes flat observations into structured issue cards (problem framing, options considered, decision/outcome, user affect, evidence quotes). Cards with `kind == "decision"` carry `criterion_weights` and `rejected_options`. `synthesis_strict` flag (set by `synthesis_overgreedy` rule) tightens orphan pressure.
7. **Card validation** (`core/card_validators.py`): `_card_evidence_indices_valid()` demotes cards with hallucinated indices to orphans. `_card_evidence_load_bearing()` demotes single-quote circular-evidence cards to orphans.
8. **Affect merge**: `_merge_card_affect()` overlays LLM-derived card affect back into the session affect summary, patching gaps the somatic pre-pass missed.
9. **Self-reflection** (`core/self_reflection_extraction.py`): `reflect_on_extraction()` runs the heuristic ruleset over `ExtractionRunStats`, fires `SelfObservation`s, appends to `self_observations.jsonl`, and refreshes `self_model.md`.
10. Returns `{"observations": orphans, "issue_cards": cards, ...}`.

### Relevance scoring formula

```
relevance = importance^0.4 x recency^0.3 x usage_signal^0.2 x context_boost^0.1
```

where `recency = 0.5^(days_since_last_activity / 60)` and `context_boost = 1.5` when project matches, `1.0` otherwise.

## Entry Points

- **SessionStart** hook: `hooks/session_start.py`, triggered by Claude Code on session open (5 s timeout)
- **PreCompact** hook: `hooks/pre_compact.py`, triggered by Claude Code before context compaction (30 s timeout)
- **UserPromptSubmit** hook: `hooks/user_prompt_inject.py`, triggered on every user message (3 s timeout)
- **Hourly cron**: `hooks/consolidate_cron.py`, invoked directly by cron, uses Bedrock via `CLAUDE_CODE_USE_BEDROCK`
- **Observation append**: `hooks/append_observation.py`, called from the `/memesis:learn` Claude skill
- **Eval harness**: `eval/needle_test.py`, `eval/continuity_test.py`, `eval/staleness_test.py`, `eval/judge_eval.py` — run as pytest or standalone
- **Developer scripts**: `scripts/scan.py`, `scripts/seed.py`, `scripts/diagnose.py`, `scripts/heartbeat.py`, `scripts/consolidate.py`, `scripts/reduce.py`, `scripts/dashboard.py`, `scripts/cost.py`, `scripts/compare.py`, `scripts/embed_backfill.py`, `scripts/audit_pipeline_dimensions.py`, `scripts/run_selected_sessions.py`, `scripts/eval_protocol.py`
- **Hook registration**: `hooks/hooks.json` defines all three hooks with venv-isolated Python and NLTK_DATA path

## Key Abstractions

- **`Memory` model** (`core/models.py`): Peewee ORM model for the `memories` table. UUID4 primary key. Overrides `save()` and `delete_instance()` to keep FTS5 index in sync atomically via `db.atomic()`. Provides `search_fts()` (BM25 ranked), `tokenize_fts_query()` (stop-word filtering, OR-joined), `sanitize_fts_term()` (double-quote escaping). Scopes: `active()` (non-archived), `by_stage()`. Schema additions from T1/T2/T3: `temporal_scope`, `extraction_confidence`, `actor`, `polarity`, `revisable`, `confidence`, `affect_valence`, `criterion_weights` (JSON TEXT), `rejected_options` (JSON TEXT).

- **`MemoryEdge` model** (`core/models.py`): Graph edges between memories. Two categories: **recomputable** (`thread_neighbor`, `tag_cooccurrence` — rebuilt by `compute_edges()`) and **incremental** (`caused_by`, `refined_from`, `subsumed_into`, `contradicts`, `echo` — created by pipeline steps, preserved across rebuilds).

- **Memory stages** (`core/lifecycle.py`): Four-stage progression: `ephemeral` → `consolidated` → `crystallized` → `instinctive`. Promotion rules enforced by `LifecycleManager`: consolidated→crystallized requires `reinforcement_count >= 3` with temporal spacing across at least 2 distinct calendar days; crystallized→instinctive requires `importance > 0.85` AND usage in 10+ unique sessions. Demotion can skip stages.

- **`Consolidator`** (`core/consolidator.py`): LLM-driven curation engine. Reads ephemeral content, applies habituation filter and replay priority sort, calls Claude with manifest context, executes keep/prune/promote decisions, resolves contradictions with second LLM call. Triggers archived-memory rehydration on newly kept observations. Somatic markers classify valence and boost importance. Card-shaped decisions (carrying `scope`/`evidence_quotes`) go through `extract_card_memory_fields()` — the Kensinger +0.05 friction bump, `criterion_weights`, and `rejected_options` are written at this layer.

- **`Crystallizer`** (`core/crystallizer.py`): Episodic-to-semantic transformation. Groups promotion candidates by embedding cosine similarity (union-find, threshold 0.75; fallback: tag overlap). Synthesizes each group into one denser insight via LLM. Archives source memories marked `subsumed_by`. Creates `subsumed_into` graph edges. Falls back to simple lifecycle promotion if LLM fails.

- **`reconsolidate()`** (`core/reconsolidation.py`): Session-evidence comparison. One batched LLM call for all injected memories. Creates directed causal edges (using sqlite-vec similarity ranking for target selection) and bidirectional contradiction edges with resolved/superseded status tracking. Affect metadata embedded in edges when `affect_signatures` flag is on.

- **`RetrievalEngine`** (`core/retrieval.py`): Multi-tier injection with hybrid RRF (FTS5 + KNN, rrf_k=60). Static path (no query) for SessionStart; hybrid path with 1-hop graph expansion, project-context boost, crystallized-stage boost, and Thompson sampling for UserPromptSubmit. Token budget at 8% of 200K context. Tier 2.5 narrative threads with affect-aware reordering. Tier 2.6 active tensions from unresolved contradiction edges.

- **`VecStore`** (`core/vec.py`): sqlite-vec virtual table via apsw. Connection-per-operation pattern. 512-dimensional float32 embeddings from Bedrock Titan v2. KNN search, embedding storage/retrieval. `available` property for graceful degradation.

- **`expand_neighbors()`** (`core/graph.py`): 1-hop graph expansion with priority-tiered edge types (causal > structural). Uses centroid cosine similarity for within-tier re-ranking.

- **Feature flags** (`core/flags.py`): JSON file at `{base_dir}/flags.json`, merged with defaults. All flags default to True. Cached after first load. Flags: `prompt_aware_tier2`, `thompson_sampling`, `provenance_signals`, `reconsolidation`, `causal_edges`, `contradiction_tensors`, `graph_expansion`, `affect_signatures`, `affect_awareness`, `ghost_coherence`, `sm2_spaced_injection`, `habituation_baseline`, `somatic_markers`, `replay_priority`, `orienting_detector`, `saturation_decay`, `integration_factor`, `adversarial_surfacing`.

- **`call_llm()`** (`core/llm.py`): Centralized LLM transport. Selects `Anthropic()` or `AnthropicBedrock()` based on `CLAUDE_CODE_USE_BEDROCK` env var. Default model: `claude-sonnet-4-6` / `us.anthropic.claude-sonnet-4-6`. Strips markdown fences. Does not parse JSON or retry — callers own those responsibilities.

- **Deferred database** (`core/models.py`, `core/database.py`): `db = SqliteDatabase(None)` at module level, bound at runtime via `init_db()`. WAL mode with `synchronous=normal` and `busy_timeout=5000`. Tables: `memories`, `narrative_threads`, `thread_members`, `memory_edges`, `retrieval_log`, `consolidation_log`. FTS5 virtual table `memories_fts` (external content, manual sync). Vector table `vec_memories` via apsw/sqlite-vec.

- **`synthesize_issue_cards()`** (`core/issue_cards.py`): Stage 1.5 LLM synthesis. Accepts flat observations, session synopsis, and session affect summary. Returns `(issue_cards, orphans, stats)`. Falls back to `([], original_observations, error_stats)` on LLM or parse failure — caller never loses data. Card schema includes `title`, `problem`, `options_considered`, `decision_or_outcome`, `user_reaction`, `user_affect_valence`, `evidence_quotes`, `evidence_obs_indices`, `kind`, `knowledge_type`, `importance`, `scope`, and optional `criterion_weights`/`rejected_options` for decision-kind cards. `synthesis_strict` tightens orphan pressure when `synthesis_overgreedy` rule is confirmed.

- **`extract_card_memory_fields()`** (`core/issue_cards.py`): Maps issue card fields to Memory column values. Returns `temporal_scope`, `confidence` (derived from `knowledge_type_confidence`), `affect_valence`, `actor` (first proper noun in evidence quotes), `criterion_weights`, `rejected_options`.

- **`aggregate_window_affect()`** / **`apply_affect_prior()`** (`core/extraction_affect.py`): Pre-pass over `[USER...]` lines in a transcript window before the Stage 1 LLM call. Uses `core.somatic.classify_valence` plus `_REPETITION_RE`/`_PUSHBACK_RE` patterns. Produces `WindowAffect` with `importance_prior` (capped +0.20, +0.05 bonus for repetition). `apply_affect_prior()` mutates observations in-place, preserving `raw_importance_pre_affect`.

- **`ParameterOverrides`** (`core/rule_registry.py`): Frozen dataclass holding all extraction knobs (`importance_gate`, `max_windows`, `window_chars`, `stride_chars`, `max_tokens_stage1`, `chunking_strategy`, `affect_pre_filter`, `synthesis_strict`, `prefilter_*`, `recurrent_failure_patterns`). Defaults match current hardcoded values so `resolve_overrides({})` is a no-op.

- **`resolve_overrides()`** (`core/rule_registry.py`): Walks the self-reflection audit dict; for each rule whose `confidence == "confirmed"` (fire_count ≥ 3), applies the registered override function. Override functions compose deterministically in `RULE_OVERRIDES` insertion order. Rules: `chunking_suboptimal`, `low_productive_rate`, `affect_blind_spot` (informational), `dedup_inert` (stub), `synthesis_overgreedy`, `parse_errors_present`, `cards_unused_high_importance`, `monotone_knowledge_lens` (stub), `affect_signal_no_extraction` (stub), `forced_clustering_low_importance`, `recurrent_agent_failure`.

- **`reflect_on_extraction()`** (`core/self_reflection_extraction.py`): Runs the registered heuristic rules over `ExtractionRunStats`. Fires `SelfObservation` dicts, appends to `~/.claude/memesis/self-model/self_observations.jsonl`, refreshes `self_model.md`. Thirteen rules registered: `low_productive_rate`, `chunking_suboptimal`, `affect_blind_spot`, `parse_errors_present`, `dedup_inert`, `low_obs_yield_per_call`, `repeated_facts_high`, `confirmed_rule_no_action`, `synthesis_overgreedy`, `monotone_knowledge_lens`, `affect_signal_no_extraction`, `forced_clustering_low_importance`, `cards_unused_high_importance`. Cross-session meta-rule `recurrent_agent_failure` fires via `reflect_on_corpus()` called by sweep runners after all sessions are processed.

- **`aggregate_audit()`** / **`select_chunking()`** (`core/self_reflection_extraction.py`): `aggregate_audit()` reads `self_observations.jsonl` and returns per-rule fire counts, confidence (`tentative` | `confirmed`), and latest record. `select_chunking()` is the mechanized feedback channel — consults the confirmed `chunking_suboptimal` rule and session shape to auto-select `stride` vs. `user_anchored`.

- **Card validators** (`core/card_validators.py`): `_card_evidence_indices_valid()` returns False when all `evidence_obs_indices` are out-of-range (hallucinated). `_card_evidence_load_bearing()` returns False when a single-quote card's lone quote is circular (fails pronoun, imperative, and technical-token checks). Both used by `synthesize_issue_cards()` to demote bad cards to orphans rather than silently accepting them.
