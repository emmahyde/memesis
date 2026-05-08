# CONTEXT: Tier 2 Audit Fixes + Reframe A

Source: sweepV2 5-agent synthesis (`backfill-output/audit-runs/sweepV2/analysis/SYNTHESIS.md`) plus per-agent reports (B, C, D, E). Tier 0 + Tier 1 shipped. This is the architectural pass.

## Scope

Eight items across two horizons:

### Tactical (synthesis Tier 2 §11–13)

1. **Item 11 — End-to-end lifecycle audit script**
   - File: new `scripts/audit_lifecycle.py` (or `core/lifecycle_audit.py` + thin CLI wrapper)
   - Read every ephemeral session file, join `Observation.status` against `Memory` rows by `source_session_id`, walk `consolidation_log`, produce per-session `ephemeral → consolidated → crystallized → instinctive` counts. Surface stuck-pending observations (`status=pending`, age > N days). Output Markdown report mirroring `scripts/audit_pipeline_dimensions.py` style.
   - Add stuck-pending alert: rule `consolidation_stuck` fires if any session has `status=pending` for >7 days.
   - Source: E §5 + Top-3 #1; SYNTHESIS §"What still won't be caught" #4.

2. **Item 12 — Closed-loop registry generalization**
   - File: `core/rule_registry.py` (already exists from `bc2148a`), `scripts/registry_status.py` (new CLI)
   - Lift `select_chunking()` pattern to a typed parameter override registry. Every confirmed rule with `proposed_action` should map to a parameter override (Jaccard threshold, importance gate, max_windows, prefilter knobs, synthesis_strict).
   - Audit current registry: enumerate every confirmed-rule type; for each, decide if there's a corresponding parameter knob. If no knob exists, add one.
   - Add CLI `python3 scripts/registry_status.py` showing: confirmed rules, active overrides, dormant rules without knobs, fire counts.
   - Closes loop on `confirmed_rule_no_action` rule from Tier 1.
   - Source: C §3.

3. **Item 13 — Downstream-utility metric (replace `issue_card_collapse_efficient`)**
   - Files: `core/feedback.py`, `core/self_reflection_extraction.py`, schema migration
   - Add `RetrievalLog` join to track whether cards from session N are retrieved during sessions N+1..N+10. New rule `cards_unused_high_importance`: fires when ≥3 high-importance (`>=0.8`) cards from a session are never retrieved within 10 subsequent sessions.
   - Replace (delete) `issue_card_collapse_efficient` rule — currently noise, fires on every dense session.
   - Add `RetrievalLog` index on `(memory_id, session_id, was_used)` if not present.
   - Source: C gap 2C + SYNTHESIS §"What still won't be caught" #2.

### ExtractionRunStats field additions (synthesis-implicit; from C §4)

4. **Item 14 — Five stats fields + corresponding rules**
   - File: `core/self_reflection_extraction.py` (extends `ExtractionRunStats`)
   - Add fields:
     - `unique_knowledge_types_emitted: int` — distinct `knowledge_type` values in final observations
     - `repeated_facts_count: int` — count of obs with Jaccard ≥0.55 vs existing memory store (cross-session). Note: distinct from Tier 1's `repeated_fact_hashes` (exact content_hash collisions); this is fuzzy semantic overlap.
     - `windows_with_affect_signal_but_no_card: int` — windows where `affect.max_boost > 0` but `productive_windows` excluded them
     - `min_card_importance: float` — lowest importance among final cards
     - (`obs_per_cost_call` already added in Tier 1 W2 — skip)
   - Add three rules driven by these fields:
     - `monotone_knowledge_lens` — fires when `unique_knowledge_types_emitted == 1` AND `final_observations >= 5`. importance=0.6, kind="finding".
     - `affect_signal_no_extraction` — fires when `windows_with_affect_signal_but_no_card >= 3`. importance=0.7, kind="finding".
     - `forced_clustering_low_importance` — fires when `synthesis_overgreedy` fires AND `min_card_importance < 0.4`. importance=0.7, kind="finding".
   - `repeated_facts_count` is read-only signal; pairs with Tier 1's `repeated_facts_high` for the exact-vs-fuzzy distinction.
   - Source: C §4.

### Stage 1.5 Synthesis enhancements (from B §4)

