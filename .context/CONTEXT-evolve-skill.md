# Context: `core/trace.py` + `/memesis:evolve` skill (with `--autoresearch` flag)

**Gathered:** 2026-05-06
**Status:** Ready for implementation
**Codebase map:** .context/codebase/ (refreshed 2026-05-06)

<domain>
## Scope

Build two coupled deliverables:

1. **`core/trace.py`** — pipeline tracing infrastructure. Emits per-session JSONL events at stage boundaries, per-decision points, validator/guard outcomes, and LLM call envelopes. Storage at `~/.claude/memesis/traces/`. Query CLI for inspection.

2. **`/memesis:evolve <transcript>`** skill — replay a session transcript through the full pipeline against an isolated tempfile DB, elicit user-described expected memories, compile them into pytest evals under `eval/recall/`, and report a structured pass/fail+delta diagnostic. Default mode is human-in-the-loop.

3. **`--autoresearch` flag** on `evolve` — vendored autoresearch skill (sibling under `skills/`) consumes the eval delta and iterates a Modify→Verify→Keep/Discard loop over a defined mutation surface, gated by guard tests and a token budget.

Out of scope: retrieval-side affect_valence wiring (Wave D deferred), plugin cache dev-staleness workaround, settings.json gitignore.
</domain>

<decisions>
## Implementation Decisions

### Trace infrastructure (`core/trace.py`)

- **D-01:** Trace storage at `~/.claude/memesis/traces/<session_id>.jsonl` — global, alongside `index.db`. Survives project moves.
- **D-02:** Schema = single append-only JSONL per session. One file, one event per line: `{ts, stage, event, payload}`. Greppable, replay-friendly. Claude's discretion — picked over per-stage files (dir overhead) and SQLite table (overkill for Wave 1).
- **D-03:** Event granularity logged: stage boundaries (start/end of extract, synthesis, consolidate, crystallize) + per-decision events (each card synth, keep/prune/promote, Kensinger bump) + validator/guard outcomes (`_card_evidence_indices_valid` results, demotion-to-orphan reasons) + LLM call envelopes (prompt_hash + response + token usage).
- **D-04:** Retention = last 50 sessions, FIFO eviction. Replay traces (tagged `session_id=replay-<orig>-<n>`) count toward the same budget.
- **D-05:** Replay traces emit to the same `traces/` dir, tagged with replay session_id. Unified greppable history.

### Replay isolation (`/memesis:evolve` core)

- **D-06:** Replay DB = tempfile-per-replay (`tempfile.mkdtemp` + index.db init). Mirrors prod path (apsw + sqlite-vec + WAL). Cleaned on exit. Rejects `:memory:` due to WAL/extension divergence risk.
- **D-07:** Pipeline invocation = direct Python function calls in-process (`from core.transcript_ingest import …`, pass `store=replay_store`). Fastest, debuggable. Cron / `pre_compact.py` simulation deferred to a later iteration.
- **D-08:** LLM cost during replay = cache by `(prompt_hash, model)` keyed dict on disk under `~/.claude/memesis/evolve/cache/`. First call hits live; subsequent replays of the same prompt hit cache. Mutation invalidates cache automatically (prompt content changes → hash changes). `--live` flag forces fresh.

### Eval generator (free-text → pytest)

- **D-09:** Compile path = hybrid. Template by default: user free-text → LLM extracts `{expected_entities, polarity, stage_target, match_mode}` → compiled deterministically by a new `core/eval_compile.py` into pytest. LLM-generated pytest as fallback only for assertions the template cannot express.
- **D-10:** Match modes supported: entity presence, semantic similarity (cosine ≥ threshold via embeddings on replay store), polarity / `affect_valence` match, absence assertion (memory NOT created — e.g., user wanted ephemeral pruned).
- **D-11:** Compiled evals land at `eval/recall/<session-slug>_recall.py`. Picked up by existing `eval/conftest.py` collection patterns. Versioned with codebase.
- **D-12:** Failure semantics = pass/fail per expected memory (boolean halt condition for autoresearch) + structured diagnostic delta (which observations missed, which stage lost the memory, which match-mode failed). Delta is the autoresearch signal.

### Autoresearch wiring (`--autoresearch`)

