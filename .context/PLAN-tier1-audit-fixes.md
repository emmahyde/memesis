# PLAN: tier1-audit-fixes

**Slug:** tier1-audit-fixes
**Source:** `.context/CONTEXT-tier1-audit-fixes.md`
**Builds on:** Tier 0 commits `0a164bd`, `0ddc058`, `e263be4`, `bc2148a`

---

## Cross-Wave Ownership Handoffs

| File | Wave 1 task | Wave 3 task | Handoff note |
|---|---|---|---|
| `core/issue_cards.py` | Task 1.1 — add Rule 0 to `ISSUE_SYNTHESIS_PROMPT` | Task 3.1 — add card→memory field promotion | Wave 3 reads the prompt unchanged; adds `_card_to_memory_fields()` helper below `synthesize_issue_cards`. Do not re-edit the prompt. |
| `tests/test_self_reflection_extraction.py` | — | Task 2.2 (Wave 2) | Tier 0's `TestSelectChunkingRule` lives here. Wave 2 must add new test classes; must not modify or delete existing ones. Run existing tests before adding to confirm baseline. |

---

## Risks

1. **`core/prompts.py` has a 1-line uncommitted edit.** Task 1.2 must read the file first and preserve that edit. If the edit conflicts with the injection point (after line 151), resolve by keeping both changes.

2. **`core/issue_cards.py` is untracked.** Task 1.1 edits it directly. Task 3.1 also edits it. Both tasks must read the file before writing. No clobber between waves (Wave 3 runs after Wave 1 completes).

3. **`core/extraction_affect.py` is untracked.** Not owned by any task in this plan. Do not touch it.

4. **`confirmed_rule_no_action` reads `RULE_OVERRIDES` from `core/rule_registry.py`.** The public API is `RULE_OVERRIDES` (dict) and `resolve_overrides()`. Task 2.2 imports `RULE_OVERRIDES` directly — no new public API needed. Confirmed: registry has key-set API.

5. **`repeated_facts_high` content-hash query.** Wave 2 must hash observation content on-the-fly (`hashlib.md5(content.encode()).hexdigest()`) and query `memories.content_hash` (column already exists — see `core/models.py:77`). No schema change needed in Wave 2. The new Wave 3 columns (`temporal_scope`, etc.) are unrelated.

6. **Wave 2 task 2.1 vs existing `affect_pre_filter` logic.** Reading `transcript_ingest.py` lines 457–482 shows `affect_pre_filter` from `ParameterOverrides` already implements a similar gate. Task 2.1 adds a separate module-constant gate (`PREFILTER_RESEARCH_NEUTRAL`) scoped to `session_type == "research"` only, independent of the override path. These must coexist: the new gate fires before the overrides block.

---

## Wave 1 — Prompt-only edits (parallel-safe)

**Estimated parallelism:** 2 tasks, fully parallel.

---

### Task 1.1 — Rule 0: entity-overlap orphan gate in `ISSUE_SYNTHESIS_PROMPT`

**Summary:** Add a sixth quality rule to `ISSUE_SYNTHESIS_PROMPT` in `core/issue_cards.py` that orphans observations lacking shared named entities with any sibling observation.

**Files owned:**
- `core/issue_cards.py`
- `tests/test_issue_cards.py` (create if missing)

**Depends on:** none

**Decisions:** D-Item6 (synthesis_overgreedy fix; prompt-only; no code change)

**Implementation notes:**
- Insert after existing Rule 5 in `QUALITY RULES` block (line 98 of current file, after `"Do not reformat them."`):
  ```
  6. ENTITY GATE: If an observation does not share at least one named entity
     (person, system, file, concept) with any other observation in the input,
     orphan it rather than forcing it into a card. Prefer zero cards to a card
     with one low-importance observation.
  ```
- No changes to `synthesize_issue_cards()` function body.

**Acceptance criteria:**
- `python3 -m pytest tests/test_issue_cards.py -v` passes.
- `TestRule0EntityGate` class exists with at minimum:
  - `test_solo_observation_becomes_orphan` — single observation with no entity overlap → mock LLM returns it in `orphans[]`, asserts `card_count == 0`.
  - `test_shared_entity_stays_in_card` — two observations sharing one entity → asserts `card_count >= 1`.
  - `test_empty_observations_returns_empty` — regression: `synthesize_issue_cards([])` returns `([], [], {...})` without LLM call.
