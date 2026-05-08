# Discussion Log: `core/trace.py` + `/memesis:evolve` skill with `--autoresearch`

> **Audit trail only.** Do not use as input to implementation agents.
> Decisions are captured in CONTEXT-evolve-skill.md — this log preserves the alternatives considered.

**Date:** 2026-05-06
**Work:** Build pipeline trace infrastructure and a transcript-replay/eval-compile/autoresearch-loop skill.
**Areas discussed:** Trace schema + storage, Replay isolation, Eval generator, Autoresearch wiring + mutation surface + guards

---

## Trace Schema + Storage

### Q1 — Trace location

| Option                                      | Description                                                            | Selected |
| ------------------------------------------- | ---------------------------------------------------------------------- | -------- |
| `~/.claude/memesis/traces/`                 | Global, alongside index.db. Survives project moves.                    | ✓        |
| Project-local `.memesis/traces/`            | Per-repo, gitignored. Vanishes on clone.                               |          |
| `~/.claude/memesis/sessions/<id>/trace.jsonl` | Co-located with session data, follows session lifecycle.             |          |
| You decide                                  | —                                                                      |          |

**User's choice:** Global `~/.claude/memesis/traces/`.

### Q2 — Schema

| Option                              | Description                                                            | Selected |
| ----------------------------------- | ---------------------------------------------------------------------- | -------- |
| Single append-only JSONL per session | One file: `traces/<session>.jsonl`. `{ts, stage, event, payload}`.    | ✓ (Claude) |
| Per-stage JSONL files               | `traces/<session>/{extract,synthesis,...}.jsonl`. Per-stage diffing.  |          |
| SQLite table (`trace_events`)        | Queryable via SQL. Heavier write path.                                |          |
| You decide                          | —                                                                      |          |

**User's choice:** "You decide" → Claude picked single append-only JSONL. Greppable, replay-friendly, lowest ceremony.

### Q3 — Granularity

| Option                          | Description                                                       | Selected |
| ------------------------------- | ----------------------------------------------------------------- | -------- |
| Stage boundaries only           | Start/end of each stage. Lightest.                                | ✓        |
| Per-decision events             | Each card synth, keep/prune/promote, Kensinger bump.              | ✓        |
| LLM call envelopes              | Prompt hash + response + token usage.                             | ✓ (after follow-up) |
| Validator + guard outcomes      | `_card_evidence_indices_valid` results, demotion-to-orphan reasons. | ✓      |

**User's choice:** Stage boundaries + per-decision + validator/guard outcomes initially. Follow-up: LLM envelopes added when flagged as required for prompt-mutation autoresearch.

### Q3-followup — LLM envelopes

| Option                                                 | Description                                                  | Selected |
| ------------------------------------------------------ | ------------------------------------------------------------ | -------- |
| Always log envelopes (prompt hash + response + tokens) | Clean prompt-version diffs. ~5–10 events/session extra.      | ✓        |
| Envelope only when `MEMESIS_TRACE_LLM=1`               | Off by default; evolve `--autoresearch` sets the flag.       |          |
| Skip — derive from response payloads                   | Cheaper but loses prompt hash.                               |          |

**User's choice:** Always log envelopes.

### Q4 — Retention

| Option                       | Description                                                         | Selected |
| ---------------------------- | ------------------------------------------------------------------- | -------- |
| Keep all                     | Disk grows unbounded.                                               |          |
| Last N sessions (N=50)       | FIFO eviction. Bounded disk.                                        | ✓        |
| Opt-in only (`MEMESIS_TRACE=1`) | No traces unless flag set. Zero overhead for normal users.       |          |
| You decide                   | —                                                                   |          |

**User's choice:** Last 50 sessions, FIFO.

---

## Replay Isolation

### Q1 — Replay DB location

| Option                                                | Description                                                | Selected |
| ----------------------------------------------------- | ---------------------------------------------------------- | -------- |
| Tempfile per replay (`mkdtemp`)                        | Fresh `index.db`. Cleaned on exit. Mirrors test fixtures.  | ✓ (Claude after explanation) |
| `:memory:` SQLite                                      | Fastest. WAL/extension behavior diverges from prod.        |          |
| Scratch dir under `~/.claude/memesis/replay/<session>/` | Persists across replays. Manual cleanup.                |          |
| You decide                                             | —                                                          |          |

