# Plan: Redesign memesis skills — learn, recall, reflect, memory, teach, connect

**Generated:** 2026-03-29
**Context doc:** `.context/CONTEXT-skill-redesign.md`
**Status:** Ready for execution

---

## Summary

10 SKILL.md files total: 2 replacements (`/learn`, `/memory`) and 8 new skills (`/recall`, `/reflect`, `/teach`, `/connect`, `/stats`, `/health`, `/threads`, `/usage`). All target the Peewee model API from the migration spec. Skills are fully independent prompt files — no compiled code, no cross-skill imports — so the only wave boundaries are grouping by concern and review load.

Wave 1 writes the two foundational input/output skills (`/learn` redesign, `/recall` new). Wave 2 writes the two process skills (`/reflect`, `/teach`). Wave 3 writes the thread and linking skills (`/connect`, `/threads`). Wave 4 writes the four analytics skills that replace `/memory` (`/stats`, `/health`, `/usage`) and retires the old `/memory` file.

---

## Cross-Wave Ownership Handoffs

| File | Wave 1 task | Wave 4 task | Notes |
|------|------------|------------|-------|
| `skills/memory/SKILL.md` | None — read-only reference | `task-4c` deletes (replaces with blank/tombstone or removes) | The split is complete only after all four replacement skills exist. Wave 4 task must not revert wave 1-3 work. |

No other files are shared across waves. Each skill file is owned by exactly one task in one wave.

---

## Wave 1 — Core input/output skills

**Rationale:** `/learn` and `/recall` are the primary read/write surface. Implementers should validate the Peewee API patterns here before the other skills reuse them.

### Task 1-A: Redesign `/learn`

**Summary:** Replace the current `/learn` skill with a two-tier write path that writes simple facts directly to `consolidated` stage and queues complex observations to the ephemeral buffer, presenting a user choice on ambiguity.

**Files owned:**
- `skills/learn/SKILL.md` (full rewrite)

**Depends on:** None (read canonical refs independently)

**Decisions:** D-01, D-02, D-03, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — `Memory.create()`, `Memory.save()`, field names, stage values
- `core/prompts.py` — `OBSERVATION_TYPES` dict for valid type names and formatting
- `.context/codebase/CONVENTIONS.md` — error handling, naming

**Acceptance criteria:**
- Frontmatter `description` is specific enough that it triggers on "remember this", "store this", corrections, and preference signals — and does not overlap with `/teach` (which handles structured multi-part knowledge)
- Procedure section distinguishes the two paths: direct write (simple fact → `consolidated` stage) vs ephemeral buffer append (complex/ambiguous → triggers choice)
- Choice presentation step is explicit: when uncertain, Claude presents the user with the two options before writing anything
- Inline Python uses `Memory.create(...)` not `MemoryStore.create(...)` — no `core/storage.py` imports
- `stage` field values match the Peewee `Check` constraint: `'ephemeral'`, `'consolidated'`, `'crystallized'`, `'instinctive'`
- Tags include `type:<observation_type>` using a valid type from `OBSERVATION_TYPES`
- Confirmation step states: what was stored, which path was taken (direct vs buffered), and the observation type
- At least 3 usage examples covering: direct simple fact, direct correction, buffered complex observation

---

### Task 1-B: Create `/recall`

**Summary:** Create the new `/recall` skill with default conversational synthesis mode and a `--detail` flag for ranked list output, merging FTS and semantic search results.

**Files owned:**
- `skills/recall/SKILL.md` (new file)

**Depends on:** None

**Decisions:** D-04, D-05, D-06, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — `Memory.search_fts()`, `VecStore.search_vector()`, `RetrievalLog`
- `.context/codebase/ARCHITECTURE.md` — per-prompt injection path, active_search pattern
- `.context/codebase/CONVENTIONS.md`

**Acceptance criteria:**
- Frontmatter `description` triggers on "what do you know about X", "recall", "search memories", "look up" — does not overlap with `/stats` or `/health`
- Default (no flag) procedure: run both `Memory.search_fts()` and `VecStore.search_vector()`, merge+deduplicate by memory ID, synthesize into a natural-language response that cites specific memory IDs (format: `[mem-id]`)
- `--detail` procedure: render a ranked list with title, summary, relevance score, stage badge (`[consolidated]`, `[crystallized]`, etc.), and retrieval method (`fts` / `semantic` / `both`)
- Inline Python shows the merge/dedup pattern explicitly — not just one search branch
- Graceful fallback documented: if `VecStore` unavailable, fall back to FTS-only with a note
- At least 2 examples: one conversational, one with `--detail`

