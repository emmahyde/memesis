# Memesis Memory Taxonomy & Deferred Patterns

## 1. Current State (memesis)

### Pipeline Architecture

Two stages, two separate prompts, type mismatch between them.

**Stage 1 — Extraction** (`OBSERVATION_EXTRACT_PROMPT`, `core/prompts.py:222`)
- Invoked by: `core/transcript_ingest.py` ← `scripts/transcript_cron.py` (15-min cron)
- Output: 0–3 observations per session slice as a JSON array
- Each observation has 4 fields: `content`, `mode`, `importance`, `tags`
- 6 type values in the `mode` field: `decision | finding | preference | constraint | correction | open_question`
- Importance anchors at lines 247–251: 0.2 (routine), 0.5 (useful), 0.8 (load-bearing decision), 0.95 (correction or hard constraint)

**Stage 2 — Consolidation** (`CONSOLIDATION_PROMPT`, `core/prompts.py:76`)
- Invoked by: `core/consolidator.py` ← `hooks/pre_compact.py` AND `hooks/consolidate_cron.py` (PreCompact hook + hourly fallback)
- Reviews ephemeral buffer with KEEP / PRUNE / PROMOTE gate
- Output JSON at line 133 has `observation_type` field with 11 values: `correction | preference_signal | shared_insight | domain_knowledge | workflow_pattern | self_observation | decision_context | personality | aesthetic | collaboration_dynamic | system_change`
- Also outputs: `concept_tags`, `files_modified`, `reinforces`, `contradicts`, `target_path`, `tags`, `title`, `summary`

### Episodic-to-Semantic Pipeline (Tulving framing)

<!-- [Added per panel C1/OD-A: Tulving episodic→semantic framing for the Stage 1→Stage 2 pipeline, as recommended by NS-F5 and OD-A consensus.] -->

The Stage 1 → Stage 2 pipeline maps onto Tulving's episodic-to-semantic memory consolidation — the most important memory transition in human cognition. Stage 1 captures temporally-tagged, session-bound observations (episodic memory): raw events tied to a specific session, cwd, and time. Stage 2 elaborates them toward context-free knowledge (semantic memory): generalizations, principles, and facts that transfer across sessions and projects. Bloom's four-type vocabulary (`factual | conceptual | procedural | metacognitive`) is kept at the `knowledge_type` field level for LLM and contributor legibility; Tulving's framing describes the architectural intent of the two-stage pipeline, not the per-memory classification scheme.

### The Type-Mismatch Bug

Stage 1 emits `mode: "decision|finding|preference|constraint|correction|open_question"`.  
Stage 2 reads the raw buffer text and re-classifies into its own 11-type schema.  
The Stage 1 type label is present in the buffer text but Stage 2 ignores it — it's treated as opaque prose, not a structured field. This means:

- A Stage 1 `finding` may become a Stage 2 `domain_knowledge`, `workflow_pattern`, or `decision_context` — or be pruned entirely
- A Stage 1 `open_question` has no corresponding Stage 2 type at all (silent drop)
- The two taxonomies have different conceptual axes (see §2), so no clean mapping exists
- No validation enforces that Stage 1 types are a subset of Stage 2 types

**Result**: type information from Stage 1 is neither preserved nor used. The `mode` field in Stage 1 output is purely cosmetic signal for the cron log.

### W2 Borrows (already done)

From `claude-mem/plugin/modes/code.json:63-99`:
- `CONCEPT_TAGS` dict at `core/prompts.py:33-41`: 7 tags — `how-it-works | why-it-exists | what-changed | problem-solution | gotcha | pattern | trade-off`
- `system_change` added to Stage 2's `observation_type` enum (11th value, `core/prompts.py:27`)
- `Memory.files_modified` field added to `core/models.py:82` with `files_list` property accessor

---

## 2. Reference Taxonomies Compared

| Dimension | memesis-extract (Stage 1) | memesis-consolidate (Stage 2) | claude-mem code.json | headroom | entroly |
|-----------|--------------------------|------------------------------|---------------------|----------|---------|
| **Axis** | Work-event (what happened) | Subject + propositional mix | Work-event (code action) | Subject (about person/entity) | Salience-based decay tier |
| **Number of types** | 6 | 11 | 8 | 6 categories + entity types | No types; just salience value |
| **Type names** | decision, finding, preference, constraint, correction, open_question | correction, preference_signal, shared_insight, domain_knowledge, workflow_pattern, self_observation, decision_context, personality, aesthetic, collaboration_dynamic, system_change | bugfix, feature, refactor, change, discovery, decision, security_alert, security_note | PREFERENCE, FACT, CONTEXT, ENTITY, DECISION, INSIGHT | — |
| **Orthogonal tags** | none | concept_tags (borrowed W2) | observation_concepts (7, same as W2) | entity_type, relationship_type | criticality, emotional_tag |
| **Importance field** | 0.0–1.0 float with anchors | not emitted by Stage 2 (implicit via KEEP gate) | not stored (type emoji only) | 0.0–1.0 with anchors | salience 3–100, maps to tick-survival |
| **Attribution** | none | none | `files_modified[]`, `files_read[]` | WHO in every fact (mandatory) | source per fragment |
| **Temporal** | none | none | none | temporal grounding required | age_ticks per memory |
| **Atomic facts** | single `content` string | `summary` ~150 chars | `facts[]` array + `narrative` | `facts[]` list, each WHO/WHAT/WHEN/WHERE | content string (single) |
| **Spatial** | none | none | `working_directory` in tool observation prompts | project-scoped DB | `source` per fragment |

**Conceptual axis diagnosis:**

- **memesis Stage 1** uses a *work-event* axis: what kind of thing happened during the session (a decision was made, something was learned, a preference was expressed). Clean and unambiguous but coarse.
- **memesis Stage 2** tries to answer *about whom/what* (personality, collaboration_dynamic, system_change) AND *what kind of knowledge* (domain_knowledge, workflow_pattern) in a single flat enum. The result is that some values are propositional (`correction`, `shared_insight`) and others are subject-categorical (`personality`, `aesthetic`). The two axes are conflated.
- **claude-mem** is cleanly *work-event* (code actions): bugfix/feature/refactor/change/discovery/decision. Orthogonal concepts are separated into `observation_concepts`. The type is the verb; the concept tags describe the knowledge shape.
- **headroom** is *subject-categorical*: memories are typed by who or what they're about (PREFERENCE about the user, FACT about entities, DECISION about choices). The extraction model enforces WHO attribution in every fact.
- **entroly** doesn't type content at all — it uses *salience* (a float) to encode memory longevity, with criticality labels as a boosting mechanism. The unit of memory is a fragment, not an observation.