5. **Item 15 — `evidence_obs_indices` validation**
   - File: `core/issue_cards.py`
   - Currently populated in card schema but never validated against input observation count. A hallucinated index (e.g., `999` when only 10 obs in input) silently passes.
   - Add post-parse validation in `synthesize_issue_cards()`: each `evidence_obs_indices` value must satisfy `0 <= idx < len(observations)`. Drop or repair cards with out-of-range indices. Log as `dropped_invalid_indices` stat.
   - Source: B §4 first paragraph.

6. **Item 16 — Drop-weak-obs prompt instruction (forced clustering tightening)**
   - File: `core/issue_cards.py`
   - Tier 1 W3b added Rules 7/8/9 (orphan target, zero-orphan audit, lookup-table guard). This adds a complementary instruction: **drop**, not just **orphan**, observations whose `importance < 0.3` AND that share no entity with any sibling.
   - Add Rule 10: "DROP GATE: An observation with importance < 0.3 sharing no named entity with any sibling MAY be dropped entirely (omit from both `issue_cards[]` and `orphans[]`). Use sparingly — preserves orphan signal but reduces noise floor."
   - Add `dropped_weak_observations` to stats output to track drop rate. Audit can monitor drop-rate vs orphan-rate over time.
   - Source: B §4 second paragraph.

7. **Item 17 — Mixed valence over time**
   - File: `core/issue_cards.py`
   - Currently `user_affect_valence` collapses multi-turn reactions into one phrase. The `mixed` enum value exists but the prompt doesn't instruct the LLM to use it when reaction evolves.
   - Add to `user_affect_valence` description: "Use `mixed` when the user's reaction evolved across the card's span (e.g., initial friction then accept). Track the trajectory in `user_reaction` text."
   - Source: B §4 third paragraph.

### Reframe A — Stateful incremental extraction (synthesis §"Reframe A")

8. **Item 18 — Queryable ephemeral vector index for cross-window dedup**
   - Files: `core/transcript_ingest.py`, `core/embeddings.py` (already exists), `core/vec.py` (already exists), schema migration
   - Architectural shift: each window in `extract_observations_hierarchical` runs against an in-session vector index of prior-window extractions. Inject top-K similar prior observations as `PRIOR EXTRACTIONS (do not duplicate)` context block in the OBSERVATION_EXTRACT_PROMPT. Stage 1 becomes stateful incremental accumulator.
   - Stage 1.5 synthesis shifts from cross-window dedup to cross-session pattern elevation (less paraphrase reconciliation, more genuine grouping).
   - Cost: 1 embedding call per window. Use existing `core.embeddings` infrastructure (sentence-transformers). Keep within memesis pyproject deps; no new dep.
   - In-session index: ephemeral SQLite vec table scoped to `session_id`, dropped after consolidation. Use `sqlite-vec` (already a dep).
   - New extraction prompt placeholder `{prior_extractions}` filled with top-3 similar observations from in-session index, or empty string for first window.
   - Add stat `cross_window_dedup_hits` (count of times an extraction was suppressed by prior-extraction context).
   - Compatibility: refine pass (Tier 0 W3) becomes redundant with Reframe A. Add deprecation flag `REFINE_PASS_ENABLED = True` (default keep-on); when Reframe A proves out, flip to False and delete.
   - Source: SYNTHESIS §"Reframe A" + E §6.

## Decisions

- **Five waves, not eight items.** Group by file ownership and dependency:
  - **Wave 1:** Items 14 (stats fields + 3 rules) + 15 (evidence_obs_indices validation) + 16 (drop gate) + 17 (mixed valence). All small, mostly additive prompt/code edits. Items 14 owns `core/self_reflection_extraction.py`. Items 15+16+17 own `core/issue_cards.py`. Disjoint files, parallel-safe.
  - **Wave 2:** Items 11 (lifecycle audit) + 12 (registry generalization). Items 11 owns new file `scripts/audit_lifecycle.py` (and read-only access to `core/lifecycle.py` / `core/database.py`). Items 12 owns `core/rule_registry.py` + new `scripts/registry_status.py`. Disjoint, parallel-safe.
  - **Wave 3:** Item 13 (downstream-utility metric). Owns `core/feedback.py` + `core/self_reflection_extraction.py` (new rule) + DB migration. Sequential single-task because it touches both feedback and rules.
  - **Wave 4:** Item 18 (Reframe A — stateful extraction). Largest. Owns `core/transcript_ingest.py` + new ephemeral vec helper module + `core/prompts.py` (placeholder addition). Subdivided internally; runs as single agent because changes are tightly coupled.
  - **Verification wave:** end-to-end run after Wave 4 to confirm Reframe A works on real session data and refine pass + Reframe A coexist correctly.