---

## Wave 2 — Process skills

**Rationale:** `/reflect` and `/teach` both involve multi-step workflows with user interaction mid-procedure. They are independent of each other and of wave 1's output.

### Task 2-A: Create `/reflect`

**Summary:** Create the `/reflect` skill that runs `SelfReflector.reflect()`, renders a preview of findings (new patterns, deprecations, self-model changes), and applies only the items the user approves.

**Files owned:**
- `skills/reflect/SKILL.md` (new file)

**Depends on:** None

**Decisions:** D-07, D-08, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — model API shape
- `.context/codebase/ARCHITECTURE.md` — `SelfReflector` description, consolidation log, reflection interval
- `.context/codebase/CONVENTIONS.md`

**Acceptance criteria:**
- Frontmatter `description` triggers on "reflect on my patterns", "update self-model", "what have you learned about me", "self-reflection"
- Procedure has two clearly separated phases: (1) preview phase — call `SelfReflector.reflect()`, render findings without applying anything; (2) approval phase — present each finding individually and apply only approved items via `SelfReflector.apply_reflection()`
- Preview rendering distinguishes at minimum: new patterns found, observations to deprecate, self-model changes proposed
- Selective approval is explicit: user can accept all, reject all, or approve a numbered subset
- Inline Python shows how to call reflect() and apply_reflection() separately with Peewee init
- Skill notes that this can be run anytime (not just on schedule) — it complements the automatic every-5-consolidations trigger
- At least 1 example invocation

---

### Task 2-B: Create `/teach`

**Summary:** Create the `/teach` skill for decomposed multi-part knowledge: Claude breaks the input into logical parts, creates a linked `Memory` record for each, and connects them via shared tags.

**Files owned:**
- `skills/teach/SKILL.md` (new file)

**Depends on:** None

**Decisions:** D-14, D-15, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — `Memory.create()`, tag_list, stage values
- `core/prompts.py` — `OBSERVATION_TYPES` for tagging the parts
- `.context/codebase/CONVENTIONS.md`

**Acceptance criteria:**
- Frontmatter `description` triggers on "teach you about", "document our process", "explain how X works" — does not overlap with `/learn` (which is for single facts/observations)
- Procedure: Claude decomposes the input into logical parts (minimum 2, maximum ~8 reasonable parts), assigns each a title + summary + content, then writes each as a `Memory.create()` call with `stage='consolidated'`
- Each created memory shares a decomposition tag in format `teach:<slug>` to link the parts (e.g., `teach:deployment-process`)
- Each part also gets a positional tag `part:N-of-M` for ordering
- Confirmation step lists all created memory IDs and titles, plus the linking tag for future retrieval
- Inline Python shows the loop pattern for creating multiple linked memories
- Skill notes that the decomposition structure is at Claude's discretion per input — no rigid template required
- At least 2 usage examples: one procedural (deployment process), one conceptual (architecture explanation)

---

## Wave 3 — Linking and thread skills

**Rationale:** `/connect` creates `NarrativeThread` records and `/threads` reads them. They are independent of each other but both benefit from wave 2 being done (thread concept is consistent). No strict code dependency — both can be written in parallel.

### Task 3-A: Create `/connect`

**Summary:** Create the `/connect` skill for user-driven thread creation — accept memory IDs or topic descriptions, search when topics are given, present choices, and write `NarrativeThread` + `ThreadMember` records.

**Files owned:**
- `skills/connect/SKILL.md` (new file)

**Depends on:** None (independent of all prior waves at the file level)

**Decisions:** D-16, D-17, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — `NarrativeThread.create()`, `ThreadMember`, `Memory.search_fts()`
- `.context/codebase/ARCHITECTURE.md` — ThreadDetector/ThreadNarrator description, thread storage
- `.context/codebase/CONVENTIONS.md`

**Acceptance criteria:**
- Frontmatter `description` triggers on "connect these memories", "link memories", "create thread", "these are related" — does not overlap with `/threads` (which is for viewing existing threads)
- Procedure handles both input modes: (a) direct IDs — look up each `Memory.get_by_id()` and confirm titles before linking; (b) topic description — run `Memory.search_fts()`, present ranked matches, let user select by number
- Thread creation: `NarrativeThread.create(title=..., summary=...)` followed by `ThreadMember.create()` for each memory in order
- Title and summary for the new thread are composed by Claude based on the selected memories — not user-supplied (though user can override)
- Confirmation shows thread ID, title, and the ordered list of linked memory titles
- Inline Python shows both lookup paths and the NarrativeThread + ThreadMember creation sequence
- At least 2 examples: one with IDs, one with topic description

