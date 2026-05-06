# PLAN: tier2-audit-fixes

Source: `.context/CONTEXT-tier2-audit-fixes.md`
Status: Ready for implementation

---

## Risks

**Pre-existing untracked files — do NOT commit:**
- `core/card_validators.py`, `core/extraction_affect.py`
- `claude-mem/`, `memvid/`, `.headroom/`, `.sisyphus/`, `graphify-out/`

**Pre-existing modified tracked files — preserve, do not revert:**
- `core/llm.py`, `core/transcript_ingest.py`, `scripts/run_selected_sessions.py`
(verify with `git diff --stat` before committing any wave)

**Registry has `cards_unused_high_importance` override pre-wired** (line 140 of `core/rule_registry.py`) — Wave 3 Task 3.1 adds the *rule* in `self_reflection_extraction.py` that emits the observation; the override already exists and will activate once the rule fires. No registry edit needed in Wave 3.

**`core/embeddings.py` uses Bedrock Titan, not sentence-transformers.** Reframe A (Task 4.1) must use `embed_text()` from `core.embeddings` (Bedrock) — NOT sentence-transformers. The CONTEXT doc says "sentence-transformers" but the existing infra is Bedrock Titan. Use `core.embeddings.embed_text()`. Bedrock not mocked at boto3 in tests — Task 4.1 must mock at `core.embeddings.embed_text` level.

**sqlite-vec apsw connections have 0ms busy timeout** (known issue, CONCERNS.md). Task 4.1's in-session vec table inherits this. Acceptable — in-session table is single-writer. Document but do not fix.

**`_run_migrations()` is not atomic** (known issue). New migrations from Wave 3 and Wave 4 follow the existing try/except ALTER TABLE pattern — same risk level, not new.

**Wave 3 and Wave 4 both touch `core/database.py`.** Wave 3 must land first. Wave 4 implementer reads the post-Wave-3 state.

---

## Cross-Wave Ownership Handoffs

| File | Wave 1 Task | Wave 3 Task | Wave 4 Task | Notes |
|------|-------------|-------------|-------------|-------|
| `core/self_reflection_extraction.py` | 1.1 — add 4 stats fields + 3 rules (additive only) | 3.1 — add `cards_unused_high_importance` rule; remove `issue_card_collapse_efficient` | — | Wave 3 builds on Wave 1 state. No removals in Wave 1. |
| `core/database.py` | — | 3.1 — add `RetrievalLog` index on `(memory_id, session_id, was_used)` | 4.1 — add in-session vec table migration | Wave 3 first; Wave 4 reads post-Wave-3 state. Both use try/except ALTER TABLE pattern. |
| `tests/test_self_reflection_extraction.py` | 1.1 — extend for new stats fields + 3 rules | 3.1 — extend for removed rule + new rule | — | Wave 3 implementer reads Wave 1's additions; must not revert them. |

---

## Wave 1 — Stats + Synthesis Enhancements (parallel)

**Estimated parallelism: 2 agents**

### Task 1.1 — ExtractionRunStats fields + 3 new rules

**Summary:** Add four new fields to `ExtractionRunStats` and three new reflection rules driven by those fields.

**Files owned:**
- `core/self_reflection_extraction.py` (additive only — no removals; Wave 3 owns removals)
- `tests/test_self_reflection_extraction.py`

**Depends on:** none

**Decisions:** Items 14 from CONTEXT.

**Implementation notes:**

Add to `ExtractionRunStats` dataclass (after existing fields):
```python
unique_knowledge_types_emitted: int = 0
repeated_facts_count: int = 0          # fuzzy Jaccard ≥0.55 vs memory store
windows_with_affect_signal_but_no_card: int = 0
min_card_importance: float = 1.0
```

Add to `to_dict()` for all four.

`repeated_facts_count` is populated by caller (transcript_ingest) — this task only adds the field and rule. Caller wiring is out of scope for this task. Rule fires on the count value; zero is fine when caller hasn't wired it yet.

