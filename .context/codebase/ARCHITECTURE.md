# Architecture

## Pattern

Event-driven plugin architecture layered over a dual-write persistence core. The system attaches to Claude Code as a Claude Code hooks plugin — three hooks fire at well-defined lifecycle points (SessionStart, UserPromptSubmit, PreCompact). Each hook is a standalone Python script that instantiates a fresh object graph and exits. There is no long-running process or daemon; all state lives in SQLite + markdown files on disk.

The internal structure is a loose hexagonal arrangement: a storage core (`core/storage.py`) sits at the center, surrounded by domain services (`consolidator`, `crystallizer`, `relevance`, `feedback`, `lifecycle`, `threads`) that compose it. LLM calls are isolated in the service layer; no storage code touches the Anthropic SDK.

## Layers & Boundaries

| Layer | Purpose | Key Files |
| --- | --- | --- |
| Hook entry points | Thin orchestration — read env, build object graph, call services, write stdout | `hooks/pre_compact.py`, `hooks/session_start.py`, `hooks/user_prompt_inject.py`, `hooks/consolidate_cron.py` |
| Domain services | Business logic: curation, promotion, scoring, synthesis | `core/consolidator.py`, `core/crystallizer.py`, `core/relevance.py`, `core/feedback.py`, `core/lifecycle.py`, `core/threads.py`, `core/self_reflection.py` |
| Retrieval / injection | Session-context memory selection and formatting for Claude's context window | `core/retrieval.py`, `hooks/user_prompt_inject.py` |
| Storage core | Dual-write CRUD: markdown files + SQLite FTS5 index, atomic writes, WAL | `core/storage.py` |
| Ingestion bridge | One-way sync from native Claude Code memory format into memesis lifecycle | `core/ingest.py` |
| Manifest | Regenerates human-readable MEMORY.md from SQLite metadata | `core/manifest.py` |
| Prompts | All LLM prompt templates in one place | `core/prompts.py` |
| Eval harness | Offline quality metrics (needle test, continuity, curation audit) | `eval/` |
| Scripts | Developer utilities (scan, seed, diagnose, heartbeat, consolidate, reduce) | `scripts/` |

## Data Flow

### Session start (injection path)

1. `hooks/session_start.py` fires; reads `CLAUDE_SESSION_ID` and `cwd` from env.
2. `NativeMemoryIngestor.ingest()` scans `~/.claude/projects/<hash>/memory/` for native `.md` files and deduplicates them into the store.
3. `RelevanceEngine.rehydrate_for_context()` unarchives previously archived memories whose relevance score has recovered above `REHYDRATE_THRESHOLD = 0.30`.
4. `RetrievalEngine.inject_for_session()` builds a three-tier context block: Tier 1 (all instinctive memories) + Tier 2 (token-budgeted crystallized, sorted by project match → importance → recency) + Tier 2.5 (narrative threads linking injected memories). All injections are logged in `retrieval_log`.
5. An ephemeral buffer file is created at `{base_dir}/ephemeral/session-{YYYY-MM-DD}.md`.
6. The formatted context string is written to stdout — Claude Code injects it into the session.

### Per-prompt injection (just-in-time path)

1. `hooks/user_prompt_inject.py` reads the full user prompt from stdin.
2. `extract_query_terms()` strips markdown formatting and produces up to 10 alpha words (4+ chars, not in stop list).
3. `store.search_fts()` fires a BM25 OR query; already-injected and archived memories are filtered out.
4. Up to 3 results are selected within a 2000-character budget. Injected IDs are logged; formatted `[Memory: title] summary` lines go to stdout.

### Observation capture

`hooks/append_observation.py` (called from the `/memesis:learn` skill) appends a timestamped observation line to the day's ephemeral buffer, holding a POSIX advisory lock (`fcntl.flock`) during the write to coordinate with the cron job.

### Consolidation / sleep consolidation (PreCompact + cron)

1. Either `hooks/pre_compact.py` (on context compaction) or `hooks/consolidate_cron.py` (hourly cron) fires.
2. The lock-snapshot-clear pattern: an exclusive lock is held while reading + resetting the ephemeral buffer, then released before the slow LLM call.
3. `FeedbackLoop.track_usage()` scores injected memories against the conversation text (weighted keyword heuristic) and calls `store.record_usage()` for those that score above threshold.
4. `Consolidator.consolidate_session()` privacy-filters content (strips emotional state patterns), builds a manifest summary, sends a structured prompt to Claude, then executes keep/prune/promote decisions. Contradictions are detected via the `contradicts` field and resolved with a second LLM call (`CONTRADICTION_RESOLUTION_PROMPT`). Newly kept memories trigger an archived-memory rehydration check via `RelevanceEngine.find_rehydration_by_observation()`.
5. `Crystallizer.crystallize_candidates()` finds consolidated memories with `reinforcement_count >= 3` and adequate temporal spacing, groups them by observation type + tag overlap, synthesizes each group into a denser insight via LLM, creates the crystallized memory, and archives source memories with `subsumed_by` set (inhibiting them from rehydration).
6. `build_threads()` clusters non-threaded memories by tag overlap + temporal spread, narrates each valid cluster into an arc via LLM, and persists it via `store.create_thread()`.
7. `RelevanceEngine.run_maintenance()` archives stale memories (relevance < 0.15) and rehydrates relevant archived ones (relevance >= 0.30).
8. Every `REFLECTION_INTERVAL = 5` consolidations, `SelfReflector.reflect()` reviews the consolidation log and updates the `self-model.md` instinctive memory.
9. `ManifestGenerator.write_manifest()` regenerates `MEMORY.md` atomically.