- Mock `call_llm` via `unittest.mock.patch("core.issue_cards.call_llm", ...)`.
- The Rule 0 text appears verbatim in `ISSUE_SYNTHESIS_PROMPT`.

---

### Task 1.2 — Session-type extraction guidance + skip-friction in `OBSERVATION_EXTRACT_PROMPT`

**Summary:** Inject a session-type branch block into `OBSERVATION_EXTRACT_PROMPT` and add skip-friction requiring the LLM to name one evaluated-and-rejected candidate before skipping.

**Files owned:**
- `core/prompts.py`
- `tests/test_prompts.py` (create if missing; or add to `tests/test_transcript_ingest.py` if `test_prompts.py` doesn't exist — check first)

**Depends on:** none

**Decisions:** D-Item7 (session-type guidance; skip-friction; prompt-only)

**Implementation notes:**
- Read `core/prompts.py` first — verify the uncommitted 1-line edit is identified and preserved.
- Injection point: after the `work_event` block (currently ends around line 154 with `"Set work_event=null when session_type != 'code'..."`), before `subtitle —` line. Add:
  ```
  SESSION-TYPE EXTRACTION GUIDANCE:
  - research: Target conceptual/metacognitive findings and insight transitions.
    Skip tool-call logs and execution traces. Force work_event=null.
  - writing: Target authoring decisions and aesthetic choices.
    Skip mechanical edits (find-replace, formatting). Force work_event=null.
  - code: Current behavior (bugfix|feature|refactor|discovery|change allowed).
  ```
- Skip-friction: locate the SKIP_PROTOCOL section (search for `"If the buffer has nothing worth processing"`). Before that sentence, add:
  ```
  SKIP DISCIPLINE: Before deciding to skip a window, name one specific
  observation you evaluated and rejected (with your reason). A skip without
  a named candidate is a refusal to engage, not a judgment.
  ```
- `OBSERVATION_EXTRACT_PROMPT` is a module-level string — the injections extend it; no format-string keys are added.

**Acceptance criteria:**
- `python3 -m pytest tests/test_prompts.py -v` (or `tests/test_transcript_ingest.py`) passes.
- `TestSessionTypeGuidance` class with:
  - `test_research_guidance_present` — asserts `"research"` and `"work_event=null"` appear in `OBSERVATION_EXTRACT_PROMPT`.
  - `test_writing_guidance_present` — asserts `"writing"` and `"aesthetic choices"` appear.
  - `test_skip_friction_present` — asserts `"name one specific"` (or equivalent phrase) appears before the skip sentinel.
- The pre-existing 1-line edit in the working tree is present in the final file (verify by reading before writing).
- `OBSERVATION_EXTRACT_PROMPT` remains a valid format string (no new `{...}` keys introduced).

---

## Wave 2 — Logic additions (parallel-safe)

**Estimated parallelism:** 2 tasks, fully parallel. Neither touches Wave 1 files.

---

### Task 2.1 — Pre-filter low-affect research windows

**Summary:** Add a module-constant gate in `extract_observations_hierarchical` that skips LLM calls on research windows with zero affect signal, logging each skip as `pre_filtered_low_affect`.

**Files owned:**
- `core/transcript_ingest.py`
- `tests/test_transcript_ingest.py`

**Depends on:** Wave 1 (no file overlap, but logically independent — can start in parallel with Wave 2.2)

**Decisions:** D-Item8 (pre-filter gate; module constant; report key; reversible)

**Implementation notes:**
- Add at module level (after imports, before `logger =`):
  ```python
  PREFILTER_RESEARCH_NEUTRAL: bool = True
  ```
- Insertion point in `extract_observations_hierarchical`: between affect aggregation (line ~438, after `affect_signals = [...]`) and the prompts list comprehension (line ~448). The new gate must run **before** the `overrides.affect_pre_filter` block (lines 459–482), because it is an independent, session-type-scoped filter:
  ```python
  if PREFILTER_RESEARCH_NEUTRAL and session_type == "research":
      prefiltered: list[int] = [
          i for i, a in enumerate(affect_signals) if a.max_boost == 0.0
      ]
      for i in prefiltered:
          skips.append({
              "window_index": i,
              "outcome": "pre_filtered_low_affect",
              "affect_intensity": affect_signals[i].max_boost,
              "affect_valence": affect_signals[i].valence,
          })
      windows = [w for i, w in enumerate(windows) if i not in set(prefiltered)]
      affect_signals = [a for i, a in enumerate(affect_signals) if i not in set(prefiltered)]
  ```
- Add `prefilter_skipped_count` to the return dict: `"prefilter_skipped_count": len(prefiltered)` (or 0 for non-research sessions). Also add it to the `ExtractionRunStats` instantiation site (`scripts/run_selected_sessions.py` or wherever stats are built — check call sites, add field with `default=0` if not already present).

**Acceptance criteria:**
- `python3 -m pytest tests/test_transcript_ingest.py -v` passes.
- `TestPrefilterResearchNeutral` class with:
  - `test_research_zero_affect_skipped` — research session, all windows `max_boost=0.0` → `call_llm_batch` not called for those windows; skips contain `outcome="pre_filtered_low_affect"`.
  - `test_research_nonzero_affect_not_skipped` — research session, one window `max_boost=0.3` → that window passes to LLM.
  - `test_code_session_not_filtered` — code session, `max_boost=0.0` windows → filter does not fire.
  - `test_constant_false_disables_gate` — set `transcript_ingest.PREFILTER_RESEARCH_NEUTRAL = False`, assert no skip records of type `pre_filtered_low_affect`.
  - `test_prefilter_skipped_count_in_return` — asserts `"prefilter_skipped_count"` key present in return dict.
- Mock `call_llm_batch` to assert call count matches only non-filtered windows.

---

### Task 2.2 — Three new self-reflection rules

**Summary:** Register `low_obs_yield_per_call`, `repeated_facts_high`, and `confirmed_rule_no_action` rules in `core/self_reflection_extraction.py`; add `obs_per_cost_call` property to `ExtractionRunStats`.

**Files owned:**
- `core/self_reflection_extraction.py`
- `tests/test_self_reflection_extraction.py`

**Depends on:** none (reads `RULE_OVERRIDES` from `core/rule_registry.py` via import; that module is stable from `bc2148a`)

**Decisions:** D-Item10 (three new rules; `ExtractionRunStats` extension; `MemoryStore` read-only)

**Implementation notes:**

`ExtractionRunStats` — add property (no new field needed; computed from existing fields):
```python
@property
def obs_per_cost_call(self) -> float:
    if self.cost_calls == 0:
        return 0.0
    return self.raw_observations / self.cost_calls
```

Rule `low_obs_yield_per_call`:
- Fires when `stats.obs_per_cost_call < 2.0 AND stats.cost_calls >= 8`.
- importance=0.65, kind="finding", proposed_action: "Reduce max_windows or enable affect_pre_filter to improve raw observation yield per LLM call."

Rule `repeated_facts_high`:
- Signature: `(stats: ExtractionRunStats, *, memory_store_path: Path | None = None)` — but the `@_rule` decorator passes only `stats`. To allow DB access, the rule must accept an optional kwarg. The existing `_RULES` list stores callables called as `rule(stats)`. Options:
  - A: Make `repeated_facts_high` a closure that captures a `MemoryStore` path at registration time — but path isn't known at import time.
  - B: Use `functools.partial` with a default `root=None` and call `Memory.select()` only when a DB connection is available, guarded by try/except.
  - **Use option B.** The rule calls `init_db` / checks if db is bound, then queries `SELECT content_hash FROM memories WHERE content_hash IS NOT NULL`. It hashes each fact in `stats` (but `ExtractionRunStats` doesn't hold raw facts). **Correct approach:** The rule receives stats only. Add a `repeated_fact_hashes: list[str]` field to `ExtractionRunStats` (default empty list) populated by the caller (`extract_observations_hierarchical`) after dedup, containing MD5 hashes of all raw observation facts. The rule then checks those hashes against the DB.
  - Add `repeated_fact_hashes: list[str] = field(default_factory=list)` to `ExtractionRunStats`.
  - Rule fires when `≥3` hashes from `stats.repeated_fact_hashes` exist in `memories.content_hash`. Uses `from core.models import Memory, db` — guarded with try/except so a missing DB doesn't crash the rule.
  - importance=0.7, kind="finding", proposed_action: "Cross-session re-extraction detected; consider deduplication at consolidation time or raising importance_gate."

Rule `confirmed_rule_no_action`:
- Import: `from core.rule_registry import RULE_OVERRIDES`.
- Fires when `aggregate_audit()` returns any rule with `fire_count >= 5 AND proposed_action non-empty AND rule_id NOT in RULE_OVERRIDES`.
- Facts: names which rule_id qualifies.
- importance=0.75, kind="open_question", proposed_action: "Wire a parameter override for {rule_id} in core/rule_registry.py RULE_OVERRIDES."
- The rule takes `stats` but also calls `aggregate_audit(root=None)` internally. This is acceptable — `aggregate_audit` is cheap (reads a JSONL).

**`to_dict()` update:** Add `"obs_per_cost_call": self.obs_per_cost_call` and `"repeated_fact_hashes": self.repeated_fact_hashes` to `ExtractionRunStats.to_dict()`.

**Acceptance criteria:**
- `python3 -m pytest tests/test_self_reflection_extraction.py -v` passes.
- Existing `TestSelectChunkingRule` and all Tier 0 tests pass without modification.
- `TestLowObsYieldRule` class:
  - `test_fires_when_yield_below_threshold` — `cost_calls=10, raw_observations=15` → fires.
  - `test_no_fire_when_yield_acceptable` — `cost_calls=10, raw_observations=25` → None.
  - `test_no_fire_when_cost_calls_low` — `cost_calls=4, raw_observations=5` → None (guard).
- `TestRepeatedFactsHighRule`:
  - `test_fires_when_three_hash_collisions` — seed DB with memories whose `content_hash` matches 3 entries in `stats.repeated_fact_hashes` → fires.
  - `test_no_fire_when_fewer_than_three` — 2 matches → None.
  - `test_graceful_when_db_unavailable` — DB not initialized → returns None (no exception).
- `TestConfirmedRuleNoAction`:
  - `test_fires_for_unwired_confirmed_rule` — mock `aggregate_audit` to return a rule with `fire_count=6`, `proposed_action="do X"`, `rule_id="unknown_rule"` not in `RULE_OVERRIDES` → fires.
  - `test_no_fire_when_rule_is_wired` — `rule_id="low_productive_rate"` (in `RULE_OVERRIDES`) → None.
  - `test_no_fire_when_fire_count_below_threshold` — `fire_count=4` → None.
- `list_rules()` returns all three new rule_ids.

---

## Wave 3 — Schema promotion (sequential, single agent)

**Estimated parallelism:** 1 task. Migration ordering requires strict sequencing within the task: `database.py` → `models.py` → `consolidator.py` → `issue_cards.py`.

---

### Task 3.1 — Schema migration + Memory dataclass + consolidator + card→memory mapping

**Summary:** Add four nullable columns to `memories`, update `Memory` model and round-trip, wire card→memory field promotion in `consolidator.py`, and add the card-field extractor helper to `core/issue_cards.py`.

**Files owned:**
- `core/database.py`
- `core/models.py`
- `core/consolidator.py`
- `core/issue_cards.py`
- `tests/test_storage.py`
- `tests/test_consolidator.py`

**Depends on:** Wave 1 Task 1.1 (issue_cards.py prompt must be complete before this task edits the same file for card→memory wiring)

**Decisions:** D-Item9 (schema; all nullable; idempotent ALTER; schema version bump; no retro backfill; regex actor extraction)

**Implementation notes, in execution order:**

**Step 1 — `core/database.py`:**
- Add four columns to the `memories` migration loop in `_run_migrations()` (after `is_pinned` entry):
  ```python
  ("temporal_scope", "TEXT"),
  ("confidence", "REAL"),
  ("affect_valence", "TEXT"),
  ("actor", "TEXT"),
  ```
  Existing `try/except` pattern handles idempotency.
- Add a schema version bump: add a `schema_versions` table if not present, or use a `user_version` PRAGMA. Recommended: `db.execute_sql("PRAGMA user_version")` → if less than target, run migrations, then `db.execute_sql(f"PRAGMA user_version = {TARGET_VERSION}")`. Set `TARGET_VERSION = 2` (or increment from current). Add `_SCHEMA_VERSION = 2` constant.

**Step 2 — `core/models.py`:**
- Add four fields to `Memory` class after `is_pinned`:
  ```python
  temporal_scope = TextField(null=True)   # "session-local" | "cross-session-durable"
  confidence = FloatField(null=True)      # 0.0–1.0; default 0.7 at write time
  affect_valence = TextField(null=True)   # friction|delight|surprise|neutral|mixed
  actor = TextField(null=True)            # best-effort attributed entity
  ```
- No changes to `save()`, `_fts_insert`, or `_fts_delete_from_db` — new fields are not FTS-indexed.

**Step 3 — `core/issue_cards.py`:**
- Read file first — Wave 1 Task 1.1 changes must be present.
- Add helper function after `synthesize_issue_cards`:
  ```python
  _ACTOR_RE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b')

  def extract_card_memory_fields(card: dict) -> dict:
      """Map issue card fields to Memory column values for Wave 3 schema promotion.

      Returns a dict with keys: temporal_scope, confidence, affect_valence, actor.
      All values may be None (Memory columns are nullable).
      """
      temporal_scope = card.get("scope")  # "session-local" | "cross-session-durable"
      confidence_raw = card.get("knowledge_type_confidence")
      confidence = 0.9 if confidence_raw == "high" else 0.7 if confidence_raw == "low" else 0.7
      affect_valence = card.get("user_affect_valence")
      actor = None
      for fact in card.get("evidence_quotes") or []:
          m = _ACTOR_RE.search(fact)
          if m:
              actor = m.group(0)
              break
      return {
          "temporal_scope": temporal_scope,
          "confidence": confidence,
          "affect_valence": affect_valence,
          "actor": actor,
      }
  ```
- Add `import re` at top of file if not present.

**Step 4 — `core/consolidator.py`:**
- Locate the `keep` decision write path — where `Memory.create(...)` is called for kept observations (search for `action == "keep"` block, around the `lifecycle.store_memory` or direct `Memory.create` call).
- When creating a memory from a card (detected by presence of `"scope"` or `"evidence_quotes"` keys in the decision dict, or via a `is_card=True` flag), call `extract_card_memory_fields(decision)` and pass the four fields to `Memory.create(...)`.
- For observations that are not cards (flat observations from old path), default all four fields to `None`.
- Import: `from .issue_cards import extract_card_memory_fields`.

**Acceptance criteria:**
- `python3 -m pytest tests/test_storage.py tests/test_consolidator.py tests/test_issue_cards.py -v` passes.
- `TestSchemaMigrationIdempotency` in `tests/test_storage.py`:
  - `test_migration_idempotent` — call `init_db(base_dir=...)` twice against the same path; second call must not raise. Query `PRAGMA table_info(memories)` and assert all four new columns present.
  - `test_new_columns_nullable` — create a `Memory` without the new fields; assert it saves and reloads without error; new fields are `None`.
- `TestCardToMemoryPromotion` in `tests/test_consolidator.py`:
  - `test_temporal_scope_promoted` — consolidation decision with `scope="cross-session-durable"` → `Memory.temporal_scope == "cross-session-durable"`.
  - `test_confidence_promoted_high` — `knowledge_type_confidence="high"` → `Memory.confidence == 0.9`.
  - `test_affect_valence_promoted` — `user_affect_valence="friction"` → `Memory.affect_valence == "friction"`.
  - `test_actor_extracted_from_quote` — `evidence_quotes=["Emma approved the design"]` → `Memory.actor == "Emma"` (or the first regex match).
  - `test_actor_null_when_no_quote` — empty `evidence_quotes` → `Memory.actor is None`.
- `TestExtractCardMemoryFields` in `tests/test_issue_cards.py`:
  - `test_scope_mapping` — asserts `temporal_scope` roundtrips correctly.
  - `test_confidence_default` — `None` confidence_raw → 0.7.
  - `test_actor_regex` — various quote strings → expected actor string.
- Full suite: `python3 -m pytest tests/ -v` — zero regressions (run last).

---

## Summary

| Wave | Tasks | Parallelism | Gate |
|---|---|---|---|
| 1 | 1.1, 1.2 | 2 parallel | none |
| 2 | 2.1, 2.2 | 2 parallel | Wave 1 complete (files disjoint, but 2.1 + 2.2 can start immediately after Wave 1) |
| 3 | 3.1 | 1 sequential | Wave 1 complete (3.1 edits `issue_cards.py` after 1.1) |

Wave 2 and Wave 3 can start simultaneously after Wave 1 finishes — they own disjoint files. Wave 3 just cannot start before Wave 1 Task 1.1 commits its `issue_cards.py` changes.