Three new rules (register via `@_rule`):

```python
@_rule("monotone_knowledge_lens")
def _rule_monotone_knowledge_lens(stats):
    if stats.unique_knowledge_types_emitted != 1:
        return None
    if stats.final_observations < 5:
        return None
    # ... SelfObservation(importance=0.6, kind="finding")

@_rule("affect_signal_no_extraction")
def _rule_affect_signal_no_extraction(stats):
    if stats.windows_with_affect_signal_but_no_card < 3:
        return None
    # ... SelfObservation(importance=0.7, kind="finding")

@_rule("forced_clustering_low_importance")
def _rule_forced_clustering_low_importance(stats):
    # Requires synthesis_overgreedy to be confirmed via aggregate_audit
    audit = aggregate_audit(root=None)
    if (audit.get("synthesis_overgreedy") or {}).get("confidence") != "confirmed":
        return None
    if stats.min_card_importance >= 0.4:
        return None
    # ... SelfObservation(importance=0.7, kind="finding")
```

**Acceptance criteria:**
- `list_rules()` includes `monotone_knowledge_lens`, `affect_signal_no_extraction`, `forced_clustering_low_importance`
- `ExtractionRunStats(session_id="x", ...).to_dict()` has all four new keys
- `python3 -m pytest tests/test_self_reflection_extraction.py -v` zero failures
- Tests cover: field present in `to_dict()`, each rule fires on threshold-crossing input, each rule silent on sub-threshold input
- No existing rules removed (verify `issue_card_collapse_efficient` still in `list_rules()`)

---

### Task 1.2 — Issue card validation + Rule 10 + mixed valence

**Summary:** Add `evidence_obs_indices` validation, drop-gate Rule 10 + `dropped_weak_observations` stat, and mixed-valence prompt instruction to `synthesize_issue_cards()`.

**Files owned:**
- `core/issue_cards.py`
- `tests/test_issue_cards.py`

**Depends on:** none

**Decisions:** Items 15, 16, 17 from CONTEXT.

**Implementation notes:**

**Item 15 — evidence_obs_indices validation:**
After parsing `cards` from LLM JSON, before the `valid_cards` filter, add:
```python
n_obs = len(observations)
for card in cards:
    indices = card.get("evidence_obs_indices") or []
    valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < n_obs]
    dropped = len(indices) - len(valid_indices)
    if dropped:
        logger.info("issue_synthesis: dropped %d out-of-range indices in card '%s'",
                    dropped, card.get("title", "?"))
    card["evidence_obs_indices"] = valid_indices
```
Accumulate `dropped_invalid_indices` count in stats dict.