### Relevance scoring formula

```
relevance = importance^0.4 × recency^0.3 × usage_signal^0.2 × context_boost^0.1
```

where `recency = 0.5^(days_since_last_activity / 60)` and `context_boost = 1.5` when project matches, `1.0` otherwise.

## Entry Points

- `SessionStart` hook: `hooks/session_start.py`, triggered by Claude Code on session open (5 s timeout)
- `PreCompact` hook: `hooks/pre_compact.py`, triggered by Claude Code before context compaction (30 s timeout)
- `UserPromptSubmit` hook: `hooks/user_prompt_inject.py`, triggered on every user message (3 s timeout)
- Hourly cron: `hooks/consolidate_cron.py`, invoked directly by cron (`7 * * * *`), uses Bedrock via `CLAUDE_CODE_USE_BEDROCK`
- Observation append: `hooks/append_observation.py`, called from the `/memesis:learn` Claude skill
- Eval harness: `eval/needle_test.py`, `eval/continuity_test.py`, `eval/staleness_test.py` — run as pytest or standalone
- Developer scripts: `scripts/scan.py`, `scripts/seed.py`, `scripts/diagnose.py`, `scripts/heartbeat.py`, `scripts/consolidate.py`, `scripts/reduce.py`

## Key Abstractions

- **`MemoryStore`** (`core/storage.py`): Dual-write CRUD over markdown files + SQLite. All persistence goes through here. Manages FTS5 index synchronization manually (`_fts_insert` / `_fts_delete`). Atomic file writes via `tempfile.mkstemp` + `shutil.move` in same directory. WAL checkpoint on `close()`. Project-scoped storage at `~/.claude/projects/<hash>/memory/` or global at `~/.claude/memory/`.

- **Memory stages** (`core/storage.py`, `core/lifecycle.py`): Four-stage progression: `ephemeral` → `consolidated` → `crystallized` → `instinctive`. Promotion rules are enforced by `LifecycleManager`: consolidated→crystallized requires `reinforcement_count >= 3` with temporal spacing across at least 2 distinct calendar days; crystallized→instinctive requires `importance > 0.85` AND usage in 10+ unique sessions.

- **`Consolidator`** (`core/consolidator.py`): LLM-driven curation engine. Reads ephemeral content, privacy-filters it, calls Claude with a manifest context, executes structured decisions (keep/prune/promote), and resolves contradictions. Single retry on malformed JSON. Calls `anthropic.Anthropic()` or `anthropic.AnthropicBedrock()` depending on `CLAUDE_CODE_USE_BEDROCK` env var.

- **`Crystallizer`** (`core/crystallizer.py`): Episodic-to-semantic transformation. Groups promotion candidates by observation type + tag overlap (greedy union-find), synthesizes each group into one denser insight via LLM, archives source memories marked `subsumed_by`. Falls back to simple lifecycle promotion if LLM fails.

- **`RelevanceEngine`** (`core/relevance.py`): Continuous relevance scoring with exponential decay. Manages archival (below 0.15) and rehydration (above 0.30). Also handles FTS-based observation-triggered rehydration.

- **`RetrievalEngine`** (`core/retrieval.py`): Three-tier session injection (instinctive always, crystallized token-budgeted at 8% of 200K context, active search on demand). Appends narrative thread context for injected memories. Token budget is characters / 4.

- **`FeedbackLoop`** (`core/feedback.py`): Usage detection (weighted keyword scoring: title 3×, summary 2×, content 1×, threshold 4.0). Importance score adjustment (+0.05 on use, −0.10 after 3 consecutive unused injections). Cross-project promotion signal (D-08: injected in 3+ distinct project contexts). Event logging to `meta/retrieval-log.jsonl`.

- **`SelfReflector`** (`core/self_reflection.py`): Periodic (every 5 consolidations) self-model update. Seeds three instinctive memories on first run: `self-model.md`, `observation-habit.md`, `compaction-guidance.md`. Reviews consolidation log and patches the self-model via LLM.

- **`ThreadDetector` / `ThreadNarrator`** (`core/threads.py`): Detects memory clusters via tag-overlap union-find, narrates valid arcs as "correction chain / preference evolution / knowledge building" stories via LLM. Threads are stored in `narrative_threads` + `thread_members` tables and injected in Tier 2.5.

- **`NativeMemoryIngestor`** (`core/ingest.py`): One-way bridge from native Claude Code `.md` files (parsed by `parse_frontmatter`) into the memesis consolidated stage. Deduplicates by content hash. Maps native types (user, feedback, project, reference) to memesis observation types.
