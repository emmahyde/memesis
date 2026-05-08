# CONTEXT: Tier 1 Audit Fixes

Source: sweepV2 5-agent synthesis (`backfill-output/audit-runs/sweepV2/analysis/SYNTHESIS.md`). Five Tier 1 items, ordered by blast radius. Tier 0 shipped (commits `0a164bd`, `0ddc058`, `e263be4`, `bc2148a`). This builds on top.

## Scope

Five items from synthesis §"Tier 1 — One-week tactical":

1. **Item 6 — Synthesis Rule 0 (entity-overlap orphan gate)**
   - File: `core/issue_cards.py`
   - Add Rule 0 to `ISSUE_SYNTHESIS_PROMPT` QUALITY RULES (line 87): "If an observation does not share at least one named entity (person, system, file, concept) with any other observation in the input, orphan it rather than forcing it into a card. Prefer zero cards to a card with one low-importance observation."
   - Effect: addresses `synthesis_overgreedy` (3 fires; 2/4 productive sessions producing zero orphans). Source: B-prompts-parser.md §"Top 3 Prompt Rewrites" #3.
   - No code change; prompt-only.

2. **Item 7 — Session-type extraction guidance**
   - File: `core/prompts.py`
   - Inject session-type block into `OBSERVATION_EXTRACT_PROMPT` after line 151 (`Session type: {session_type}`):
     - `research`: target conceptual/metacognitive findings; skip tool-call logs; force `work_event=null`
     - `writing`: target authoring decisions and aesthetic choices; force `work_event=null`
     - `code`: current behavior (`bugfix|feature|refactor|test|exploration`)
   - Also add skip-friction (B Top-3 #2): before SKIP_PROTOCOL, require LLM to name one evaluated-and-rejected candidate when skipping.
   - Effect: addresses 100% skip rate on research session 418d1c86 (11 windows × 0 productive); raises cost of reflexive skipping. Source: B-prompts-parser.md §1, §"Top 3 Prompt Rewrites" #1+#2.
   - No schema change; prompt-only.

3. **Item 8 — Pre-filter low-affect research windows**
   - File: `core/transcript_ingest.py`
   - In `extract_observations_hierarchical` between affect aggregation (around line 399) and the LLM batch call: if `session_type == "research"` AND `window_affect.max_boost == 0.0` AND no repetition/pushback signal, skip the LLM call. Log as `pre_filtered_low_affect` in the skips list.
   - Add config knob (module-level constant `PREFILTER_RESEARCH_NEUTRAL = True`) so the gate is reversible.
   - Add to report: `prefilter_skipped_count` so the audit can show recovery rate.
   - Effect: ~30-40% LLM cost reduction on observer/agent sessions. Source: E-lifecycle-architect.md §4 + Top-3 #3.

4. **Item 9 — Schema field promotion**
   - Files: `core/database.py`, `core/models.py`, `core/consolidator.py`, `core/issue_cards.py`
   - Add columns to `memories`: `temporal_scope TEXT`, `confidence REAL`, `affect_valence TEXT`, `actor TEXT`. All nullable. ALTER TABLE migration with idempotent guards.
   - Promote from issue card → Memory at consolidation time:
     - `temporal_scope` from `card.scope` (`session-local | cross-session-durable`)
     - `confidence` from `card.knowledge_type_confidence` (already in cards) or default 0.7
     - `affect_valence` from `card.user_affect_valence`
     - `actor` from facts attribution (best-effort regex first, deferred LLM extraction if needed)
   - Update `Memory` dataclass + `Memory.from_row()` / `Memory.to_row()` round-trip.
   - Source: B-prompts-parser.md §3 + §"Top 3 Schema Additions".
   - **Bigger blast radius — own wave.**

5. **Item 10 — New self-reflection rules**
   - File: `core/self_reflection_extraction.py`, `core/self_reflection.py` (rule registration)
   - Three new rules:
     - `low_obs_yield_per_call` — fires when `raw_observations / cost_calls < 2.0` AND `cost_calls >= 8`. Adds `obs_per_cost_call` to `ExtractionRunStats` (or compute inline).
     - `repeated_facts_high` — fires when ≥3 observations have content-hash collision against existing `Memory` rows in DB (cross-session re-extraction signal). Requires `MemoryStore` lookup at write time.
     - `confirmed_rule_no_action` — meta-rule: fires when any rule has `fire_count >= 5` AND `proposed_action` non-empty AND no corresponding parameter-override registry entry. Reads from confirmed-rule overrides registry (lifted from `bc2148a` closed-loop work).
   - Source: C-rules-architect.md §"Top 3 Actionable Rule Additions".

## Decisions

- **Three waves, not five.** Group by file ownership and risk:
  - **Wave 1:** Items 6 + 7 (prompt-only edits). Items 6 owns `core/issue_cards.py`; Item 7 owns `core/prompts.py`. Disjoint files, parallel-safe.
  - **Wave 2:** Items 8 + 10 (logic-only, additive). Item 8 owns `core/transcript_ingest.py`. Item 10 owns `core/self_reflection_extraction.py`. Disjoint files. `repeated_facts_high` needs `MemoryStore` read-only access — no schema change.
  - **Wave 3:** Item 9 (schema + cross-cutting). Sequential single-agent due to migration ordering: database.py → models.py → consolidator.py → issue_cards.py.
- **Migration strategy.** ALTER TABLE with `try/except` on duplicate-column error (idempotent). Existing rows get NULL for new columns; back-fill is opportunistic at next consolidation. No retro back-fill script in Tier 1 (defer).
- **Reuse closed-loop registry.** Item 10's `confirmed_rule_no_action` reads the parameter-override registry from `bc2148a`. Confirms that registry has a public read API; if not, that's a Wave 2 dependency surfaced during planning.
- **No new LLM calls.** All five items use existing transport. Item 9's `actor` field uses regex extraction first; LLM-based attribution is Tier 2.
- **Tests live alongside.** New tests in existing files. No new test modules.
- **Prompt regression risk.** Items 6 + 7 change extraction shape. Add a fixture-based regression test in `tests/test_issue_cards.py` and `tests/test_prompts.py` (or `tests/test_transcript_ingest.py`) verifying old fixture inputs produce well-shaped outputs.

## Out of Scope

- Item 11 (end-to-end lifecycle audit script) — Tier 2.
- Item 12 (closed-loop generalization) — already partially shipped in `bc2148a`; full lift to registry pattern is Tier 2.
- Item 13 (downstream-utility metric replacing `issue_card_collapse_efficient`) — Tier 2.
- Reframe A (stateful incremental extraction) — Tier 2.
- Reframe B (differential session scoring) — Tier 2.
- LLM-based actor extraction — defer to Tier 2 if regex coverage proves insufficient.
- Back-fill of new schema columns on existing memories — opportunistic only.

## Cross-cutting Concerns

- **Pre-existing uncommitted state in working tree:** `core/prompts.py` (1-line modification — user's pre-session work) and `core/issue_cards.py` / `core/extraction_affect.py` (untracked). Wave 1 must edit these files without clobbering user's pending work — verify before each edit. Same `cp`/`checkout`/`edit`/`restore` workflow used in Wave 2 of Tier 0 if needed.
- **`select_chunking()` and `aggregate_audit()` in `self_reflection_extraction.py`:** Item 10 must not break the dead-key fix from Tier 0 Wave 1. Tests should cover both old (Tier 0) and new (Tier 10) rules in the aggregate.
- **`MemoryStore` read API for `repeated_facts_high`:** content-hash query needs to be cheap. If `memories` doesn't have a content-hash column, add one in Wave 3 (Item 9 territory) or compute on-the-fly in Wave 2 (acceptable for Tier 1 — optimize in Tier 2).
- **Migration ordering in Wave 3:** ALTER TABLE must run before any `Memory` write that uses the new fields. Add a schema version bump in `core/database.py` and run migration on first `MemoryStore` open.

## Verification

After Wave 1 (prompts):
- `python3 -m pytest tests/test_issue_cards.py tests/test_transcript_ingest.py -v`
- Manual: re-run extraction on session 418d1c86 (research, 11 windows, 0 productive) — expect `skips` to include skip-reason text from the new "name one evaluated candidate" requirement.
- Manual: re-run extraction on a session with `synthesis_overgreedy` history — expect more orphans, fewer forced clusters.

After Wave 2 (logic):
- `python3 -m pytest tests/test_transcript_ingest.py tests/test_self_reflection.py tests/test_self_reflection_extraction.py -v`
- Manual: re-run on observer session — expect `prefilter_skipped_count > 0`, `cost_calls` reduced.
- Manual: aggregate audit shows `low_obs_yield_per_call`, `repeated_facts_high`, `confirmed_rule_no_action` registered (even if zero fires on this fixture).

After Wave 3 (schema):
- `python3 -m pytest tests/test_storage.py tests/test_consolidator.py tests/test_issue_cards.py -v`
- Migration idempotency: open `MemoryStore` twice; second open must not fail or re-add columns.
- New cards consolidated to memory must populate `temporal_scope`, `confidence`, `affect_valence`. `actor` may be NULL for cards without clear attribution (acceptable).
- Full suite: `python3 -m pytest tests/ -v` — no regressions.