**Item 16 — Rule 10 in prompt + `dropped_weak_observations` stat:**
Add after Rule 9 in `ISSUE_SYNTHESIS_PROMPT`:
```
10. DROP GATE: An observation with importance < 0.3 sharing no named entity
    with any sibling MAY be dropped entirely (omit from both `issue_cards[]`
    and `orphans[]`). Use sparingly — preserves orphan signal but reduces
    noise floor.
```
Add `dropped_weak_observations` to stats output. This is a prompt-instruction stat — actual tracking requires LLM cooperation; initialize to 0 from the parsed `synthesis_notes` field or leave at 0 for now (LLM doesn't return this count). Document this in code comment.

**Item 17 — mixed valence instruction:**
In the prompt, update the `user_affect_valence` description line:
```
"user_affect_valence": "friction|delight|surprise|neutral|mixed",
```
Add inline after: `// Use 'mixed' when the user's reaction evolved across the card's span (e.g., initial friction then accept). Track the trajectory in 'user_reaction' text.`

**Acceptance criteria:**
- `python3 -m pytest tests/test_issue_cards.py -v` zero failures
- Test: card with `evidence_obs_indices: [999]` when only 5 obs → index dropped, card survives if it has evidence_quotes
- Test: `stats["dropped_invalid_indices"]` present in returned stats dict
- Test: `ISSUE_SYNTHESIS_PROMPT` contains Rule 10 text verbatim (`DROP GATE`)
- Test: `ISSUE_SYNTHESIS_PROMPT` contains `mixed` with trajectory guidance
- Regression: existing synthesis behavior unchanged for valid inputs

---

## Wave 2 — New Scripts + Registry Generalization (parallel)

**Estimated parallelism: 2 agents**

### Task 2.1 — End-to-end lifecycle audit script

**Summary:** New `scripts/audit_lifecycle.py` that joins Observation + Memory + consolidation_log to produce per-session stage-transition counts and surface stuck-pending observations.

**Files owned:**
- `scripts/audit_lifecycle.py` (new)
- `tests/test_audit_lifecycle.py` (new)
- `scripts/_audit_render.py` (new, optional — only if render helpers extractable cleanly from `audit_pipeline_dimensions.py`)

**Read-only access (no writes):**
- `core/lifecycle.py`, `core/database.py`, `core/consolidator.py`, `core/models.py`

**Depends on:** none

**Decisions:** Item 11 from CONTEXT.

**Implementation notes:**

Script entry point:
```
python3 scripts/audit_lifecycle.py --base-dir ~/.claude/memory --out /tmp/lifecycle_audit.md
```

Logic:
1. `init_db(base_dir=args.base_dir)`
2. Query `Observation` table grouped by `source_session_id`, count by `status`
3. Join to `Memory` via `source_session_id` to get `stage` distribution per session
4. Walk `consolidation_log` for `session_id` × `action` counts
5. Per-session row: `ephemeral_count | consolidated_count | crystallized_count | instinctive_count | pending_count`
6. Stuck-pending alert: `status='pending'` and `created_at < now - 7days` → emit `consolidation_stuck` warning block
7. Output Markdown mirroring `audit_pipeline_dimensions.py` style (section headers, bullet rows)

If `scripts/_audit_render.py` is created, it extracts `fmt_card`/`fmt_obs` from `audit_pipeline_dimensions.py` — but only do this if it's a clean extraction with no behavior change to the existing script.

**Acceptance criteria:**
- `python3 scripts/audit_lifecycle.py --base-dir ~/.claude/memory --out /tmp/lifecycle_audit.md` runs end-to-end against a real or test DB without error
- Output markdown has per-session stage counts section
- Stuck-pending section present (even if empty)
- `python3 -m pytest tests/test_audit_lifecycle.py -v` zero failures
- Tests use `tmp_path` fixture + `init_db`; no real `~/.claude/memory` access

---

### Task 2.2 — Registry generalization + CLI status

**Summary:** Audit existing `RULE_OVERRIDES` for unmapped confirmed rules; add any missing knobs; add `scripts/registry_status.py` CLI showing active vs dormant rules with fire counts.

**Files owned:**
- `core/rule_registry.py`
- `scripts/registry_status.py` (new)
- `tests/test_rule_registry.py` (new — check `tests/` first; if file exists, extend it)

**Depends on:** none

**Decisions:** Item 12 from CONTEXT.

**Implementation notes:**

Audit current `RULE_OVERRIDES`:
- `chunking_suboptimal` ✓ knob: `chunking_strategy`
- `low_productive_rate` ✓ knobs: `max_windows`, `affect_pre_filter`
- `affect_blind_spot` ✓ (informational, remediation already active)
- `dedup_inert` ✓ (stub, documented)
- `synthesis_overgreedy` ✓ knob: `synthesis_strict`
- `parse_errors_present` ✓ knob: `max_tokens_stage1`
- `cards_unused_high_importance` ✓ knob: `importance_gate`

New rules from Wave 1 Task 1.1 that need registry entries:
- `monotone_knowledge_lens` — informational only; add stub override with note
- `affect_signal_no_extraction` — informational; add stub override with note
- `forced_clustering_low_importance` — informational; add stub override with note

New rules from Wave 3 Task 3.1 (`cards_unused_high_importance`) — already wired. Skip.

Add stub overrides for the three new Wave 1 rules. These are informational rules — the overrides document the intent but take no parameter action yet.

`scripts/registry_status.py`:
```
python3 scripts/registry_status.py [--root PATH]
```
Output table:
```
| rule_id | fire_count | confidence | has_override | knobs_affected |
```
- `fire_count` from `aggregate_audit()`
- `has_override` = rule_id in RULE_OVERRIDES
- `knobs_affected` = inspect override fn docstring or a static metadata dict

Add a `RULE_METADATA` dict in `rule_registry.py` mapping rule_id → `{"knobs": [...], "note": "..."}` for the status CLI to read without inspecting docstrings.

**Acceptance criteria:**
- `python3 scripts/registry_status.py` prints table without error (empty audit = all zeros)
- All rules from `list_rules()` (self_reflection_extraction) appear in output, even without overrides (dormant column)
- `RULE_OVERRIDES` has entries for `monotone_knowledge_lens`, `affect_signal_no_extraction`, `forced_clustering_low_importance` (stub or real)
- `python3 -m pytest tests/test_rule_registry.py -v` zero failures
- Tests: `resolve_overrides({})` returns defaults; confirmed `synthesis_overgreedy` sets `synthesis_strict=True`

---

## Wave 3 — Downstream-Utility Metric (sequential single-agent)

**Estimated parallelism: 1 agent**

**Depends on:** Wave 1 (Task 1.1 state of `core/self_reflection_extraction.py` and `tests/test_self_reflection_extraction.py`)

### Task 3.1 — cards_unused_high_importance + remove issue_card_collapse_efficient

**Summary:** Add cross-session retrieval join in `FeedbackLoop`; new rule `cards_unused_high_importance`; remove `issue_card_collapse_efficient` rule; add `RetrievalLog` index if missing.

**Files owned:**
- `core/feedback.py`
- `core/self_reflection_extraction.py` (new rule + rule removal — builds on Wave 1 state)
- `core/database.py` (index migration if `(memory_id, session_id, was_used)` index absent)
- `tests/test_feedback.py`
- `tests/test_self_reflection_extraction.py` (extend Wave 1's additions)

**Depends on:** Wave 1 Task 1.1 (file ownership of `core/self_reflection_extraction.py` transfers here)

**Decisions:** Item 13 from CONTEXT.

**Implementation notes:**

**DB migration (in `_run_migrations()`):**
```python
# retrieval_log index for cards_unused_high_importance join
try:
    db.execute_sql(
        "CREATE INDEX IF NOT EXISTS idx_retrieval_log_memory_session "
        "ON retrieval_log(memory_id, session_id, was_used)"
    )
except Exception:
    pass
```

**FeedbackLoop addition — `cards_unused_in_window()`:**
```python
def cards_unused_in_window(
    self,
    memory_ids: list[str],
    after_session_id: str,
    window: int = 10,
) -> list[str]:
    """Return memory_ids from list that have 0 retrieval hits in next `window` sessions.
    
    Joins retrieval_log on memory_id; filters to sessions after after_session_id.
    Returns IDs with no 'was_used=True' record in window.
    """
    # Implementation: query RetrievalLog.select() filtered by memory_id IN list
    # and session_id > after_session_id, was_used=True, LIMIT window sessions.
    # Returns IDs with no match.
```

**New rule in `self_reflection_extraction.py`:**
```python
@_rule("cards_unused_high_importance")
def _rule_cards_unused_high_importance(stats: ExtractionRunStats) -> SelfObservation | None:
    """≥3 high-importance cards from this session never retrieved in next 10 sessions."""
    # Requires DB connection; guard same as repeated_facts_high rule.
    # Query: Memory WHERE source_session_id=stats.session_id AND importance >= 0.8
    # Then FeedbackLoop.cards_unused_in_window() or direct RetrievalLog query.
    # Fire condition: len(unused_high_imp) >= 3
    # importance=0.7, kind="finding"
```

**Remove `issue_card_collapse_efficient` rule:**
- Delete the `@_rule("issue_card_collapse_efficient")` function entirely
- Verify `list_rules()` no longer contains it

**Acceptance criteria:**
- `python3 -m pytest tests/test_feedback.py tests/test_self_reflection_extraction.py -v` zero failures
- `list_rules()` contains `cards_unused_high_importance`; does NOT contain `issue_card_collapse_efficient`
- `cards_unused_high_importance` appears in `aggregate_audit()` output structure after firing
- DB index `idx_retrieval_log_memory_session` present after `init_db()` on fresh DB
- Test: `cards_unused_in_window()` returns correct IDs given seeded retrieval_log fixture
- Test: rule fires when ≥3 memories with importance ≥0.8 have no retrieval hits; silent otherwise
- Wave 1 test additions still pass (no regressions from file re-edit)

---

## Wave 4 — Reframe A: Stateful Incremental Extraction (sequential single-agent)

**Estimated parallelism: 1 agent**

**Depends on:** Wave 3 (post-Wave-3 state of `core/database.py`)

### Task 4.1 — In-session vec table + prior-extraction injection

**Summary:** Add stateful in-session vector index to `extract_observations_hierarchical`; inject top-3 similar prior observations into each window prompt; add `cross_window_dedup_hits` stat; gate behind `REFRAME_A_ENABLED` flag.

**Files owned:**
- `core/transcript_ingest.py`
- `core/session_vec.py` (new — in-session ephemeral vec helper)
- `core/prompts.py` (add `{prior_extractions}` placeholder to `OBSERVATION_EXTRACT_PROMPT`)
- `tests/test_transcript_ingest.py`
- `tests/test_session_vec.py` (new)

**Depends on:** Wave 3 Task 3.1 (for `core/database.py` state; no actual dependency on Wave 3 logic, only file ordering)

**Decisions:** Item 18 from CONTEXT. `REFRAME_A_ENABLED` default False. Refine pass (`REFINE_PASS_ENABLED`) stays True.

**Implementation notes:**

**`core/session_vec.py` — in-session ephemeral vec store:**

In-session SQLite vec table schema (separate DB file, not `index.db`):
```python
# Table DDL (created per-session, in a temp file or in index.db as a session-scoped table)
CREATE VIRTUAL TABLE IF NOT EXISTS vec_session_{session_id_slug} USING vec0(
    obs_idx INTEGER PRIMARY KEY,
    embedding float[512]
)
```

Use a separate apsw connection to `index.db` (same DB, session-namespaced table). Drop table after consolidation.

```python
class SessionVecStore:
    """In-session ephemeral vector index for cross-window dedup.
    
    Scoped to one session_id. Created on first window, dropped after session completes.
    Uses same index.db as VecStore but a different table.
    """
    
    def __init__(self, db_path: Path, session_id: str):
        self._db_path = str(db_path)
        self._table = f"vec_session_{_slug(session_id)}"
        self._available = False
        self._create_table()
    
    def add(self, obs_idx: int, text: str) -> None:
        """Embed text and store at obs_idx."""
        
    def query_similar(self, text: str, k: int = 3) -> list[str]:
        """Return top-k stored observation texts by cosine similarity."""
    
    def drop(self) -> None:
        """Drop the session-scoped table."""
```

Embedding: `from core.embeddings import embed_text`. Guard: `if embedding is None: skip store, skip query`.

**`core/prompts.py` — placeholder addition:**
Add `{prior_extractions}` placeholder to `OBSERVATION_EXTRACT_PROMPT`. When empty string, renders as nothing. When populated: block header `PRIOR EXTRACTIONS (do not duplicate):` followed by bullet list of top-3 prior obs texts.

Exact injection point in prompt: insert after the window text block, before the output schema. Example:
```
{prior_extractions}
```
Where `prior_extractions` is either `""` or:
```
PRIOR EXTRACTIONS (do not duplicate — these facts have already been captured from earlier windows in this session):
- <obs text 1>
- <obs text 2>
- <obs text 3>
```

**`core/transcript_ingest.py` — stateful extraction loop:**

Add module-level flag:
```python
REFRAME_A_ENABLED: bool = False
```

In `extract_observations_hierarchical()` (or equivalent window loop):
```python
if REFRAME_A_ENABLED and get_db_path():
    svec = SessionVecStore(get_db_path(), session_id)
else:
    svec = None

cross_window_dedup_hits = 0

for window_idx, window in enumerate(windows):
    prior_block = ""
    if svec:
        prior_obs = svec.query_similar(window_text, k=3)
        if prior_obs:
            prior_block = "PRIOR EXTRACTIONS (do not duplicate ...):\n" + \
                          "\n".join(f"- {t}" for t in prior_obs)
    
    prompt = OBSERVATION_EXTRACT_PROMPT.format(..., prior_extractions=prior_block)
    # ... LLM call ...
    
    if svec:
        for obs in new_obs:
            svec.add(window_obs_count, obs_text)
        # cross_window_dedup_hits: compare new_obs count vs raw LLM output count
        # if prior_block was non-empty and new_obs < expected, increment
        cross_window_dedup_hits += max(0, raw_count - len(new_obs))

if svec:
    svec.drop()
```

Add `cross_window_dedup_hits` to `ExtractionRunStats` (additive field; Task 1.1 owns the dataclass but this field is new — coordinate: Task 4.1 adds it to the dataclass directly since Wave 1 has shipped by Wave 4). Add to `to_dict()`.

**Acceptance criteria:**
- `REFRAME_A_ENABLED=False` (default): behavior identical to pre-Wave-4 — all existing tests pass
- `REFRAME_A_ENABLED=True` manually in test: `cross_window_dedup_hits >= 0` (no crash)
- `python3 -m pytest tests/test_transcript_ingest.py tests/test_session_vec.py -v` zero failures
- `SessionVecStore.drop()` removes the table; confirmed by attempting query after drop → error
- `OBSERVATION_EXTRACT_PROMPT` contains `{prior_extractions}` placeholder
- Tests mock `core.embeddings.embed_text` — no real Bedrock calls
- `REFINE_PASS_ENABLED` flag added to `transcript_ingest.py` (default `True`) — refine pass coexistence test passes
- `python3 -m pytest tests/ -v` zero regressions across all prior waves

---

## Wave 5 — End-to-End Verification (orchestrator, not implementer)

**Estimated parallelism: 1 (orchestrator-level verification)**

**Depends on:** all prior waves complete

**Steps (not an implementer task — run by the person coordinating the tier):**

1. `python3 -m pytest tests/ -v` — zero regressions
2. `python3 scripts/audit_lifecycle.py --base-dir ~/.claude/memory --out /tmp/lifecycle_audit.md`
3. `python3 scripts/registry_status.py` — all Wave 1 rules visible
4. `REFRAME_A_ENABLED=1 python3 scripts/run_selected_sessions.py --report /tmp/reframe_a_report.json` on a heavy session
5. Confirm `cross_window_dedup_hits > 0` in at least one session
6. Confirm `_refine_observations` still callable (refine pass coexists)
7. Re-run `audit_pipeline_dimensions.py` on the new report; confirm `affect_signal_no_extraction` and `monotone_knowledge_lens` appear when applicable

---

## Summary

| Wave | Tasks | Parallelism | Key deliverables |
|------|-------|-------------|------------------|
| 1 | 1.1, 1.2 | 2 agents | 4 new stats fields, 3 new rules, obs_indices validation, Rule 10, mixed valence |
| 2 | 2.1, 2.2 | 2 agents | lifecycle audit script, registry status CLI |
| 3 | 3.1 | 1 agent | downstream-utility rule, remove noisy rule, DB index |
| 4 | 4.1 | 1 agent | Reframe A in-session vec store, prior-extraction injection |
| 5 | verification | orchestrator | end-to-end run + coexistence check |