- **D-13:** Autoresearch invocation = vendored, specialized autoresearch skill. Establish under `skills/autoresearch/` as a sibling of `skills/learn/`, `skills/recall/`, etc. `evolve --autoresearch` invokes it explicitly. Memesis plugin depends on its own vendored copy — no external skill dependency.
- **D-14:** Mutation surface (autoresearch may modify):
  - `core/prompts.py` — `OBSERVATION_EXTRACT_PROMPT`, `SESSION_TYPE_GUIDANCE`
  - `core/issue_cards.py` — `ISSUE_SYNTHESIS_PROMPT` (fragile per CONCERNS — full guard suite must hold)
  - `core/rule_registry.py` — `ParameterOverrides` thresholds (Kensinger bump, importance gates, numeric knobs)
  - `core/consolidator.py` + `core/crystallizer.py` — code-level logic mutation. Highest-risk surface; guard tests enforce invariants.
- **D-15:** Guard set (must all pass after every mutation, before keep):
  - Full unit suite: `python3 -m pytest tests/`
  - Tier-3 invariant tests explicitly: `TestCardImportance`, `TestAllIndicesInvalidDemotion`, `TestRule3KensingerRemoved`, `TestEvidenceIndicesValidation`
  - Existing `eval/recall/` regression — prevents fixing session A by breaking session B
  - Manifest schema + JSON round-trip checks (T2/T3 type coercion)
- **D-16:** Loop budget = max N iterations (default N=10) **+** token budget cap (cumulative LLM token spend ceiling). Halt on whichever hits first. Plateau detection deferred — re-evaluate after first real run shows behavior.

### Claude's Discretion