- **No deprecations in this tier.** Refine pass stays default-on; Reframe A turns on opt-in via flag. Real cleanup happens after empirical validation.
- **Use existing infra.** `core.embeddings`, `core.vec`, `sqlite-vec` already in deps. No new packages.
- **Schema migrations idempotent.** Same pattern as Tier 1 W3a (try/except on duplicate-column).
- **Tests live alongside.** Existing test files extended; one new file `tests/test_audit_lifecycle.py` for the new script.

## Out of Scope

- Reframe B (differential session scoring) — defer; rules-as-circuit-breakers + stats fields handle near-term needs. Revisit after 50+ sessions of telemetry.
- Speaker-tagged window rendering (composite-narrative attribution) — synthesis §"What still won't be caught" #3. Deferred — needs prompt changes plus rendering changes.
- Cross-session re-extraction at the *retrieval* layer (different from extraction-side dedup) — out of scope.
- Backfill of new Memory schema columns from Tier 1 W3a — opportunistic only.
- Replacing peewee with raw SQL or a different ORM.
- LLM-based actor extraction (regex sufficient per Tier 1 W3b).

## Cross-cutting Concerns

- **Refine pass + Reframe A overlap.** Both target paraphrase-across-windows. With Reframe A enabled, refine pass becomes redundant (and 2× cost). Default keep-on for safety; add a flag and benchmark, not a delete.
- **`repeated_facts_count` (Item 14) vs `repeated_fact_hashes` (Tier 1 W2).** Different mechanisms — exact content_hash vs fuzzy Jaccard. Keep both: hashes catch byte-identical re-extractions, Jaccard catches paraphrase-level cross-session repeats. Two rules (`repeated_facts_high` confirmed-exact, new `repeated_facts_fuzzy_high` if needed).
- **Pre-existing uncommitted state.** As of Tier 1 close, working tree has untracked `core/card_validators.py`, `core/extraction_affect.py`, `claude-mem/`, `memvid/`, etc. Wave agents must verify they don't accidentally commit those.
- **Registry as source-of-truth.** Once Item 12 lands, every parameter override flows through `RULE_OVERRIDES`. Hardcoded thresholds (Jaccard 0.55, 0.7, importance gate 0.3) become registry-resolvable.
- **`scripts/audit_pipeline_dimensions.py` integration.** Item 11's lifecycle script and the existing extraction-pipeline audit script should share rendering helpers (`fmt_card`, `fmt_obs`). Lift to `scripts/_audit_render.py` if needed.

## Verification

After Wave 1:
- `python3 -m pytest tests/test_self_reflection_extraction.py tests/test_issue_cards.py -v`
- New rules registered; new stats fields present in `to_dict()`.
- Prompt rendering tests confirm new instructions verbatim.

After Wave 2:
- `python3 scripts/audit_lifecycle.py --base-dir ~/.claude/memory --out /tmp/lifecycle_audit.md` runs end-to-end; surfaces stuck-pending if any.
- `python3 scripts/registry_status.py` prints active vs dormant overrides table.

After Wave 3:
- `python3 -m pytest tests/test_feedback.py tests/test_self_reflection_extraction.py -v`
- `cards_unused_high_importance` rule appears in `aggregate_audit()`.
- `issue_card_collapse_efficient` removed (verify via `list_rules()`).

After Wave 4 (Reframe A):
- `REFRAME_A_ENABLED=1 python3 scripts/run_selected_sessions.py --report /tmp/reframe_a_report.json`
- `cross_window_dedup_hits > 0` on at least one heavy session.
- `prefilter_skipped_count` (Tier 1) and `cross_window_dedup_hits` together should reduce raw observation count by ≥30% on representative sessions vs Tier 1 baseline.
- `_refine_observations` still callable; coexistence test passes.

After full Tier 2:
- `python3 -m pytest tests/ -v` zero regressions.
- Re-run sweepV2 audit; expect: `affect_signal_no_extraction` distinguishes detector miss from LLM skip; `monotone_knowledge_lens` flags single-axis sessions; `cards_unused_high_importance` flags real waste; lifecycle audit shows non-trivial promotion rates.