**User's choice:** Asked "What does `:memory:` SQLite mean?" → after explanation (RAM-only, no WAL, vec-extension divergence) said "go for it" → tempfile per replay.

### Q2 — Pipeline invocation

| Option                                                  | Description                                              | Selected |
| ------------------------------------------------------- | -------------------------------------------------------- | -------- |
| Direct Python function calls (in-process)               | Fastest, debuggable. Imports `core.transcript_ingest`.   | ✓        |
| Subprocess with env overrides                           | Cleaner isolation. Harder to instrument.                 |          |
| Hook simulation — `pre_compact.py` with synthetic input | Closest to prod runtime.                                 |          |

**User's choice:** Direct Python now. Cron and `pre_compact.py` simulation deferred to a later iteration.

### Q3 — LLM cost during replay

| Option                                       | Description                                                      | Selected |
| -------------------------------------------- | ---------------------------------------------------------------- | -------- |
| Live LLM every replay                        | Most accurate. Cost scales with iterations.                      |          |
| Cache by `(prompt_hash, model)` — hit-or-call | Mutation invalidates cache automatically.                       |          |
| Cache + bypass flag (`--live`)               | Default cached; `--live` forces fresh.                           | ✓ (Claude) |
| You decide                                   | —                                                                |          |

**User's choice:** "You decide" → Claude picked cache + `--live` bypass. Best balance of cost and accuracy for autoresearch loops.

### Q4 — Replay trace destination

| Option                                                    | Description                                          | Selected |
| --------------------------------------------------------- | ---------------------------------------------------- | -------- |
| Same dir, tagged `session_id=replay-<orig>-<n>`           | Unified greppable history. Counts toward N=50 budget. | ✓      |
| Separate `~/.claude/memesis/traces/replay/`                | Independent retention budget.                       |          |
| You decide                                                | —                                                    |          |

**User's choice:** Same dir, replay-tagged session_id.

---

## Eval Generator (Free-text → pytest)

### Q1 — Compile path

| Option                                              | Description                                                    | Selected |
| --------------------------------------------------- | -------------------------------------------------------------- | -------- |
| LLM generates pytest source                         | Maximum flexibility. Non-deterministic. Brittle.               |          |
| Structured template (entity-list + match-rule)      | Compiled deterministically by `core/eval_compile.py`.          |          |
| Hybrid — template by default, LLM fallback         | Template for 80%; LLM fallback for novel cases.                | ✓        |
| You decide                                          | —                                                              |          |

**User's choice:** Hybrid.

### Q2 — Match modes

| Option                                       | Description                                                       | Selected |
| -------------------------------------------- | ----------------------------------------------------------------- | -------- |
| Entity presence                              | Memory containing entity X exists in stage Y.                     | ✓        |
| Semantic similarity (cosine ≥ threshold)     | Embedding-based fuzzy match. Needs embeddings on replay store.    | ✓        |
| Polarity / `affect_valence` match            | Memory polarity matches expected (e.g., negative re tool X).      | ✓        |
| Absence assertion                            | Expected memory NOT created.                                      | ✓        |

**User's choice:** All four.

### Q3 — Eval location

| Option                                                  | Description                                       | Selected |
| ------------------------------------------------------- | ------------------------------------------------- | -------- |
| `eval/recall/<session-slug>_recall.py`                   | Existing eval/ tree. Versioned with codebase.    | ✓        |
| `~/.claude/memesis/evolve/<session>/eval.py`             | Per-replay scratch. Throwaway.                   |          |
| Both — scratch by default, `--commit` promotes          | User curates which graduate to regression suite. |          |

**User's choice:** Versioned `eval/recall/` only.

### Q4 — Failure semantics

| Option                                              | Description                                                          | Selected |
| --------------------------------------------------- | -------------------------------------------------------------------- | -------- |
| Boolean pass/fail per expected memory               | Simple. May plateau on partial wins.                                 |          |
| Score [0,1] per expected memory + aggregate          | Weighted convergence; partial improvement signal.                   |          |
| Pass/fail + diagnostic delta                         | Boolean halt + structured delta (which obs missed, which stage lost). | ✓      |