---

## 3. Synthesis: Unified Schema Proposal

The existing taxonomies conflate three orthogonal axes. Recommendation: separate them into independent fields.

**Axis 1 — `kind` (propositional: what type of claim is this)**  
Borrowed from Stage 1. These are epistemically distinct:

```
decision     — a choice made, with rationale
finding      — something learned about the system or codebase
preference   — how the user wants to work
constraint   — a requirement or limit going forward
correction   — an earlier belief was wrong; state the correct version
open_question — an unresolved issue worth surfacing next session
```

**Axis 2 — `subject` (about whom/what)**  
Cleaned from Stage 2's conflated enum:

```
self         — about the AI's own tendencies or failure modes
user         — about the user's personality, aesthetics, collaboration style
system       — about the codebase, infrastructure, or tool behavior
collaboration — how we work together (delegation, trust, feedback)
workflow     — how the user thinks and operates
domain       — technical fact not obviously re-derivable
```

**Axis 3 — `work_event` (optional code-action tag, nullable)**  
Borrowed from claude-mem code.json observation_types:

```
bugfix | feature | refactor | change | discovery | decision | null
```

This is `null` for personal/preference/correction observations. Only populated when the memory traces to a discrete code action.

**Decision (2026-04-27): replace claude-mem's 7 concept_tags with academically-backed Bloom-Revised 4-dim + Zettelkasten links.**

Audit of claude-mem's 7 tags found them to be folk taxonomy: `what-changed` is tautological with the observation itself, `problem-solution` collapses procedural+factual, `gotcha`/`trade-off` both metacognitive. No academic precedent.

Replacement is a hybrid backed by:
- **Anderson & Krathwohl 2001** (revised Bloom, ~50k citations) — 4 knowledge dimensions, borrowed as a convenient four-way vocabulary; empirical validation for memory-system classification is TBD (inter-annotator agreement on this corpus must be measured before treating as reliable retrieval feature). [Removed per panel C2: "validated for retention + cross-context transfer in 50+ years of education research" — that research validated Bloom for instructional design, not memory-system classification. Krathwohl 2002 explicitly notes inter-rater reliability issues with the factual/conceptual distinction even among trained educators.]
- **Park et al. 2023** (*Generative Agents*, arXiv 2304.03442) + **Xu et al. 2024** (*A-MEM Zettelkasten*, arXiv 2502.12110) — both empirically showed closed-tag taxonomies underperform LLM-emergent specificity. A-MEM's linked-note graph beats fixed-schema baselines on long-horizon QA.

**New fields:**

- `knowledge_type` — closed enum, one of: `factual | conceptual | procedural | metacognitive` (Bloom-Revised vocabulary). Replaces `concept_tags[]`.
- `knowledge_type_confidence` — `"low" | "high"`. Only `high`-confidence values should be used as hard retrieval filters; `low`-confidence values are soft metadata only. [Added per panel C2.]
  - factual: discrete facts, terminology, specifics ("auth tokens stored in Redis with 24h TTL")
  - conceptual: mechanism, principle, model, classification ("EventBus uses copy-on-write snapshot")
  - procedural: how-to, method, sequence ("always call `_resolve_db_path` before `init_db`")
  - metacognitive: strategy, self-knowledge, vigilance, trade-off-awareness ("I default to most-powerful tool when simplest would do")