---

### Task 3-B: Create `/threads`

**Summary:** Create the `/threads` skill for thread visualization — list existing narrative threads with member counts and arc summaries, with optional drill-down into a specific thread's members and evolution.

**Files owned:**
- `skills/threads/SKILL.md` (new file)

**Depends on:** None

**Decisions:** D-11, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — `NarrativeThread`, `ThreadMember`, `thread.members` property
- `.context/codebase/ARCHITECTURE.md` — ThreadDetector, Tier 2.5 injection, thread narration
- `.context/codebase/CONVENTIONS.md`

**Acceptance criteria:**
- Frontmatter `description` triggers on "show threads", "what threads exist", "memory threads", "narrative threads" — does not overlap with `/connect`
- Default (no args): list all `NarrativeThread` records ordered by `updated_at` desc, showing title, member count, and summary
- With thread ID or title arg: show full thread detail — ordered member list with each memory's title, stage badge, importance score, and a brief quote of the memory summary
- Inline Python shows how to query `NarrativeThread.select()` and access `thread.members` for drill-down
- Skill notes that threads are also built automatically by the consolidation pipeline — `/connect` and `/threads` are the user-facing complement
- At least 2 examples: one listing all threads, one drilling into a specific thread

---

## Wave 4 — Analytics skills (split from `/memory`)

**Rationale:** These four skills replace `/memory` entirely. All four can be written in parallel. The old `skills/memory/SKILL.md` is retired in this wave — it must not be touched until all four replacements exist and are complete (enforced by placing the retirement in this wave, not earlier).

### Task 4-A: Create `/stats`

**Summary:** Create the `/stats` skill showing memory counts by stage, importance distribution, and a cross-project view of memories shared across or specific to projects.

**Files owned:**
- `skills/stats/SKILL.md` (new file)

**Depends on:** None at file level; conceptually depends on wave 1 establishing Peewee patterns

**Decisions:** D-09, D-13, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — `Memory.by_stage()`, `Memory.active()`, `project_context` field
- `.context/codebase/ARCHITECTURE.md` — stage definitions, project-scoped vs global storage
- `.context/codebase/CONVENTIONS.md`

**Acceptance criteria:**
- Frontmatter `description` triggers on "memory stats", "how many memories", "memory counts", "what have you stored" — specific enough not to overlap with `/health` or `/usage`
- Output sections: (1) counts by stage (`ephemeral`, `consolidated`, `crystallized`, `instinctive`, archived); (2) importance distribution (e.g., buckets: low 0.1–0.4, medium 0.4–0.7, high 0.7–1.0 with counts); (3) cross-project view (memories with `project_context` matching cwd vs memories with null/different `project_context`)
- Inline Python uses `Memory.by_stage()` and `Memory.active()` scopes — no raw SQL
- At least 1 example invocation

---

### Task 4-B: Create `/health`

**Summary:** Create the `/health` skill showing relevance health: memories approaching the archival threshold, stale memories already archived, and rehydration candidates that could reactivate.

**Files owned:**
- `skills/health/SKILL.md` (new file)

**Depends on:** None

**Decisions:** D-09, D-10, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — model field names (`archived_at`, `importance`, `last_used_at`)
- `.context/codebase/ARCHITECTURE.md` — `RelevanceEngine` description, archival threshold 0.15, rehydration threshold 0.30, scoring formula
- `.context/codebase/CONVENTIONS.md`

**Acceptance criteria:**
- Frontmatter `description` triggers on "memory health", "what's being archived", "health check", "stale memories" — does not overlap with `/stats`
- Output sections: (1) archive candidates — active memories with computed relevance near the 0.15 threshold (show score, title, days since last use); (2) recently archived — memories archived in the last 30 days; (3) rehydration candidates — archived memories with relevance score above 0.30 that could reactivate
- Inline Python instantiates `RelevanceEngine` with Peewee init pattern and calls `get_archival_candidates()`, `get_rehydration_candidates()`
- Skill explains the relevance formula inline in plain language so the user understands why a memory is flagged
- At least 1 example invocation

---

### Task 4-C: Create `/usage` and retire `/memory`