**User's choice:** Pass/fail + diagnostic delta.

---

## Autoresearch Wiring + Mutation Surface + Guards

### Q1 — Invocation

| Option                                                          | Description                                                          | Selected |
| --------------------------------------------------------------- | -------------------------------------------------------------------- | -------- |
| Skill chain via written config file                             | evolve writes YAML; user invokes /autoresearch.                     |          |
| Subprocess invocation                                           | Tight coupling. Breaks if CLI invocation changes.                    |          |
| Print-instructions mode                                         | User runs manually.                                                  |          |
| You decide                                                      | —                                                                    |          |
| (Other) Vendored autoresearch skill as sibling                  | Establish under `skills/autoresearch/` in memesis plugin. Explicit dependency, no external skill chain. | ✓ |

**User's choice (free-text):** Pull in vendored, specialized autoresearch skill. Sibling under `skills/` in memesis plugin. Explicit dependency.

### Q2 — Mutation surface

| Option                                                            | Description                                              | Selected |
| ----------------------------------------------------------------- | -------------------------------------------------------- | -------- |
| `core/prompts.py` (extract / session-type guidance)                | Drives whether/how observations are made.               | ✓        |
| `core/issue_cards.py` (`ISSUE_SYNTHESIS_PROMPT`)                   | Stage 1.5 synthesis. Fragile — needs guards.            | ✓        |
| `core/rule_registry.py` (`ParameterOverrides` thresholds)           | Numeric knobs. Safest target.                          | ✓        |
| `core/consolidator.py` + `core/crystallizer.py` logic               | Code-level mutation. Highest risk.                     | ✓        |

**User's choice:** All four. High-risk code-level mutation included — guard suite is what enforces safety.

### Q3 — Guard set

| Option                                          | Description                                                                  | Selected |
| ----------------------------------------------- | ---------------------------------------------------------------------------- | -------- |
| All `tests/test_*.py` (full unit suite)         | Strongest. ~30s+ per iteration.                                              | ✓        |
| Tier-3 invariant tests only                     | Fast targeted set: TestCardImportance et al.                                 | ✓        |
| Existing `eval/` recall checks                  | Regression on prior sessions. Prevents fix-A-break-B.                        | ✓        |
| Manifest schema + JSON round-trip               | Catches T2/T3 type-coercion regressions.                                     | ✓        |

**User's choice:** All four. Layered defense given high-risk mutation surface.

### Q4 — Loop budget

| Option                                              | Description                                              | Selected |
| --------------------------------------------------- | -------------------------------------------------------- | -------- |
| Max N iterations (N=10)                             | Hard cap.                                                |          |
| Max N + plateau detection (3 rounds no delta)       | Halts early on stuck loops.                              |          |
| Max N + token budget cap                            | Hard cost ceiling on cumulative LLM spend.               | ✓        |
| You decide                                          | —                                                        |          |

**User's choice:** Max N + token budget cap. Plateau detection deferred until real-run behavior observed.

---

## Claude's Discretion

- D-02: Trace schema (chose single append-only JSONL).
- D-06: Replay DB location (chose tempfile after explaining `:memory:` divergence).
- D-08: LLM cost handling during replay (chose cache + `--live`).
- Trace event payload field shapes — Claude picks conservative shapes, documents in module docstring.
- Trace JSONL flush cadence — Claude picks per-event flush for crash safety.
- Cache eviction policy for evolve cache dir — Claude picks simple LRU bound.
- Skill arg-parsing shape — Claude aligns with existing `skills/` patterns.

## Deferred Ideas

- Original `transcript-audit` standalone skill — folded into `evolve` default mode.
- Cron / `pre_compact.py` simulation as an evolve invocation mode.
- Plateau detection on autoresearch loop.
- Plugin cache dev-staleness workaround.
- `.claude/settings.json` gitignore.
- Wave D retrieval-side `affect_valence` RRF wiring.
- LLM-generated pytest fallback path until template proves insufficient.
- Trace SQLite mirror until JSONL grep hits limits.
