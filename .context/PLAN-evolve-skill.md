# Plan: core/trace.py + /memesis:evolve skill + --autoresearch flag

**Source:** .context/CONTEXT-evolve-skill.md
**Generated:** 2026-05-06
**Status:** Ready for execution

## Overview

Build pipeline tracing infrastructure (`core/trace.py`), a `/memesis:evolve` skill that replays transcripts through an isolated DB and compiles pytest evals, and an `--autoresearch` mutation loop that iterates over a defined mutation surface gated by a guard suite and a token budget.

**Waves:** 5
**Total tasks:** 14

---

## Wave 1: Foundation — net-new modules only, no edits to existing code

**Prerequisite:** None (first wave)

### Task 1.1: core/trace.py — JSONL trace writer, event schema, retention

Deliver a standalone trace module: `TraceWriter` class, event schema, per-session JSONL append, atomic FIFO rotation (50-session cap), and public `emit()` API. No edits to any existing file.

- **Files owned:**
  - `core/trace.py`
  - `tests/test_trace.py`
- **Depends on:** None
- **Decisions:** D-01, D-02, D-03, D-04, D-05
- **Acceptance criteria:**
  - [ ] `TraceWriter(session_id)` creates `~/.claude/memesis/traces/<session_id>.jsonl` on first `emit()` call
  - [ ] Each `emit(stage, event, payload)` appends a single line `{"ts": "<iso>", "stage": "...", "event": "...", "payload": {...}}` to the file; no batching
  - [ ] Atomic rotation: when session count exceeds 50, the oldest session file is removed using `tempfile.mkstemp` + `shutil.move` idiom for the counter/index file; trace files themselves are deleted directly
  - [ ] Replay session IDs (`session_id` matching `replay-<orig>-<n>` pattern) count against the same 50-session budget
  - [ ] `emit()` does NOT use `logging.getLogger` — structured data only, separate channel
  - [ ] Module docstring documents all event type `payload` shapes: `stage_boundary`, `card_synth`, `keep`, `prune`, `promote`, `kensinger_bump`, `validator_outcome`, `llm_envelope`
  - [ ] `tests/test_trace.py` covers: emit creates file, per-event flush, retention eviction at 51st session, replay session counted in budget, payload roundtrip via `json.loads`

---

### Task 1.2: core/eval_compile.py — free-text → pytest template compiler skeleton

Deliver the eval compiler: `EvalSpec` dataclass (fields: `expected_entities`, `polarity`, `stage_target`, `match_mode`), LLM extraction of spec from free-text description, deterministic template renderer producing a valid pytest file, and the four match modes as enum members.

- **Files owned:**
  - `core/eval_compile.py`
  - `tests/test_eval_compile.py`
- **Depends on:** None
- **Decisions:** D-09, D-10, D-11, D-12
- **Acceptance criteria:**
  - [ ] `EvalSpec` dataclass has fields: `slug: str`, `expected_entities: list[str]`, `polarity: str | None`, `stage_target: str | None`, `match_mode: Literal["entity_presence", "semantic_similarity", "polarity_match", "absence"]`
  - [ ] `extract_spec_from_text(description: str) -> EvalSpec` calls `core.llm.call_llm()` (NOT a direct Anthropic client) to populate the spec; returns a deterministic `EvalSpec`
  - [ ] `compile_to_pytest(spec: EvalSpec, replay_store_path: str) -> str` returns a valid Python source string that, when written to `eval/recall/<slug>_recall.py`, is collected by pytest via existing `eval/conftest.py` collection rules
  - [ ] All four match modes produce syntactically valid pytest output; `absence` mode asserts the memory was NOT created
  - [ ] LLM fallback path for assertions the template cannot express is stubbed with a `# TODO: LLM-generated assertion fallback` comment (per CONTEXT deferred section)
  - [ ] `tests/test_eval_compile.py` covers: spec extraction mock (patches `core.eval_compile.call_llm`), each match mode renders valid Python (verified via `compile()` builtin), absence mode inverts assertion

---

### Task 1.3: replay DB driver helper

Deliver a `ReplayDB` context manager that creates an isolated tempfile DB using the same `init_db()` path (no hand-rolled schema), exposes the `base_dir` for constructing a `MemoryStore`, and cleans up on exit. Also delivers the LLM cache wrapper module.

- **Files owned:**
  - `core/replay_db.py`
  - `core/llm_cache.py`
  - `tests/test_replay_db.py`
