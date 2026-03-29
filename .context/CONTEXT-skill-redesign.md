# Context: Redesign memesis skills — learn, recall, reflect, memory, teach, connect

**Gathered:** 2026-03-29
**Status:** Ready for implementation
**Codebase map:** .context/codebase/ (mapped 2026-03-29)

<domain>
## Scope

Redesign and expand the memesis plugin skill set. Six skills total: redesign `/learn` and `/memory`, add `/recall`, `/reflect`, `/teach`, and `/connect`. All skills target the upcoming Peewee model API (Memory.create, Memory.by_stage, etc.), shipping alongside or after the Peewee migration. The current `stats`, `browse`, `search`, `status` subcommands under `/memory` become separate top-level skills.
</domain>

<decisions>
## Implementation Decisions

### /learn — Direct memory creation with user choice on ambiguity

- **D-01:** Two-tier write path. Simple facts (corrections, preferences, specific knowledge) write directly to consolidated stage — no ephemeral buffer, no consolidation risk. Complex observations that need synthesis go to the buffer with a priority flag.
- **D-02:** When there's uncertainty about whether input is a simple fact or needs synthesis, present the user with the choice: "Store this directly, or queue for consolidation with your other observations?"
- **D-03:** Classification done inline by the session Claude — no extra LLM call. The skill prompt instructs Claude to determine observation type, title, summary, and tags before writing.

### /recall — Search with conversational and detail modes

- **D-04:** Default mode is conversational: Claude synthesizes matching memories into a natural response ("Here's what I know about X...") with citations to specific memory IDs.
- **D-05:** `--detail` flag gives ranked list with title, summary, relevance score, stage badge, and retrieval method (FTS vs semantic) for each match.
- **D-06:** Uses both FTS and semantic (vec) search, merges and deduplicates results.

### /reflect — On-demand self-reflection with preview

- **D-07:** Shows what the reflection found (new patterns, deprecated observations, self-model changes) and lets the user approve before applying.
- **D-08:** User can selectively approve — accept some observations, reject others, before the self-model is updated.

### /memory → separate top-level skills

- **D-09:** `/memory` becomes a suite of focused skills: `/stats`, `/health`, `/threads`, `/usage`. Each has a sharp trigger description.
- **D-10:** `/health` shows relevance health: memories approaching archival, stale memories, rehydration candidates.
- **D-11:** `/threads` shows narrative thread visualization with member memories and evolution arcs.
- **D-12:** `/usage` shows injection counts, usage rates, importance trends, which memories are actually being used.
- **D-13:** `/stats` shows counts by stage, importance distribution, cross-project view (memories shared across projects, project-specific vs global).

### /teach — Structured multi-part knowledge

- **D-14:** Like `/learn` but for structured knowledge that benefits from decomposition. "Teach me about our deployment process" creates a multi-part memory with context, steps, and relationships.
- **D-15:** Claude (session agent) decomposes the knowledge into logical parts, each becoming a linked memory. Tags connect them.

### /connect — User-driven thread creation

- **D-16:** Manually link memories into a thread or mark them as related. User-driven thread creation complementing the automatic thread detection.
- **D-17:** Accepts memory IDs or topic descriptions. If topics, search for matching memories and present choices.

### API Target

- **D-18:** All skills target Peewee models (Memory.create, Memory.by_stage, VecStore.search_vector, etc.). They ship alongside or after the Peewee migration.

### Claude's Discretion

- The exact prompt wording and examples in each SKILL.md
- How /teach decomposes knowledge into parts (Claude decides the structure per input)
- Whether /reflect shows a diff-style or narrative-style preview
</decisions>

<canonical_refs>

## Canonical References

**Downstream agents MUST read these before implementing.**

### Peewee migration spec

- `.context/specs/2026-03-29-peewee-orm-migration.md` — Defines the new model API (Memory, NarrativeThread, VecStore) that all skills must target

### Existing skill definitions

- `skills/learn/SKILL.md` — Current /learn implementation (to be replaced)
- `skills/forget/SKILL.md` — Current /forget (not in scope but related)
- `skills/memory/SKILL.md` — Current /memory (being split into separate skills)
- `skills/ideate/SKILL.md` — Autonomous development loop (not in scope)

### Architecture

- `.context/codebase/ARCHITECTURE.md` — Hook lifecycle, data flow, observation capture
- `.context/codebase/CONVENTIONS.md` — Naming, error handling, logging patterns

### Observation types

- `core/prompts.py` — OBSERVATION_TYPES dict defining valid types and their formatting
- `hooks/append_observation.py` — Current observation append implementation with file locking
</canonical_refs>

<code_context>

## Codebase Insights

### Reusable Assets

- `hooks/append_observation.py` — file-locked buffer append (for /learn ephemeral path)
- `core/retrieval.py:RetrievalEngine.active_search()` — existing search for /recall
- `core/relevance.py:RelevanceEngine` — scoring, archival candidates, rehydration candidates for /health
- `core/self_reflection.py:SelfReflector` — existing reflection logic for /reflect
- `core/threads.py:ThreadDetector` — thread detection for /threads
- `core/feedback.py:FeedbackLoop` — usage tracking data for /usage
- `core/prompts.py:OBSERVATION_TYPES` — type classification reference for /learn and /teach

### Established Patterns

- Skills are `skills/<name>/SKILL.md` files — Claude Code reads them as skill prompts
- Hook scripts handle the backend; skills handle the UX/orchestration
- All skills currently reference `MemoryStore` — will shift to Peewee models post-migration
- Skills print inline Python code that Claude executes — not pre-built CLI commands

### Integration Points

- `/learn` direct-write needs to trigger FTS sync and embedding (currently handled by save() in Peewee migration spec)
- `/recall` needs both FTS (`Memory.search_fts()`) and vec (`VecStore.search_vector()`)
- `/reflect` calls `SelfReflector.reflect()` but delays `apply_reflection()` until user approves
- `/connect` creates threads via `NarrativeThread.create()` + `ThreadMember` records
- `/teach` creates multiple linked `Memory` records with shared tags
</code_context>

<specifics>
## Specific Ideas

- `/learn` should feel like "I'm telling you something important" — not "I'm adding to a queue that might get pruned"
- `/recall` conversational mode should feel like asking a knowledgeable colleague, not querying a database
- `/reflect` preview should make the self-model feel inspectable and controllable, not opaque
- The split from `/memory` subcommands to separate skills should make each one feel purpose-built with a crisp trigger description
</specifics>

<deferred>
## Deferred Ideas

- `/forget` redesign — current implementation is adequate, revisit after Peewee migration
- `/backfill` redesign — operational script, not a UX-facing skill, low priority
- Skill-to-skill chaining (e.g., /teach automatically calling /connect to link the parts)
</deferred>

---

_Context for: Redesign memesis skills — learn, recall, reflect, memory, teach, connect_
_Gathered: 2026-03-29_