- `linked_observation_ids[]` — Zettelkasten-style graph edges to other memory IDs. Enables traversal queries beyond vector recall (A-MEM's strongest empirical result).
- `files_modified[]` — keep, borrowed W2, `Memory.files_modified` at `core/models.py:82`

**Why no separate `keywords[]`:** practically indistinguishable from `facts[]` content. The atomic-fact field already carries entity-level specificity. Adding keywords would create LLM ambiguity about where to put a token.

**Removed (W2 borrow that gets reverted):** `concept_tags[]` field + the `CONCEPT_TAGS` dict at `core/prompts.py:33-41`. Migration in §4 handles existing rows.

**New fields to add:**

- `facts[]` — atomic, attribution-required, no pronouns. Each fact is WHO + WHAT + WHEN/WHERE if applicable. Convergence of claude-mem `<facts><fact>` XML structure (`parser.ts:135`) and headroom `get_conversation_extraction_prompt()` lines 260–264
- `importance` — 0.0–1.0 float. Stage 1 already emits this. Stage 2 should emit it too. Anchors from Stage 1's `core/prompts.py:247–251` plus headroom's `extraction.py:260–268`: 0.3 background, 0.5 useful, 0.7 important, 0.9 critical
- `cwd` / `working_directory` — absolute path of working directory at time of observation. claude-mem injects this in every `buildObservationPrompt()` call at `prompts.ts:125`: `<working_directory>${obs.cwd}</working_directory>`. Multi-project attribution requires this.
- `title` — short label (≤60 chars). claude-mem uses `<title>` field; already tracked in `Memory.title`
- `subtitle` — one sentence (≤24 words), per claude-mem code.json `xml_subtitle_placeholder`. Retrieval card without re-loading full content.

**Concrete JSON schema:**

```json
{
  "id": "uuid",
  "kind": "decision | finding | preference | constraint | correction | open_question",
  "subject": "self | user | system | collaboration | workflow | domain",
  "work_event": "bugfix | feature | refactor | change | discovery | decision | null",
  "knowledge_type": "factual | conceptual | procedural | metacognitive",
  "knowledge_type_confidence": "low | high",
  "linked_observation_ids": ["other-uuid-1", "other-uuid-2"],
  "title": "Short label, ≤60 chars",
  "subtitle": "One-sentence card, ≤24 words.",
  "facts": [
    "Emma prefers explicit type annotations over inferred types in all new C# code",
    "memesis consolidator runs hourly as fallback via consolidate_cron.py"
  ],
  "content": "Full narrative content (existing field, kept)",
  "importance": 0.8,
  "files_modified": ["core/prompts.py", "core/models.py"],
  "cwd": "/Users/emmahyde/projects/memesis",
  "tags": ["taxonomy", "consolidation"],
  "source_session": "session-uuid",
  "created_at": "2026-04-27T00:00:00"
}
```

**Stage 1 / Stage 2 alignment:**

Stage 1 `OBSERVATION_EXTRACT_PROMPT` should emit `kind` (its current `mode` field, renamed) plus `importance`, `cwd`, `facts[]`, and optionally `subject`. These are the fields that survive into Stage 2's buffer.

Stage 2 `CONSOLIDATION_PROMPT` should:
1. Preserve Stage 1's `kind` field (not re-classify it)
2. Add `subject` and `work_event` as Stage 2-only enrichment
3. Emit `importance` in its output JSON (currently absent from the Stage 2 response schema at lines 122–140)
4. Emit `subtitle` as the ≤24-word retrieval card

This makes Stage 1's type label a first-class field that flows through, rather than being silently discarded.

---

## 4. Migration Considerations

### Schema migration

`Memory` model at `core/models.py:56` currently has:
- `stage` — keep; used for lifecycle tracking
- No `kind`, `subject`, `work_event` fields
- No `subtitle` field
- `files_modified` — added W2 (already exists)

**Approach**: add new fields as nullable columns with a Peewee migration. Do not remove `observation_type` (it's the existing Stage 2 label and is referenced in consolidation logs and retrieval). Instead, treat it as `legacy_type` and populate `kind` + `subject` from the back-derivation map below.

**Back-derivation map** (Stage 2 `observation_type` → new fields):

```
correction          → kind=correction,   subject=self | system (depends),  knowledge_type=metacognitive
preference_signal   → kind=preference,   subject=user,                     knowledge_type=metacognitive
shared_insight      → kind=finding,      subject=domain,                   knowledge_type=conceptual
domain_knowledge    → kind=finding,      subject=domain,                   knowledge_type=factual | conceptual
workflow_pattern    → kind=preference,   subject=workflow,                 knowledge_type=procedural
self_observation    → kind=finding,      subject=self,                     knowledge_type=metacognitive
decision_context    → kind=decision,     subject=system,                   knowledge_type=conceptual
personality         → kind=finding,      subject=user,                     knowledge_type=metacognitive
aesthetic           → kind=preference,   subject=user,                     knowledge_type=metacognitive
collaboration_dynamic → kind=finding,    subject=collaboration,            knowledge_type=metacognitive
system_change       → kind=finding,      subject=system, work_event=change, knowledge_type=factual
```

**Concept_tags → knowledge_type collapse map** (for migrating any rows already tagged with W2's claude-mem-borrowed concept_tags):

```
how-it-works       → conceptual
why-it-exists      → conceptual
what-changed       → factual          (the change itself is the observation)
problem-solution   → procedural       (when method is the focus) | factual (when fix-fact is the focus)
gotcha             → metacognitive
pattern            → conceptual       (abstract structure) | procedural (reusable sequence)
trade-off          → metacognitive
```

Where one source maps to multiple targets, the migration script flags the row for LLM-pass re-classification rather than guessing.

The `subject` derivation for `correction` and `decision_context` is ambiguous and requires LLM pass or manual review. A migration script should populate the obvious ones and flag the ambiguous rows for review.

### Prompt changes

Stage 1 `OBSERVATION_EXTRACT_PROMPT` at `core/prompts.py:222`:
- Rename `mode` → `kind` in the output JSON schema and the type guidance text (lines 231–236)
- Add `subject` to output JSON schema (optional field, Stage 2 can enrich later)
- Add `facts` to output JSON schema (array of atomic strings, Stage 1 may leave empty if not clearly attributable)
- Add `cwd` as a system-injected field (passed in from `transcript_ingest.py`, not extracted by LLM)

Stage 2 `CONSOLIDATION_PROMPT` at `core/prompts.py:76`:
- In the output JSON at lines 122–140: add `importance`, `subtitle`, `subject`, `work_event`
- Change `observation_type` label to explicitly note it's the legacy field; keep it for backward compat
- Add `kind` as a passthrough field: "preserve the `kind` from the original extraction if present; do not re-classify"
- Remove `domain_knowledge` and `personality` from the Stage 2 `observation_type` enum — these are now captured by `kind=finding, subject=domain` and `kind=finding, subject=user` respectively

**Strict subset rule**: After migration, every value in Stage 1's `kind` field must be expressible as a `kind` value in Stage 2's output. Currently `open_question` has no Stage 2 counterpart — add it or explicitly PRUNE observations of that kind during consolidation.

---

## 5. Deferred Patterns from claude-mem (Future Work)

### Mode System

**What it is**: `plugin/modes/*.json` files define per-mode observation taxonomies. The active mode controls which `observation_types` are valid, what emoji labels them, and which prompt sections are loaded. `code.json` is the default; `law-study.json`, `email-investigation.json`, `meme-tokens.json` show the range.

**Where it lives**: `claude-mem/plugin/modes/` (35+ files); loaded by `ModeManager.getInstance()` referenced in `parser.ts:144`.

**Why memesis doesn't need it yet**: memesis has one audience (software developers using Claude Code) and one session type (code sessions). The taxonomy is already domain-specific.

**Trigger**: When memesis users want non-code session capture — research sessions, writing sessions, email triage. Or when a second installation context appears (e.g., running memesis in a non-engineering project).

**Effort**: Medium. The prompt builders in Stage 1 and Stage 2 would need to become mode-parametric. The `OBSERVATION_TYPES` dict at `core/prompts.py:16` is already a good analogue for mode config — it just needs to become loadable from a JSON file.

---

### XML Schema with Fail-Fast Parser

**What it is**: `src/sdk/parser.ts` implements a discriminated-union return (`ParseResult`, line 41–43), no coercion, no silent passthrough, no lenient mode (lines 1–10). Ghost-observation filter at lines 170–179: records where every content field is null/empty are dropped. `<skip_summary reason="…"/>` is a first-class bypass signal (lines 65–81).

**Where it lives**: `claude-mem/src/sdk/parser.ts`

**Why memesis doesn't need it yet**: memesis uses JSON output from its prompts, which is validated by `json.loads()` at the call site. JSON parse failures are currently logged but not classified. The failure mode is different — a missing field silently becomes `None` rather than causing a type error.

**Trigger**: When buffer audits or consolidation logs show JSON parse failures causing silent data loss, or when prompt output starts returning partial JSON more than 1% of the time. Also trigger when `open_question` or multi-turn observations become structurally important and need richer validation.

**Effort**: Low for a Python equivalent. A `dataclasses`-based validator with `Optional` fields and explicit rejection of all-null records would mirror the ghost-observation filter. Could be done in `core/consolidator.py` at the buffer-parsing step.

---

### `<skip_summary reason="…"/>` as First-Class Signal

**What it is**: When the observer agent intentionally skips extraction (nothing worth recording), it emits `<skip_summary reason="…"/>` rather than an empty response. The parser treats this as `{ valid: true, kind: 'summary', data: { skipped: true, skip_reason: '...' } }` (parser.ts lines 65–81). This separates intentional skip from parse failure.

**Where it lives**: `claude-mem/src/sdk/parser.ts:65–81` and the `ParsedSummary.skipped` field at `parser.ts:33–34`.

**Why memesis doesn't need it yet**: memesis Stage 1 returns an empty JSON array `[]` for "nothing to extract." The consolidation stage can tell the difference between "empty buffer" and "failed extraction" from the buffer length. The distinction matters more in live observer systems (claude-mem) where the agent runs per-tool-use and silence is ambiguous.

**Trigger**: When buffer audits show unexplained empty outputs and operators can't tell whether it was intentional skip vs. prompt failure. Needed before adding a dashboard or alert on "extraction gaps."

**Effort**: Trivial. Stage 1 prompt already says "Return a JSON array (empty array if nothing qualifies)." Change to return `{"skipped": true, "reason": "..."}` instead of `[]`, and validate accordingly in `transcript_ingest.py`.

---

### Per-Tool-Use Observation (Live Observer Pattern)

**What it is**: claude-mem's primary model: every tool execution in the primary session is forwarded to a separate observer agent in real time (`buildObservationPrompt()` at `prompts.ts:100–133`). The observer sees each `<observed_from_primary_session>` block and may emit an `<observation>` or remain silent. This is N observations-per-session-second, not a batch review at the end.

**Where it lives**: `claude-mem/src/services/worker/SDKAgent.ts` (main loop), `src/sdk/prompts.ts:100–133`

**Why memesis doesn't need it yet**: memesis uses a retrospective batch model: transcript slices are processed by cron every 15 minutes. The batch model has lower cost (one LLM call per slice vs. one per tool use) and fits the cron architecture. The live model requires a persistent worker process and a message queue.

**Trigger**: When retrospective consolidation consistently misses signal that was only visible during the session — for example, intermediate reasoning steps in a long debugging session that don't appear in the final transcript. Also trigger if the 15-minute cron delay causes missed observations when sessions are short.

**Cost shape comparison**: Live observer = O(tool_uses × session_count) LLM calls. Batch = O(slices × session_count). For a session with 100 tool uses in 3 slices, live is ~33× more expensive. The live model's advantage is precision; the batch model's advantage is cost.

**Effort**: High. Requires persistent worker, message queue, hook integration at the tool-use level, and a different storage architecture than the current cron pipeline.

---

### KnowledgeAgent Corpus Priming

**What it is**: Load the full memory corpus into a separate Agent SDK session, then resume that session for Q&A queries. The corpus session is primed once and then reused via `session.resume` (KnowledgeAgent.ts lines 75–86 for prime, 191–203 for query resume). All 12 tools are blocked in the knowledge agent (lines 28–41). Auto-reprime on session expiry (lines 139–161).

**Where it lives**: `claude-mem/src/services/worker/knowledge/KnowledgeAgent.ts`

**Why memesis doesn't need it yet**: memesis retrieval uses FTS5 full-text search (`Memory.search_fts()` at `core/models.py:118`) plus vector similarity. These are sufficient for keyword and semantic lookup. Corpus priming is expensive to prime (one full-context LLM call) and adds session state management complexity.

**Trigger**: When memesis retrieval needs semantic synthesis — e.g., "what are all my past decisions about data storage?" across 50 scattered memories. That's a cross-memory reasoning query that vector search can't answer well. Or when a memesis CLI/REPL needs to hold a conversation about stored memories.

**Effort**: Medium. The architecture already has a retrieval layer; the KnowledgeAgent pattern would be an additional query path, not a replacement.

---

### SSE Observation Broadcasting

**What it is**: `ObservationBroadcaster` (imported in `ResponseProcessor.ts:27`) emits real-time SSE events to connected web UI clients on each stored observation and summary. `SSEBroadcaster.ts` in the worker manages the connection pool. The `broadcastObservation()` call at `ResponseProcessor.ts:301–318` fires after the atomic DB transaction commits.

**Where it lives**: `claude-mem/src/services/worker/agents/ResponseProcessor.ts:301–318`, `claude-mem/src/services/worker/SSEBroadcaster.ts`

**Why memesis doesn't need it yet**: memesis has no dashboard or live UI. The cron architecture means observations are stored in batch; there's no "just happened" event to stream.

**Trigger**: When a memesis dashboard or visualization is built. Also useful for real-time feedback during a debugging session: "3 observations captured so far" displayed in a terminal panel.

**Effort**: Low to medium. SSE server is simple; the harder part is a client that subscribes and renders. If the dashboard is CLI-only (e.g., `watch -n1 memesis status`), SSE isn't needed — polling suffices.

---

### Telegram/Integration Notifiers

**What it is**: `notifyTelegram()` call in `ResponseProcessor.ts:198–203` pushes observation summaries to a Telegram channel after the DB transaction commits. Fire-and-forget. Configured via user settings.

**Where it lives**: `claude-mem/src/services/integrations/TelegramNotifier.ts` (referenced in ResponseProcessor.ts:19)

**Why memesis doesn't need it yet**: memesis is a single-user local tool. Cross-channel notification only makes sense when the memory store is shared or when observations need review by a person outside the session.

**Trigger**: Cross-tool workflow where observations need to reach Slack, Linear, or a shared channel. Or when memesis is extended to multi-user scenarios.

**Effort**: Trivial to add a webhook notifier. The existing `notifyTelegram()` pattern (fire-and-forget after transaction) is the right pattern to copy.

---

### Atomic DB Transaction + Fire-and-Forget Vector Sync

**What it is**: `ResponseProcessor.processAgentResponse()` at lines 139–153 wraps DB storage in a single transaction (`sessionStore.storeObservations()`), then fires Chroma sync and SSE broadcast as non-blocking async operations after the transaction commits. Failures in Chroma sync are logged but don't roll back the DB record (lines 291–296).

**Where it lives**: `claude-mem/src/services/worker/agents/ResponseProcessor.ts:139–231`

**Why memesis doesn't need it yet**: memesis's Peewee `Memory.save()` already wraps FTS5 sync atomically (see `core/models.py:197–209`). The vector sync is separate but already fire-and-forget via the cron pipeline.

**Formal fence pattern to document**: (1) DB write is the durability fence — if it succeeds, the observation is safe. (2) Vector sync is a best-effort enrichment — if it fails, retrieval degrades but data is not lost. (3) SSE/notifiers are pure side effects — if they fail, nothing structural is affected. Memesis currently follows this pattern informally; it's worth documenting it explicitly in `core/consolidator.py` as a comment.

**Effort**: Zero — this is a documentation task, not an implementation task.

---

### Process Supervision / RestartGuard / Env Sanitization

**What it is**: `src/supervisor/` provides: PID file management, process lifecycle supervision, `RestartGuard` (tracks success/failure rate to prevent restart loops), env sanitization (`env-sanitizer.ts`) which strips sensitive vars before spawning subprocesses.

**Where it lives**: `claude-mem/src/supervisor/index.ts`, `RestartGuard.ts` (referenced in `ResponseProcessor.ts:193`), `env-sanitizer.ts`

**Why memesis doesn't need it yet**: memesis runs as cron jobs and hook scripts, not a long-running daemon. Process supervision is only needed for daemon processes that need restart-on-crash behavior.

**Trigger**: If memesis adds a persistent worker process (e.g., for live observer pattern or SSE server). The env sanitization pattern is immediately useful if memesis ever spawns subprocesses with credentials in the environment.

**Effort**: Medium. The supervisor pattern is tightly coupled to claude-mem's worker architecture. Adopting it would require memesis to adopt a daemon model first.

---

### Title + Subtitle Dual-Field

**What it is**: `ParsedObservation` at `parser.ts:17–18` has both `title` and `subtitle`. The `subtitle` is described in code.json at `xml_subtitle_placeholder`: "One sentence explanation (max 24 words)." The title is the short label; the subtitle is the retrieval card — enough context to judge relevance without loading full content.

**Where it lives**: `claude-mem/src/sdk/parser.ts:17–18`, `claude-mem/plugin/modes/code.json:115`

**Status**: Near-term borrow (listed in §3 schema proposal). `Memory.title` already exists in `core/models.py:61`. `Memory.subtitle` needs to be added.

**Trigger**: Immediately useful. Add to Stage 2 output schema and DB migration in the next prompt rewrite pass.

**Effort**: Low. One DB column, one prompt field.

---

### Verb Vocabulary Anchor

**What it is**: claude-mem's `recording_focus` prompt section (code.json line 104) explicitly lists approved verbs: "implemented, fixed, deployed, configured, migrated, optimized, added, refactored, discovered, confirmed, traced." The effect is that observations are pulled toward concrete past-tense action statements rather than vague passive-voice observations.

**Where it lives**: `claude-mem/plugin/modes/code.json:104` under `recording_focus`

**Why memesis doesn't need it yet**: memesis's Stage 1 prompt uses the behavioral framing guidance ("Phrase friction signals as workflow patterns rather than feelings") but doesn't anchor on specific verbs.

**Trigger**: When observation quality audits show a pattern of vague or present-tense statements ("user likes X" vs. "Emma rejected tailored CSS grid in favor of fixed-width panels citing scan-path predictability").

**Effort**: Trivial. One sentence added to Stage 1 and Stage 2 prompts.

---

### Spatial Awareness (Working Directory in Observation)

**What it is**: claude-mem's `buildObservationPrompt()` at `prompts.ts:125` injects `<working_directory>${obs.cwd}</working_directory>` into every tool observation. The `spatial_awareness` prompt section (code.json line 102) explains to the observer why cwd matters for multi-project attribution.

**Where it lives**: `claude-mem/src/sdk/prompts.ts:125`, `claude-mem/plugin/modes/code.json:102`

**Status**: Near-term borrow (listed in §3 schema proposal as `cwd` field). The field format in claude-mem is a full absolute path injected at observation time, not extracted by LLM.

**How to adopt**: In `scripts/transcript_cron.py`, capture the session's working directory at transcript-slice time and inject it as a non-LLM-extracted field on the Stage 1 output. Do not ask the LLM to extract cwd — it's a system fact.

**Effort**: Low. One field in the output schema, one line in the ingest script.

---

### Mode-Driven Type Guidance + Emoji per Type

**What it is**: Each observation type in code.json has `emoji` and `work_emoji` fields (e.g., `"bugfix": { "emoji": "🔴", "work_emoji": "🛠️" }`, lines 7–13). These give each type categorical visual identity in retrieval UIs and CLI output.

**Where it lives**: `claude-mem/plugin/modes/code.json:5–62`

**Why memesis doesn't need it yet**: memesis has no UI. The CLI output is plain text.

**Trigger**: When a memesis dashboard or rich CLI output (`memesis list --pretty`) is built. Emoji-per-type makes the kind/subject axes scannable at a glance.

**Effort**: Zero to low — a dict mapping `kind` values to emoji. Can be defined in `core/prompts.py` alongside `OBSERVATION_TYPES`.

---

## 6. Patterns from headroom Worth Considering

### Atomic Fact Format with WHO/WHAT/WHEN/WHERE

**Source**: `headroom/memory/extraction.py:39–77` (`FACT_EXTRACTION_PROMPT`) and `get_conversation_extraction_prompt()` at lines 184+, specifically the `ATOMIC FACT FORMAT` section at lines 259–268.

**Pattern**: Every extracted fact must be self-contained: `[WHO] [WHAT] [WHEN/WHERE if applicable]`. "Prefers tea over coffee" fails (missing WHO). "Alice prefers tea over coffee" passes. No pronouns.

**Status**: Near-term borrow — included in §3's `facts[]` field proposal. The headroom and claude-mem `facts[]` patterns converge exactly here: claude-mem's `field_guidance` (code.json line 108) says "No pronouns - each fact must stand alone."

**Effort**: Prompt change only. Add the WHO/WHAT/WHEN/WHERE contract to Stage 1's extraction guidance.

---

### Importance Scoring 0.0–1.0 with Anchors

**Source**: `headroom/memory/extraction.py:260–268` in `get_conversation_extraction_prompt()`.

**Pattern**: 0.3–0.4 background, 0.5–0.6 useful, 0.7–0.8 important, 0.9–1.0 critical. Stage 1 already has this at `core/prompts.py:247–251` with similar anchors. Headroom's version is slightly more graduated (4 bands vs. 4 anchor points).

**Status**: Already in memesis Stage 1. The gap is that Stage 2 doesn't emit `importance` in its output JSON. Fix: add `importance` to the Stage 2 response schema at `core/prompts.py:133`.

**Effort**: One line added to Stage 2 prompt output schema.

---

### Entity + Relationship Extraction as Separate Stages

**Source**: `headroom/memory/extraction.py:84–150`. Three separate prompts: `ENTITY_EXTRACTION_PROMPT` (8 entity types), `RELATIONSHIP_EXTRACTION_PROMPT` (SPO triples). Headroom's architecture uses these to populate both a vector store (facts) and a graph store (Neo4j/Qdrant).

**Pattern**: Facts → vector store for semantic retrieval. Entities + relationships → graph store for traversal queries ("all memories mentioning X that relate to Y").

**Why memesis doesn't need it yet**: memesis's `MemoryEdge` model at `core/models.py:355` provides a lightweight graph (edges between Memory nodes), but it's tag-based (`thread_neighbor`, `tag_cooccurrence`, `caused_by`, `refined_from`), not entity-graph. This is sufficient for current retrieval patterns.

**Trigger**: When memesis users want cross-entity queries — "what do I know about the relationship between the EventBus and the consolidation pipeline?" That requires entity nodes and typed relationships, not just tag co-occurrence.

**Effort**: High. Requires a separate entity extraction prompt, a new DB schema for entities and relationships, and a graph query layer. The `MemoryEdge` table would need to be extended or replaced.

---

### Hierarchical Scoping (User → Session → Agent → Turn)

**Source**: `headroom/wiki/memory.md`, "Hierarchical Scoping" section. Four scope levels: USER (persists across all sessions), SESSION (current session only), AGENT (per-agent in session), TURN (ephemeral, single turn).

**Why memesis doesn't need it yet**: memesis is project-scoped. All memories are for one user (the developer) across one project. The scope implicit in memesis is roughly headroom's SESSION level, with no per-agent or per-turn concept.

**Trigger**: If memesis is extended to multi-user scenarios (team memory), or if different agents (Claude/Codex) need separate memory namespaces within the same project. Also relevant if memesis adds a "forget after this session" ephemeral observation type.

**Effort**: Medium. Would require adding `scope` and `user_id` fields to `Memory`, and scoping all retrieval queries accordingly.

---

### Agent Provenance

**Source**: `headroom/wiki/memory.md`, "Agent Provenance" section. Every memory tracks `source_agent`, `source_provider`, `created_via`, `created_at_utc` in metadata.

**Why memesis doesn't need it yet**: memesis is single-agent (Claude Code only). There's no ambiguity about which agent wrote a memory.

**Trigger**: When multiple agents (Claude, Codex, Gemini) share the same memesis store. Without provenance, you can't audit which agent introduced a wrong belief.

**Effort**: Low to medium. One `source_agent` field on `Memory`. The cron and hook pipelines already know which script created the observation; passing that through to the Memory row is straightforward.

---

### LLM-Mediated Dedup Piggybacking

**Source**: `headroom/wiki/memory.md`, "Intelligent Deduplication" section. When a new memory is saved, headroom searches for similar existing memories (cosine similarity), then returns an enriched hint to the calling LLM. The LLM decides whether to merge — using the user's own model, not a separate dedup call.

**Why memesis doesn't need it yet**: memesis's consolidation prompt already handles reinforcement (`PROMOTE` action at `core/prompts.py:117`) and contradiction detection (lines 113–116). The Stage 2 LLM is already doing dedup reasoning. The gap is that memesis's dedup is retrospective (during consolidation) rather than at write time.

**Trigger**: When the memory store grows large enough that duplicate accumulation becomes a retrieval quality problem. Current approach (consolidation-time dedup) is adequate until then.

**Effort**: Low. Could add a pre-consolidation similarity search step in `core/consolidator.py` that surfaces potential duplicates to the consolidation prompt.

---

## 7. Patterns from entroly Worth Considering

### Salience-Based Decay Tiers

**Source**: `entroly/long_term_memory.py:79–103`. `SalienceProfile` dataclass: pinned=100 (~460 ticks), high_entropy=50 (~230 ticks), selected=30 (~138 ticks), normal=15 (~69 ticks), low_value=3 (~14 ticks). A tick = one `optimize_context()` call.

**Pattern**: Memory longevity is not set by the author — it emerges from access patterns and content properties. High-entropy fragments (information-dense) survive longer. Emotional tag multipliers (criticality="Safety" → 3× salience boost) encode domain priority.

**Why memesis doesn't have this**: memesis has no decay model. Memories accumulate indefinitely. The only removal mechanism is explicit PRUNE during consolidation or manual archival. The `archived_at` field exists but is not auto-populated by any decay process.

**Relevant memesis field**: `Memory.importance` at `core/models.py:65` could seed an initial salience score. `Memory.reinforcement_count` at `core/models.py:71` and `Memory.injection_count` at `core/models.py:72` could drive salience decay/boost.

**Trigger**: When the memory store exceeds a threshold (say, 500 active memories) and retrieval recall starts degrading because too many old low-value memories are competing with recent high-value ones. Also when memesis starts being used across many projects and memories from completed projects pollute retrieval.

**Effort**: Medium. The `spaced.py` module at `core/spaced.py` already implements spaced repetition injection scheduling (`injection_ease_factor`, `injection_interval_days` on Memory). A decay model could be layered on top of this: memories that aren't injected for N intervals have their `importance` decayed, eventually triggering auto-archive.

---

### Pinned vs. Explored Fragment Distinction

**Source**: `entroly/long_term_memory.py:87–91`. `is_pinned=True` → salience=100 (effective immortality). High-entropy + selected + high-relevance → salience=30. This creates a two-tier model: things the user explicitly pins survive indefinitely; things the system selected survive proportionally to their access frequency.

**Why memesis doesn't have this**: memesis has `[PRIORITY]` prefix convention at `core/prompts.py:87` ("MANDATORY KEEP: Observations prefixed with [PRIORITY] were explicitly stored by the user via /learn"). This is the semantic equivalent of `is_pinned=True`. The gap is that pinned memories don't get any structural treatment — they're kept at consolidation time but not treated differently in retrieval or decay.

**Trigger**: When pinned memories start being buried under high-volume auto-extracted memories. The fix is to give pinned memories a higher retrieval weight, not just a higher importance score.

**Effort**: Low. Add a `pinned` boolean to `Memory`. Retrieval scoring in `core/retrieval.py` already has `importance_score` as a component — pinned memories could get a fixed boost in that layer.

---

## 8. Recommended W5 Plan (Next Iteration)

These are concrete, shippable changes. Everything in §5/6/7 is deferred.

### Pull-list

**1. Rename `mode` → `kind` in Stage 1 output (core/prompts.py:222–265)**
- Change field name in JSON output schema
- Update type guidance text
- Update `transcript_ingest.py` to expect `kind` not `mode`
- Non-breaking if done atomically; buffer entries with `mode` field will be ignored by the consolidator (acceptable loss for one cycle)

**2. Add `importance` to Stage 2 output JSON (core/prompts.py:133)**
- Add `"importance": 0.0-1.0` to the decisions array in CONSOLIDATION_PROMPT
- Update `core/consolidator.py` to read and write this value to `Memory.importance`

**3. Add `subtitle` to Stage 2 output JSON and Memory model**
- Add `"subtitle": "≤24 words"` to Stage 2 output schema
- Add `subtitle = TextField(null=True)` to `Memory` at `core/models.py`
- Add subtitle to FTS index in `Memory._fts_insert()` (or leave out — subtitle is short enough that title+summary cover it)
- Add subtitle to `Memory._fts_delete_from_db()` if indexed

**4. Add `subject` field to Stage 2 output (core/prompts.py:133)**
- Add `"subject": "self|user|system|collaboration|workflow|domain"` to Stage 2 output
- Add `subject = TextField(null=True)` to `Memory`
- No migration required for existing rows (nullable)

**5. Add `cwd` as system-injected field to Stage 1 (scripts/transcript_cron.py + core/prompts.py)**
- Pass current working directory from the transcript ingest context
- Store on Memory as `cwd = TextField(null=True)` (not LLM-extracted)

**6. Align Stage 1 `kind` values as strict subset of Stage 2**
- Stage 2 must handle all 6 Stage 1 `kind` values: decision, finding, preference, constraint, correction, open_question
- Add `open_question` to the Stage 2 `observation_type` enum OR document the explicit PRUNE rule for open_question observations

**7. Add WHO/WHAT/WHEN/WHERE contract to Stage 1 `facts[]` (core/prompts.py:222)**
- Add `facts` array to Stage 1 output JSON schema
- Add one-sentence attribution rule: each fact must begin with a named subject, no pronouns

**8. Replace `concept_tags[]` with `knowledge_type` (Bloom-Revised) — REVERTS W2 partial**
- Add `knowledge_type = TextField(null=True)` to `Memory`, enum: `factual | conceptual | procedural | metacognitive`
- Update Stage 1 + Stage 2 prompt JSON schemas: drop `concept_tags`, add `knowledge_type` (single value, not array)
- Remove `CONCEPT_TAGS` dict at `core/prompts.py:33-41`
- Document Bloom-Revised mapping in prompt with one-line definitions
- Migration: apply concept_tags → knowledge_type collapse map from §4 to existing rows; ambiguous rows (`pattern`, `problem-solution`) flagged for LLM re-classification

**9. Add `linked_observation_ids[]` for Zettelkasten edges**
- Add `linked_observation_ids = TextField(null=True)` (JSON-encoded UUID list) to `Memory`
- Stage 2 prompt: ask LLM to identify which existing memories (from manifest) the new observation extends, contradicts, or builds on — populate `linked_observation_ids[]`
- This subsumes the existing `reinforces` / `contradicts` single-link fields; deprecate them after one cycle
- A-MEM (Xu 2024) shows graph traversal beats vector-only recall on long-horizon QA — this enables that

**10. Migration script for existing memories**
- Apply back-derivation map from §4 to populate `kind`, `subject`, `knowledge_type` on existing rows
- Apply concept_tags collapse map for any rows with W2-borrowed tags
- Flag ambiguous rows (correction, decision_context, pattern, problem-solution) with target field=null for LLM re-pass

### Tests required
- Stage 1 output validates against new schema (kind, importance, facts[], cwd, knowledge_type)
- Stage 2 output includes importance, subtitle, knowledge_type, linked_observation_ids[]
- Back-derivation map covers all 11 existing `observation_type` values without KeyError
- Concept_tags collapse map covers all 7 claude-mem tags without KeyError
- `Memory.save()` with new fields persists and round-trips through FTS index correctly
- `open_question` observations from Stage 1 are either kept or explicitly pruned by Stage 2 (no silent drop)
- `knowledge_type` enum validates: rejects values outside `{factual, conceptual, procedural, metacognitive}`
- `linked_observation_ids[]` round-trips as JSON list of valid UUIDs

---

## §9 Salience tiers + recency decay + access reinforcement (W5 add-on)

Currently `importance` is a static one-shot score. No decay, no access reinforcement. Adding the full Park 2023 / MemoryBank 2023 / entroly retention model.

### Academic basis

<!-- [Fixed per panel C1: ACT-R citation removed as basis for this formula; Park 2023 reattributed correctly; multiplicative vs additive choice documented explicitly.] -->

- **Anderson 1983** (*ACT-R*, ~10k citations) — base-level activation: `A = ln(Σ t_i^-d)` over access timestamps. **ACT-R uses power-law decay**, not exponential. This implementation departs from ACT-R: it uses exponential decay (`exp(-t/τ)`) rather than the power-law sum. ACT-R is cited here for the access-reinforcement concept only; it is not the basis for the decay formula.
- **Park et al. 2023** (*Generative Agents*, arXiv 2304.03442) — retrieval ranks memories by `α·recency + β·importance + γ·relevance` (additive weighted sum). Park is cited for the importance + recency + relevance retrieval-ranking pattern. Note: Park combines additively; this implementation combines **multiplicatively** (see note below). The multiplicative form is a deliberate design choice, not a Park implementation.
- **Zhong et al. 2023** (*MemoryBank*, arXiv 2305.10250) — Ebbinghaus forgetting curve `R = e^(-t/S)` where access boosts strength `S`. This formula is **Ebbinghaus-style exponential decay (cf. MemoryBank/Zhong 2023)** combined with a heuristic log-access boost. The closest cited precedent for the formula as implemented.
- **entroly** (`long_term_memory.py:30`) — discrete tier model: 100/50/20/5 → ~460/230/92/23 ticks. Practical but coarse vs continuous activation.

### Schema additions

```jsonc
{
  "salience_tier": "T1 | T2 | T3 | T4",   // assigned at extract from importance
  "decay_tau_hours": 720,                  // derived from tier (see table)
  "access_count": 0,                       // incremented on retrieval
  "last_accessed_at": "2026-04-27T...Z",   // updated on retrieval
  "created_at": "2026-04-27T...Z"          // for absolute-age decay if needed
  // existing: importance (0.0–1.0)
}
```

### Tier assignment (at extract time, from `importance`)

<!-- [Fixed per panel C6: τ is a time constant (decay to 1/e ≈ 37%), not a half-life (decay to 50%). Half-life = τ × ln(2) ≈ 0.693τ. Table now shows both columns.] -->

| Tier | Importance | Time constant (τ) | Half-life (≈) | Anchor |
|---|---|---|---|---|
| T1 (pinned) | ≥ 0.9 | 720h (30d) | 499h (~20.8d) | corrections, hard constraints |
| T2 (high) | 0.7–0.89 | 168h (7d) | 116h (~4.85d) | load-bearing decisions |
| T3 (normal) | 0.4–0.69 | 48h (2d) | 33h (~1.39d) | useful context |
| T4 (ephemeral) | < 0.4 | 12h | 8.3h | routine findings |

Tiers exist for UI bucketing + explainability ("why is this memory disappearing?"). The actual scoring is continuous via the activation formula below. Tier just sets τ. Note: `decay_tau_hours` is a **time constant** — the memory decays to 1/e ≈ 37% of its value after `τ` hours, not 50%. Half-life = `τ × ln(2) ≈ 0.693 × τ`.

### Activation formula (computed at retrieval)

```python
def activation(memory: Memory, now: datetime) -> float:
    age_hrs = (now - memory.last_accessed_at).total_seconds() / 3600
    recency = math.exp(-age_hrs / memory.decay_tau_hours)
    access_boost = 1 + math.log(1 + memory.access_count)  # sub-linear
    return memory.importance * recency * access_boost
```

<!-- [Fixed per panel C6: decay_tau_hours is a time constant, not a half-life. recency = exp(-t/τ) hits 1/e ≈ 0.368 at t=τ, not 0.5. Half-life = τ × ln(2).] -->
- `recency` ∈ (0, 1] — fresh = 1.0, decays to 1/e ≈ 0.368 at t=τ (not 0.5; `τ` is a time constant, not a half-life)
- `access_boost` ∈ [1, ∞) — log-curve, prevents runaway from frequent access
- Final score ranks retrieval candidates alongside vector similarity

#### Multiplicative vs. additive combination (panel hard-disagreement — unresolved)

The formula combines all three factors multiplicatively. This is a deliberate design choice for the injection use case: all three factors (importance, recency, access history) must be strong for a memory to rank. An old memory (near-zero recency) scores near zero regardless of importance or access count.

This departs from Park et al. 2023, which combines additively (`α·recency + β·importance + γ·relevance`). With additive scoring, a very old but high-importance memory can still rank. The two behaviors differ meaningfully at the tails.

Panel split: DS favors additive (Park-grounded, empirically tested on a retrieval task); LLME favors multiplicative (correct semantics for injection — you don't want to inject a near-dead memory); NS declined to endorse either without an explicit "pragmatic, not theoretically derived" statement. **Current implementation is multiplicative.** Resolution: A/B test once C4 retrieval baseline is established.

### Access reinforcement

On any retrieval (relevance hit OR linked-graph traversal):

```python
def on_access(memory: Memory):
    memory.access_count += 1
    memory.last_accessed_at = utcnow()
    memory.save()
```

This is Park's "spreading activation" — accessed memories stay alive longer. Optionally propagate a fractional access boost along `linked_observation_ids[]` edges (A-MEM-style).

### Pruning policy

Background sweep (existing `lifecycle.py` hook):

- Compute current activation for every memory
- Prune memories where `activation < 0.05` AND `salience_tier in (T3, T4)`
- T1/T2 never auto-pruned — only manual deletion
- Pruned memories retained in audit log for 30 days

### W5 add-on tasks

**11. Add salience schema fields to `Memory` model**
- `salience_tier`, `decay_tau_hours`, `access_count`, `last_accessed_at`, `created_at`
- Migration: backfill `created_at = consolidated_at` (or row creation), `last_accessed_at = created_at`, `access_count = 0`, `salience_tier` from existing importance via the table above

**12. Add `core/activation.py` module**
- `activation(memory, now) -> float`
- `assign_tier(importance) -> str`
- `decay_tau_for_tier(tier) -> int`
- `on_access(memory)` mutator

**13. Wire access reinforcement into retrieval path**
- Patch `RelevanceEngine` (or wherever `Memory.objects(...).get(...)` lands for retrieval) to call `on_access(memory)` on every hit
- Skip access bump for internal admin queries (introspection, manifest builds) — only user/agent-initiated retrieval counts

**14. Add pruning to lifecycle sweep**
- `LifecycleManager.sweep_decayed()` — compute activation, prune T3/T4 below threshold
- Log prune decisions to `pruning_log.jsonl` for audit

### Tests required (add to W5 test list)

- `assign_tier(0.95) == "T1"`, `assign_tier(0.5) == "T3"`, etc. (boundary cases at 0.4, 0.7, 0.9)
- `activation(fresh memory) ≈ importance × 1.0` (recency factor ≈ 1)
- `activation(memory aged τ hours) ≈ importance × 0.368` (e^-1 = 0.368, time-constant sanity — decay to 1/e ≈ 37% at t=τ; this is NOT a half-life check) <!-- [Fixed per panel C6] -->
- `activation` after 10 accesses > activation after 1 access (log boost monotonic)
- `on_access` increments count + updates timestamp atomically
- Pruning sweep removes T4 memory with activation 0.01, keeps T1 memory with activation 0.01
- Migration backfill: existing rows get tier assigned correctly from importance
- ACT-R formula round-trip — activation at t=0 equals importance (recency=1, access_boost=1 when access_count=0)

### What this does NOT do (deferred to later)

- Spaced-repetition revival prompts (active recall) — Anki-style scheduled re-surfacing
- Cross-memory inhibition (related-memory competition) — Anderson 1983 has a fan effect, skipping for now
- Adaptive τ per session pattern — currently τ is tier-fixed; could learn from per-user access patterns
- Importance re-scoring — currently importance is set once at extract; could be re-scored on consolidate or via reflection
