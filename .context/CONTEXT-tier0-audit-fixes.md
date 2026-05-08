# CONTEXT: Tier 0 Audit Fixes

Source: pipeline audit on memesis observation/synthesis pipeline. Five concrete bugs, all confirmed against current code. Goal — ship Tier 0 in two waves with smallest blast radius first, verify, then larger semantic changes.

## Scope

Five bugs from audit, ordered by risk:

1. **Bug 1 — dead-key lookup in `select_chunking()`**
   - File: `core/self_reflection_extraction.py:647`
   - Current: `by_rule.get("chunking_mismatch_user_anchored_low_turns")`
   - Fix: rename lookup key to `"chunking_suboptimal"` (rule was renamed in PHASE-E without updating consumer)
   - Effect: confirmed-rule branch becomes live; `chunking_suboptimal` rule fires can drive stride selection again

2. **Bug 4 — dead/redundant dedup pass**
   - Files: `core/transcript_ingest.py`
     - `_normalize_for_dedupe` (lines 211–215) — keep, used by content hash
     - `_is_duplicate` (lines 218–236) — DELETE (Jaccard 0.7)
     - `_dedupe_observations` (lines 239–259) — REPLACE with content-hash exact-dupe check
     - `REFINE_PROMPT` (lines 262–289) — DELETE (unused)
     - `_refine_observations` (lines 291–338) — DELETE (defined but never called)
     - Call site: `core/transcript_ingest.py:453` — keep call to new content-hash dedup, same return shape `(deduped, n_dropped)`
   - Rationale: Jaccard fires 2/175 across 5 sessions because LLM rewords across windows; word-bag Jaccard cannot catch paraphrase. Stage 1.5 synthesis already does semantic dedup. Replace with cheap content hash for exact dupes only.

3. **Bug 2 — affect merge from issue cards into session affect**
   - File: `core/transcript_ingest.py`
   - Function: `_aggregate_session_affect()` at line 493 — currently aggregates only somatic-derived `WindowAffect`
   - Add: `_merge_card_affect(cards, base_affect)` — reconciles LLM-derived `user_reaction` and `user_affect_valence` from issue cards into the aggregated session affect
   - ~20 LOC, no schema change, no new LLM call
   - Effect: solves `affect_blind_spot` (9 fires) at architectural level — somatic detector can't see compiler errors / behavioral corrections / non-lexical pushback; LLM cards already carry that signal

4. **Bug 5 — Stage 1 truncation + JSON repair + skip-reason persistence**
   - File: `core/llm.py` — three call signatures default `max_tokens=1024` (lines 212, 241, 279)
   - **DECISION (user override):** raise the GLOBAL default to `max_tokens=8192` across `call_llm`, `call_llm_async`, `call_llm_batch`. Sonnet 4-6 handles 8K cap fine; cost stays bounded since outputs are typically much shorter than the cap. Other call sites (consolidator, crystallizer, threads, self_reflection) benefit from same headroom. No need to special-case Stage 1.
   - Add basic JSON repair to extraction parse path: if final `]` missing, attempt to truncate to last complete `}` and close array; log recovery as `parse_error_repaired`
   - Persist skip-reason text in report — currently only outcome + affect retained
   - Effect: 16K-char windows producing 5+ obs no longer silently `[]`. All other LLM callers gain headroom too.

5. **Defect 3 — orphan rendering in audit MD**
   - File: `scripts/audit_pipeline_dimensions.py`
   - Currently renders cards in PICKED but not orphan observation text
   - Add orphan section under PICKED with full text, importance, kind, evidence quotes (mirror `fmt_card` style)
   - Effect: 6 orphans become visible for quality assessment

## Decisions

- **Two waves, not one.** Wave 1 = Bug 1 + Bug 4 (smallest blast radius, no semantic change). Verify by running audit on existing report fixtures. Wave 2 = Bug 2 + Bug 5 + Defect 3 (semantic and output-shape changes).
- **No schema migration.** All five fixes preserve current SQLite schema and report JSON shape (additive only).
- **No new LLM calls.** Bug 2 reuses fields already in cards. Bug 5 changes existing call's max_tokens.
- **Tests live alongside.** `tests/test_transcript_ingest.py` exists — add cases for content-hash dedup and `_merge_card_affect`. Bug 1 is covered by aggregated-audit fixture; add a regression test asserting the lookup key matches the canonical rule name.
- **Backwards compat.** No deprecation shims for `_refine_observations` / `_is_duplicate`. Per CLAUDE.md, delete cleanly.

## Out of Scope

- Tier 1 items (synthesis Rule 0, session-type prompt branching, schema field promotion, low-affect prefilter, new self-reflection rules)
- Tier 2 items (end-to-end lifecycle audit, parameter override registry, downstream-utility metric)
- Reframe A (stateful incremental extraction) and Reframe B (differential session scoring)

## Cross-cutting Concerns

- `_normalize_for_dedupe` retained — used by new content-hash function
- Affect aggregation called from `synthesize_issue_cards` flow; must not break card synthesis input shape
- `select_chunking()` is the only place a self-observation mechanically changes behavior — Bug 1 fix restores that loop

## Verification

After Wave 1:
- `python3 -m pytest tests/test_transcript_ingest.py tests/test_self_reflection_extraction.py -v`
- Re-run `scripts/audit_pipeline_dimensions.py` on existing report; expect identical PICKED count, lower DROPPED count for Jaccard line
- Confirm `chunking_suboptimal` lookup hits in audit aggregate

After Wave 2:
- Full `python3 -m pytest tests/ -v`
- Re-run `scripts/run_selected_sessions.py --report` on the heavy session set; expect `affect_blind_spot` rule fires reduced, no truncated `[]` from dense windows, orphans rendered in audit MD