- Trace event payload field shapes (what each `event` type's `payload` looks like) — pick conservative shapes, document in `core/trace.py` module docstring.
- Trace JSONL flush cadence (per-event vs batched on stage boundary) — pick per-event for crash safety unless perf measurements say otherwise.
- Cache eviction for `~/.claude/memesis/evolve/cache/` — pick a simple LRU bound (~500 MB) without ceremony.
- Skill arg-parsing shape inside `/memesis:evolve` — pick what aligns with existing skills under `skills/`.
</decisions>

<canonical_refs>

## Canonical References

**Downstream agents MUST read these before implementing.**

### Design ethos & conventions

- `.context/DESIGN_ETHOS.md` — north star for memesis architecture decisions
- `.context/codebase/CONVENTIONS.md` — T1/T2/T3 wave patterns, write-site discipline, Kensinger single-site rule, JSON round-trip
- `.context/codebase/ARCHITECTURE.md` — Stage 1.5, transcript pipeline data flow, card→Memory promotion
- `.context/codebase/CONCERNS.md` — `ISSUE_SYNTHESIS_PROMPT` fragility, importance flow regression risk, evidence_obs_indices fragility
- `.context/codebase/TESTING.md` — pytest patterns, eval harness collection, MEMORY_LIFECYCLE_REAL_LLM=1 opt-in
- `AGENTS.md` (project) — directory structure, hook behavior, key entry points
- `CLAUDE.md` (project) — persistence + LLM transport rules

### Pipeline mutation targets

- `core/issue_cards.py` (lines 44–205) — `ISSUE_SYNTHESIS_PROMPT`
- `core/prompts.py` — `OBSERVATION_EXTRACT_PROMPT`, `SESSION_TYPE_GUIDANCE`, `EMOTIONAL_STATE_PATTERNS`
- `core/rule_registry.py` — `ParameterOverrides` (precedent: telemetry → behavior)
- `core/consolidator.py` (lines 524–538) — `_execute_keep()` write-site (Kensinger single-site at :535)
- `core/crystallizer.py` — crystallization gates
- `core/card_validators.py` — `_card_evidence_indices_valid()`

### Pipeline orchestration / replay surface

- `core/transcript_ingest.py` — pipeline orchestration entry point for direct in-process invocation
- `core/database.py` — schema, WAL, migrations (replay DB init must call same path)
- `core/llm.py` — `call_llm()` shared transport (cache wrapper hooks here)
- `core/embeddings.py` + `core/vec.py` — needed for semantic-similarity match mode

### Hook + skill plumbing

- `hooks/hooks.json` — registered hook wiring (referenced for D-07's deferred cron/hook simulation)
- `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` — plugin manifest (sibling skill registration follows existing skill layout)

### Existing observability precedents

- `scripts/audit_pipeline_dimensions.py` — closest existing observability tool; informs trace query CLI shape
- `eval/conftest.py` — fixture patterns; `eval/recall/<slug>_recall.py` files are auto-collected
</canonical_refs>

<code_context>

## Codebase Insights

### Reusable Assets

- `core/rule_registry.py` — telemetry → behavior precedent for autoresearch's mutation of numeric thresholds. Existing pattern: parameter overrides loaded at runtime. Extend rather than parallel-build.
- `core/llm.py:call_llm()` — single-site for LLM transport. Cache wrapper for D-08 hooks here, not at call sites.
- `eval/conftest.py` — `live_store` + `eval_engine` fixtures already exist. Compiled evals plug into the existing collection (`*_recall.py`).
- `scripts/audit_pipeline_dimensions.py` — model the trace query CLI on this script's invocation pattern.
- Existing pipeline already emits per-stage stats dicts — `trace.py` formalizes and persists these instead of duplicating.

### Established Patterns

- All persistence through `MemoryStore` / `database.py`. Replay DB initialization must use the same `init_db()` path — do not hand-roll schema.
- All LLM calls through `core.llm.call_llm()`. Cache wrapper attaches at this seam for D-08.
- Atomic writes use `tempfile.mkstemp` + `shutil.move`. Trace JSONL writes follow this pattern (per-event flush + atomic rename on rotation).
- `logger = logging.getLogger(__name__)` standard, no global config. Trace events are structured data, not log messages — separate channel.
- Module-deferred SQLite imports (apsw, sqlite-vec) at module level. Replay tempfile DB respects this.

### Integration Points

- **Trace emission seams:** stage boundaries in `core/transcript_ingest.py`; per-decision sites in `core/consolidator.py:_execute_keep/_execute_prune/_execute_promote`, `core/crystallizer.py`, `core/issue_cards.py` synthesis; validator outcomes in `core/card_validators.py`; LLM envelopes wrap inside `core/llm.py:call_llm()`.
- **Replay store:** `core/transcript_ingest.py` accepts a store argument — confirm and route to tempfile-backed `MemoryStore` from `evolve` driver.
- **Autoresearch sibling skill:** lives at `skills/autoresearch/SKILL.md`. Reads autoresearch config from `~/.claude/memesis/evolve/<session>/autoresearch.yaml` written by `evolve --autoresearch`. Loose-coupled handoff.
- **Eval compile output:** `eval/recall/<slug>_recall.py` files are runnable directly via `python3 -m pytest eval/recall/<slug>_recall.py`. Autoresearch loop runs the single compiled file as its convergence signal; runs the full guard set as the keep gate.

### Concerns That Apply

- **`ISSUE_SYNTHESIS_PROMPT` fragility** (CONCERNS): autoresearch mutating it must run the full guard suite, not the lite tier-3 set alone.
- **Importance flow regression risk** (CONCERNS): `_execute_promote()` card-field wiring lacks coverage. Adding traces around this seam doubles as a coverage win.
- **Kensinger single-site invariant** (CONVENTIONS): trace must record the Kensinger bump at `consolidator.py:535` so autoresearch can detect re-introduction at other sites as a guard violation.
- **JSON type coercion** (CONCERNS): manifest round-trip guard catches T2/T3 field regressions when autoresearch mutates code.
- **Plugin cache staleness** (CONCERNS): trace + evolve infrastructure runs from project source during dev. After install, `~/.claude/plugins/cache/memesis-local/memesis/0.2.0/` must be rebuilt for hook-runtime changes — note in skill output, do not solve here.
</code_context>

<specifics>
## Specific Ideas

- Single JSONL per session (one file, one line per event) is the chosen schema specifically because autoresearch diffs across iterations need plain-text greppable history without DB joins.
- Cache key for D-08 = `sha256(model + prompt)`. Mutation invalidation is automatic, not heuristic.
- Token-budget cap (D-16) is a hard ceiling, not soft — autoresearch halts mid-iteration if exceeded, no "finish current round" grace period. Re-run with raised budget if user wants to continue.
- Vendored autoresearch skill (D-13) means memesis ships its own copy. Updates to upstream autoresearch are pulled in deliberately, not auto-tracked. Stability over freshness.
</specifics>

<deferred>
## Deferred Ideas

- **Original `transcript-audit` protocol** — folded entirely into `/memesis:evolve` default (no-flag) mode. No separate skill.
- **Cron / `pre_compact.py` hook simulation as an evolve invocation mode** (D-07 alt) — defer until in-process direct invocation has shipped.
- **Plateau detection on autoresearch loop** (D-16 alt) — defer until first real loop run shows whether plateau is a real failure mode.
- **Plugin cache dev-staleness workaround** — out of scope. Documented in CONCERNS.
- **`.claude/settings.json` gitignore** — out of scope, separate housekeeping.
- **Wave D deferred: retrieval-side `affect_valence` RRF wiring** — out of scope; tracked in CONCERNS.
- **LLM-generated pytest fallback shape** (D-09 fallback path) — defer until a template-uncoverable assertion shows up in real use.
- **Trace SQLite mirror** (D-02 alternative) — defer; revisit if greppable JSONL hits performance / query limits.
</deferred>

---

_Context for: `core/trace.py` + `/memesis:evolve` skill with `--autoresearch` flag_
_Gathered: 2026-05-06_