- **Depends on:** None
- **Decisions:** D-06, D-08
- **Acceptance criteria:**
  - [ ] `ReplayDB()` as a context manager: `__enter__` calls `tempfile.mkdtemp()`, then `init_db(base_dir=<tempdir>)`, yields `base_dir`; `__exit__` calls `close_db()` then `shutil.rmtree(tempdir)`
  - [ ] Rejects `:memory:` path — the constructor raises `ValueError` if called with `db_path=":memory:"` (enforcing D-06's reasoning)
  - [ ] Resulting DB uses WAL mode (verifiable via `PRAGMA journal_mode` query inside context)
  - [ ] `core/llm_cache.py` exports `cached_call_llm(model, prompt, **kwargs) -> str` — wraps `core.llm.call_llm()`, keyed by `sha256(model + prompt)`, stored at `~/.claude/memesis/evolve/cache/<sha256>.json`
  - [ ] Cache hit returns stored response without calling `call_llm()`; `--live` flag (`force_live=True` kwarg) bypasses cache
  - [ ] Cache eviction: if total cache dir size exceeds 500 MB, oldest files (by mtime) are removed until under limit
  - [ ] `tests/test_replay_db.py` covers: context manager creates and cleans up, WAL mode verified, `:memory:` rejection, cache hit/miss (mocked `call_llm`), cache sha256 key derivation, `force_live` bypasses cache

---

## Wave 2: Instrumentation — one task per modified existing file

**Prerequisite:** Wave 1 complete (Task 1.1 delivers `core/trace.py` schema and `emit()` API)

### Task 2.1: Instrument core/llm.py — LLM envelope trace events + cache wrapper hook

Wire `TraceWriter.emit()` for LLM call envelopes (prompt_hash, model, token usage) inside `call_llm()`. Also hook `llm_cache.py`'s `cached_call_llm` at this seam so the cache wrapper is available for `evolve`'s in-process replay without modifying call sites.

- **Files owned:**
  - `core/llm.py`
  - `tests/test_llm.py`
- **Depends on:** Task 1.1, Task 1.3
- **Decisions:** D-03, D-08
- **Acceptance criteria:**
  - [ ] `call_llm()` emits a `llm_envelope` trace event: `{"prompt_hash": sha256(model+prompt)[:16], "model": model, "input_tokens": N, "output_tokens": N}` when a `TraceWriter` is active for the current session; no-ops if no active writer
  - [ ] Token usage (`response.usage.input_tokens`, `response.usage.output_tokens`) is extracted and included in the trace payload; previously discarded (resolves the low-severity concern in CONCERNS)
  - [ ] Active `TraceWriter` is passed via a thread-local or module-level `_active_trace` context variable — NOT stored in the Anthropic client object
  - [ ] `call_llm()` signature is unchanged for all existing callers
  - [ ] `tests/test_llm.py` covers: `llm_envelope` event emitted with correct hash and token counts (mock response), no-op when no active trace writer

---

### Task 2.2: Instrument core/transcript_ingest.py — stage boundary trace events

Wire `emit()` at stage-start and stage-end boundaries for the transcript extraction pipeline: `stage1_extract_start`, `stage1_extract_end`, `stage15_synthesis_start`, `stage15_synthesis_end`.

- **Files owned:**
  - `core/transcript_ingest.py`
- **Depends on:** Task 1.1
- **Decisions:** D-03, D-05
- **Acceptance criteria:**
  - [ ] `stage1_extract_start` event emitted before the Stage 1 LLM call loop, payload includes `n_windows`, `session_id`
  - [ ] `stage1_extract_end` event emitted after the dedup + drop-gate step, payload includes `n_obs_pre_dedup`, `n_obs_post_dedup`, `n_dropped`
  - [ ] `stage15_synthesis_start` emitted before `synthesize_issue_cards()` call, payload includes `n_obs_input`
  - [ ] `stage15_synthesis_end` emitted after synthesis returns, payload includes `n_cards`, `n_orphans`, `n_invalid_indices_demoted`
  - [ ] Trace emission is conditional: only fires when a `TraceWriter` is available (no regression if tracing not initialized)
  - [ ] No existing `transcript_ingest.py` caller or test is broken (all existing tests in `tests/test_somatic.py`, `tests/test_relevance.py`, `tests/test_retrieval.py` continue to pass)

---

### Task 2.3: Instrument core/consolidator.py — per-decision trace events

Wire `emit()` at each consolidator decision site: `keep`, `prune`, `promote`, and the Kensinger bump.

- **Files owned:**
  - `core/consolidator.py`
- **Depends on:** Task 1.1
- **Decisions:** D-03, D-05
- **Acceptance criteria:**
  - [ ] `keep` event emitted inside `_execute_keep()` after the memory is saved, payload includes `memory_id`, `importance`, `affect_valence`, `stage`
  - [ ] `prune` event emitted inside `_execute_prune()`, payload includes `content_hash_prefix` (first 8 chars of MD5), `reason`
  - [ ] `promote` event emitted inside `_execute_promote()`, payload includes `memory_id`, `from_stage`, `to_stage`
  - [ ] `kensinger_bump` event emitted at `consolidator.py:534-535` (the sole Kensinger site) when bump is applied, payload includes `memory_id`, `pre_bump_importance`, `post_bump_importance`
  - [ ] Trace emission conditional on active writer; all existing `TestConsolidateKeep`, `TestConsolidatePrune`, `TestConsolidatePromote`, `TestCardImportance` tests continue to pass

---

### Task 2.4: Instrument core/crystallizer.py and core/issue_cards.py — synthesis + crystallization events

Wire `emit()` at the crystallizer synthesis boundary and at the card synthesis site in `issue_cards.py`.

- **Files owned:**
  - `core/crystallizer.py`
  - `core/issue_cards.py`
- **Depends on:** Task 1.1
- **Decisions:** D-03, D-05
- **Acceptance criteria:**
  - [ ] `crystallize_group_start` event in `_crystallize_group()`, payload includes `group_size`, `memory_ids: list`
  - [ ] `crystallize_group_end` event after synthesis, payload includes `crystallized_memory_id`, `sources_archived: int`
  - [ ] `card_synth_start` event in `synthesize_issue_cards()` before LLM call, payload includes `n_obs`
  - [ ] `card_synth_end` event after LLM parse, payload includes `n_cards_raw`, `parse_ok: bool`
  - [ ] Trace emission conditional on active writer; all existing `test_crystallizer.py` and `test_issue_cards.py` tests continue to pass

---

### Task 2.5: Instrument core/card_validators.py — validator/guard outcome trace events

Wire `emit()` at the demotion-to-orphan sites in the card validator invocation within `issue_cards.py`.

- **Files owned:**
  - `core/card_validators.py`
- **Depends on:** Task 1.1
- **Decisions:** D-03
- **Acceptance criteria:**
  - [ ] `validator_outcome` event emitted for each card that fails `_card_evidence_indices_valid()`, payload includes `card_title`, `invalid_indices: list`, `window_count`
  - [ ] `validator_outcome` event emitted for each card that fails `_card_evidence_load_bearing()`, payload includes `card_title`, `reason: "circular_evidence"`
  - [ ] Events distinguish demotion type via `event` field: `"indices_invalid_demotion"` vs `"load_bearing_demotion"`
  - [ ] Conditional on active writer; all existing `TestAllIndicesInvalidDemotion` and `TestEvidenceIndicesValidation` tests continue to pass

---

## Wave 3: Evolve skill — replay driver, eval output, diagnostic delta

**Prerequisite:** Wave 1 and Wave 2 complete

### Task 3.1: /memesis:evolve skill + scripts/evolve.py driver

Deliver the skill definition and the orchestrator script that drives the full replay-and-eval workflow: accept a transcript path, spin up a `ReplayDB`, invoke the pipeline in-process, elicit expected-memory descriptions from the user, compile them to pytest evals, run the guard set, and emit a structured diagnostic delta.

- **Files owned:**
  - `skills/evolve/SKILL.md`
  - `scripts/evolve.py`
  - `tests/test_evolve_skill.py`
- **Depends on:** Task 1.1, Task 1.2, Task 1.3, Task 2.1, Task 2.2, Task 2.3, Task 2.4, Task 2.5
- **Decisions:** D-05, D-06, D-07, D-08, D-09, D-10, D-11, D-12, D-13
- **Acceptance criteria:**
  - [ ] `skills/evolve/SKILL.md` documents invocation: `/memesis:evolve <transcript_path> [--autoresearch] [--live]`; skill arg-parsing follows existing skill layout under `skills/`
  - [ ] `scripts/evolve.py` accepts `--transcript`, `--autoresearch`, `--live` flags; `--live` passes `force_live=True` to `cached_call_llm`
  - [ ] Driver creates a `ReplayDB`, initializes a `TraceWriter` with `session_id = f"replay-{orig_session_id}-{n}"`, and invokes `transcript_ingest` with the tempfile store
  - [ ] Driver prompts user for expected-memory descriptions (stdin/stdout), passes each to `extract_spec_from_text()`, compiles with `compile_to_pytest()`, writes to `eval/recall/<slug>_recall.py`
  - [ ] Driver runs `python3 -m pytest tests/ eval/recall/<slug>_recall.py` and captures pass/fail per expected memory
  - [ ] Diagnostic delta output: for each failing expected memory, reports which stage lost it (traceable via JSONL events) and which match mode failed
  - [ ] `ReplayDB` cleanup on normal exit AND on exception (verify via test)
  - [ ] `tests/test_evolve_skill.py` covers: replay DB created and cleaned up, trace writer initialized with correct replay session_id, eval file written to correct path, guard suite invocation command is correct (subprocess mock)

---

## Wave 4: Autoresearch skill — mutation engine, loop, budget cap

**Prerequisite:** Wave 3 complete

### Task 4.1: skills/autoresearch/SKILL.md + core/autoresearch.py mutation engine

Deliver the vendored autoresearch skill and its Python engine: reads the `autoresearch.yaml` config, iterates the Modify→Verify→Keep/Discard loop over the defined mutation surface, runs the guard suite as the keep gate, and halts on token budget or iteration cap.

- **Files owned:**
  - `skills/autoresearch/SKILL.md`
  - `core/autoresearch.py`
  - `tests/test_autoresearch.py`
- **Depends on:** Task 3.1
- **Decisions:** D-13, D-14, D-15, D-16
- **Acceptance criteria:**
  - [ ] `skills/autoresearch/SKILL.md` documents the vendored-copy policy: memesis ships its own copy; updates to upstream are pulled deliberately (D-13)
  - [ ] `Autoresearcher` class reads `~/.claude/memesis/evolve/<session>/autoresearch.yaml` for `max_iterations` (default 10) and `token_budget` (cumulative ceiling)
  - [ ] Mutation surface is limited to the files listed in D-14: `core/prompts.py`, `core/issue_cards.py`, `core/rule_registry.py`, `core/consolidator.py`, `core/crystallizer.py`. Attempts to mutate any other file raise `ValueError`
  - [ ] Guard set (D-15) must ALL pass after each mutation before `keep`: `python3 -m pytest tests/` + explicit tier-3 tests (`TestCardImportance`, `TestAllIndicesInvalidDemotion`, `TestRule3KensingerRemoved`, `TestEvidenceIndicesValidation`) + `eval/recall/` + manifest JSON round-trip check
  - [ ] Halt semantics: hard halt when iteration count reaches `max_iterations` OR cumulative token spend (tracked via `llm_envelope` trace events) exceeds `token_budget`; mid-iteration halt if token cap is exceeded — no "finish current round" grace period (D-16)
  - [ ] On `keep`: mutation is written to disk atomically (`tempfile.mkstemp` + `shutil.move`); `autoresearch.yaml` updated with iteration count and token spend
  - [ ] On `discard`: working copy reverted to pre-mutation state via `git checkout -- <file>` (safe because mutation surface files are version-controlled)
  - [ ] `tests/test_autoresearch.py` covers: guard failure causes discard (mock guard), token budget exhaustion halts mid-iteration, out-of-surface file mutation raises ValueError, keep/discard atomic write verified, iteration count tracking

---

### Task 4.2: scripts/evolve.py --autoresearch wiring + autoresearch.yaml config writer

Wire the `--autoresearch` flag in `scripts/evolve.py` to invoke `Autoresearcher` with the diagnostic delta from Task 3.1, and write the `autoresearch.yaml` config before invoking.

- **Files owned:**
  - `scripts/evolve.py`
  - `scripts/autoresearch_config.py`
- **Depends on:** Task 3.1, Task 4.1
- **Decisions:** D-13, D-16
- **Acceptance criteria:**
  - [ ] When `--autoresearch` is passed, `scripts/evolve.py` writes `~/.claude/memesis/evolve/<session>/autoresearch.yaml` with `max_iterations: 10`, `token_budget: <user-configured or default>`, `mutation_surface: [list of D-14 files]`, `guard_suite: [command strings]`
  - [ ] `Autoresearcher` is instantiated with the session path and the compiled eval slug from Task 3.1's diagnostic delta
  - [ ] `scripts/autoresearch_config.py` provides `write_autoresearch_config(session_id, **overrides)` used by `evolve.py`; config written atomically
  - [ ] `--autoresearch` without a compiled eval (i.e., zero failing expected memories) exits cleanly with message "No failing evals — autoresearch not triggered"

---

## Wave 5: CLI + integration tests

**Prerequisite:** Waves 1–4 complete

### Task 5.1: scripts/trace_query.py — trace inspection CLI

Deliver a CLI tool modeled on `scripts/audit_pipeline_dimensions.py` for querying trace JSONL files: filter by session, event type, stage, and time range; pretty-print or JSON output.

- **Files owned:**
  - `scripts/trace_query.py`
- **Depends on:** Task 1.1
- **Decisions:** D-01, D-02, D-03
- **Acceptance criteria:**
  - [ ] `python3 scripts/trace_query.py --session <id>` prints all events for that session in chronological order
  - [ ] `--event <type>` filters to a specific event type (e.g., `kensinger_bump`, `llm_envelope`)
  - [ ] `--stage <name>` filters to a pipeline stage
  - [ ] `--json` flag emits raw JSONL lines instead of pretty-printed output (grep-friendly)
  - [ ] `--list-sessions` enumerates all sessions under `~/.claude/memesis/traces/` sorted by mtime, shows session_id and event count
  - [ ] Script handles missing trace directory gracefully (creates it on first run, prints "No traces found" if empty)

---

### Task 5.2: Integration tests — replay determinism, cache, guard rejection, eval delta

Deliver integration-level tests that exercise the full evolve workflow end-to-end with mocked LLM responses.

- **Files owned:**
  - `tests/test_evolve_integration.py`
- **Depends on:** Task 3.1, Task 4.1, Task 4.2, Task 5.1
- **Decisions:** D-05, D-06, D-07, D-08, D-12, D-15, D-16
- **Acceptance criteria:**
  - [ ] `TestReplayDeterminism`: two replays of the same transcript with identical mocked LLM responses produce identical `Memory` rows in the replay DB (compare by content_hash)
  - [ ] `TestCacheHitMiss`: first replay calls `call_llm()` N times; second replay of identical transcript calls it 0 times (cache hits); `--live` forces N calls on second replay
  - [ ] `TestMutationGuardRejection`: a mutation that breaks `TestRule3KensingerRemoved` is discarded; the file is reverted; iteration count increments; loop continues
  - [ ] `TestEvalDeltaAccuracy`: given a mocked pipeline that drops one expected observation at stage 1.5 synthesis, the diagnostic delta correctly identifies `stage15_synthesis_end` as the loss point
  - [ ] `TestBudgetHalt`: autoresearch with `token_budget=1` (one token) halts after the first LLM call in the first mutation iteration; working files reverted cleanly
  - [ ] All tests use `tmp_path` for DB isolation and mock `call_llm` at `core.llm.call_llm` or `core.llm_cache.call_llm`; no real API calls

---

## File Ownership Map

| File | Owner |
| ---- | ----- |
| `core/trace.py` | Task 1.1 |
| `tests/test_trace.py` | Task 1.1 |
| `core/eval_compile.py` | Task 1.2 |
| `tests/test_eval_compile.py` | Task 1.2 |
| `core/replay_db.py` | Task 1.3 |
| `core/llm_cache.py` | Task 1.3 |
| `tests/test_replay_db.py` | Task 1.3 |
| `core/llm.py` | Task 2.1 |
| `tests/test_llm.py` | Task 2.1 |
| `core/transcript_ingest.py` | Task 2.2 |
| `core/consolidator.py` | Task 2.3 |
| `core/crystallizer.py` | Task 2.4 |
| `core/issue_cards.py` | Task 2.4 |
| `core/card_validators.py` | Task 2.5 |
| `skills/evolve/SKILL.md` | Task 3.1 |
| `scripts/evolve.py` | Task 3.1, Task 4.2 |
| `tests/test_evolve_skill.py` | Task 3.1 |
| `skills/autoresearch/SKILL.md` | Task 4.1 |
| `core/autoresearch.py` | Task 4.1 |
| `tests/test_autoresearch.py` | Task 4.1 |
| `scripts/autoresearch_config.py` | Task 4.2 |
| `scripts/trace_query.py` | Task 5.1 |
| `tests/test_evolve_integration.py` | Task 5.2 |

---

## Cross-Wave Ownership Handoffs

| File | Wave N Owner | Wave M Owner | Handoff Notes |
| ---- | ------------ | ------------ | ------------- |
| `core/trace.py` | Task 1.1 (creates module, schema, `emit()` API) | Tasks 2.1–2.5 (call `emit()` from instrumentation sites) | Wave 2 tasks must read Task 1.1's final `emit()` signature and event type constants before adding call sites; must not modify `trace.py` itself |
| `core/llm.py` | Task 2.1 (adds trace emission + active writer context var) | Task 1.3 cross-ref: `llm_cache.py` wraps `call_llm()` | `llm_cache.py` (1.3) imports `call_llm` from `llm.py`; Task 2.1's modifications to `llm.py` do not change the `call_llm` signature, so 1.3 is unaffected — but if 2.1 adds a required parameter, 1.3 must update its wrapper call accordingly. Implementers: coordinate on signature before 2.1 modifies |
| `scripts/evolve.py` | Task 3.1 (creates driver script with `--transcript`, `--live` flags and replay/eval orchestration) | Task 4.2 (adds `--autoresearch` wiring and `autoresearch.yaml` write) | Task 4.2 must read Task 3.1's flag-parsing and session-id derivation before adding `--autoresearch` logic; must not rewrite existing flag handling |
| `core/issue_cards.py` | Task 2.4 (adds trace emit calls at card synth boundary) | Task 4.1 (listed as autoresearch mutation surface in D-14) | Autoresearch (4.1) treats `issue_cards.py` as a mutable target; it must read the file as modified by Task 2.4 and ensure mutations preserve the trace emission calls or the guard suite detects their removal |
| `core/consolidator.py` | Task 2.3 (adds trace emit at keep/prune/promote/kensinger sites) | Task 4.1 (listed as autoresearch mutation surface in D-14) | Same pattern as above — autoresearch mutations must not remove Kensinger trace event; `TestRule3KensingerRemoved` guards the prompt side; the trace event guards the code side |

**Handoff protocol:** When a file appears here, the later task's implementer MUST:

1. Read the file as modified by the earlier task (not the original)
2. Build on those changes, not revert them
3. If the earlier task's changes conflict with the later task's needs, escalate to team lead

---

## Decision Traceability

| Decision | Tasks |
| -------- | ----- |
| D-01 (trace storage path `~/.claude/memesis/traces/<session_id>.jsonl`) | Task 1.1, Task 5.1 |
| D-02 (single append-only JSONL per session, one event per line) | Task 1.1, Task 5.1 |
| D-03 (event granularity: stage boundaries, per-decision, validator outcomes, LLM envelopes) | Task 1.1, Task 2.1, Task 2.2, Task 2.3, Task 2.4, Task 2.5 |
| D-04 (retention = last 50 sessions FIFO) | Task 1.1 |
| D-05 (replay traces in same `traces/` dir, tagged with replay session_id) | Task 1.1, Task 2.2, Task 2.3, Task 3.1 |
| D-06 (replay DB = tempfile per replay via `init_db()`, no `:memory:`) | Task 1.3, Task 3.1 |
| D-07 (pipeline invocation = direct Python function calls in-process) | Task 3.1 |
| D-08 (LLM cache by `sha256(model+prompt)` at `~/.claude/memesis/evolve/cache/`) | Task 1.3, Task 2.1, Task 5.2 |
| D-09 (hybrid free-text→LLM spec extraction → deterministic template compiler) | Task 1.2 |
| D-10 (match modes: entity presence, semantic similarity, polarity match, absence) | Task 1.2 |
| D-11 (compiled evals at `eval/recall/<slug>_recall.py`) | Task 1.2, Task 3.1 |
| D-12 (failure semantics: pass/fail per expected memory + structured diagnostic delta) | Task 1.2, Task 3.1, Task 5.2 |
| D-13 (autoresearch = vendored sibling skill at `skills/autoresearch/`) | Task 4.1, Task 4.2 |
| D-14 (mutation surface: `prompts.py`, `issue_cards.py`, `rule_registry.py`, `consolidator.py`, `crystallizer.py`) | Task 4.1, Task 4.2 |
| D-15 (guard set: full unit suite + tier-3 invariant tests + eval/recall regression + manifest round-trip) | Task 4.1, Task 5.2 |
| D-16 (loop budget: max N=10 iterations + token cap, hard halt mid-iteration) | Task 4.1, Task 4.2, Task 5.2 |

**Wave 5 status:** pending
**Wave 4 status:** pending
**Wave 3 status:** pending
**Wave 2 status:** pending
**Wave 1 status:** pending