**Summary:** Create the `/usage` skill (injection counts, usage rates, importance trends, top/bottom used memories) and retire the old `/memory` skill by replacing its content with a tombstone redirect.

**Files owned:**
- `skills/usage/SKILL.md` (new file)
- `skills/memory/SKILL.md` (retire — replace with tombstone)

**Depends on:** All of tasks 4-A, 4-B must be complete before retiring `/memory`. At the wave level this is enforced since 4-A, 4-B, and 4-C are in the same wave — the implementer of 4-C must confirm 4-A and 4-B files exist before overwriting `skills/memory/SKILL.md`.

**Decisions:** D-09, D-12, D-18

**Canonical refs to read before implementing:**
- `.context/specs/2026-03-29-peewee-orm-migration.md` — `RetrievalLog`, `Memory` fields: `injection_count`, `usage_count`, `last_injected_at`, `last_used_at`, `importance`
- `.context/codebase/ARCHITECTURE.md` — `FeedbackLoop` description, importance score adjustment, usage detection heuristic
- `.context/codebase/CONVENTIONS.md`

**Acceptance criteria (for /usage):**
- Frontmatter `description` triggers on "usage stats", "which memories are used", "injection counts", "how often" — does not overlap with `/stats`
- Output sections: (1) most injected memories (top 10 by `injection_count`); (2) most used memories (top 10 by `usage_count`); (3) memories injected but never used (high injection, zero usage — candidates for demotion); (4) importance trend summary (memories whose importance has risen vs fallen recently, using `updated_at` as proxy)
- Inline Python queries `Memory` model fields directly — `Memory.select().order_by(Memory.injection_count.desc()).limit(10)`
- At least 1 example invocation

**Acceptance criteria (for /memory tombstone):**
- `skills/memory/SKILL.md` frontmatter `description` is updated to a non-triggering value (e.g., `"deprecated — use /stats, /health, /threads, or /usage instead"`) or the file is replaced with a clear redirect note
- The tombstone does not contain active procedure instructions that could confuse Claude into running old `MemoryStore` code

---

### Task 4-D: Create `/threads` analytics — already in Wave 3

> Note: `/threads` (Task 3-B) is the thread visualization skill. It is placed in Wave 3 alongside `/connect` because both are thread-related. Wave 4 covers the four `/memory` replacement analytics skills only.

---

## File Ownership Summary

| Wave | Task | Files owned |
|------|------|-------------|
| 1 | 1-A | `skills/learn/SKILL.md` |
| 1 | 1-B | `skills/recall/SKILL.md` |
| 2 | 2-A | `skills/reflect/SKILL.md` |
| 2 | 2-B | `skills/teach/SKILL.md` |
| 3 | 3-A | `skills/connect/SKILL.md` |
| 3 | 3-B | `skills/threads/SKILL.md` |
| 4 | 4-A | `skills/stats/SKILL.md` |
| 4 | 4-B | `skills/health/SKILL.md` |
| 4 | 4-C | `skills/usage/SKILL.md`, `skills/memory/SKILL.md` |

**Not in scope (do not touch):**
- `skills/forget/SKILL.md`
- `skills/ideate/SKILL.md`
- `skills/backfill/SKILL.md`

---

## Shared Implementation Notes (all tasks)

All tasks must read these before writing:

1. **Peewee API** — `.context/specs/2026-03-29-peewee-orm-migration.md`. No `MemoryStore` imports, no `core/storage.py` imports. Database init is `init_db(project_context=os.getcwd())` from `core/database.py`.

2. **SKILL.md format** — Mirror the existing skill format: YAML frontmatter (`name`, `description`), H1 heading, Usage block, Procedure section (numbered steps), Implementation section (Python code block), Examples section.

3. **Inline Python pattern** — Skills print Python that Claude executes at runtime. Always include `import os, sys; sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")` at the top of each code block. Always call `init_db(project_context=os.getcwd())` before any model access.

4. **Trigger descriptions** must be specific and non-overlapping. Cross-check the other skills in the same wave before finalizing.

5. **Error handling** — document what Claude should do if a query returns no results or if `VecStore` is unavailable. Keep to the established pattern: `ValueError` for domain errors, non-fatal fallback for optional subsystems.

6. **Observation types** (for `/learn` and `/teach`) — valid types from `core/prompts.py`: `correction`, `preference_signal`, `shared_insight`, `domain_knowledge`, `workflow_pattern`, `self_observation`, `decision_context`.
