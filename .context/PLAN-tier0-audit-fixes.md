# PLAN: Tier 0 Audit Fixes

**Source context:** `.context/CONTEXT-tier0-audit-fixes.md`
**Waves:** 2
**Tasks:** 5 (Wave 1: 2 parallel; Wave 2: 2 units in sequence + 1 parallel)
**Schema changes:** none
**New LLM calls:** none

---

## Cross-Wave Ownership Handoffs

| File | Wave 1 task | Wave 1 action | Wave 2 task(s) | Wave 2 action |
|---|---|---|---|---|
| `core/transcript_ingest.py` | Task 1.2 | Delete `_is_duplicate`, `_dedupe_observations` (Jaccard), `REFINE_PROMPT`, `_refine_observations`; insert content-hash `_dedupe_observations` | Tasks 2.1 + 2.2 (sequential, one agent) | 2.1 adds `_merge_card_affect`; 2.2 bumps `max_tokens` on `call_llm_batch` call + adds JSON repair in `_parse_extract_response` |
| `tests/test_transcript_ingest.py` | Task 1.2 | Add content-hash dedup regression test | Tasks 2.1 + 2.2 (sequential, one agent) | 2.1 adds `_merge_card_affect` test; 2.2 adds JSON repair test |

Wave 2 implementers must rebase on Wave 1's deletions. The removed Jaccard functions and `REFINE_PROMPT` must not reappear. The `_normalize_for_dedupe` helper is retained and may be referenced by the new content-hash function.

---

## Wave 1 — Small blast radius, no semantic change

**Parallelism:** 2 tasks, fully independent. No shared files.
**Verify after wave:**
```
python3 -m pytest tests/test_transcript_ingest.py tests/test_self_reflection.py -v
```
Also re-run `scripts/audit_pipeline_dimensions.py` on an existing report JSON; expect identical PICKED count, Jaccard dedup line shows 0 or reduced drops, and `chunking_suboptimal` lookup registers hits.

---

### Task 1.1 — Fix dead-key lookup in `select_chunking()`

**Summary:** Rename the `by_rule.get(...)` key in `select_chunking()` from `"chunking_mismatch_user_anchored_low_turns"` to `"chunking_suboptimal"` so the confirmed-rule branch becomes live again.

**Files owned:**
- `core/self_reflection_extraction.py`
- `tests/test_self_reflection.py` (add regression test only — do not modify existing tests)

**Depends on:** none

**Decisions:**
- Dead key from PHASE-E rename; fix is a one-line rename (CONTEXT §Bug 1).
- No backward-compat shim needed — the old key name had no callers outside this function.

**Acceptance criteria:**
1. `core/self_reflection_extraction.py` line ~647: `by_rule.get("chunking_suboptimal")` (not `"chunking_mismatch_user_anchored_low_turns"`).
2. `select_chunking()` docstring updated to reference the correct rule name.
3. New test in `tests/test_self_reflection.py` asserts that when `aggregate_audit()` returns a dict with key `"chunking_suboptimal"` and `confidence == "confirmed"`, `select_chunking()` returns `"stride"` for an agent-driven session shape. Test must fail against the old key name.
4. `python3 -m pytest tests/test_self_reflection.py -v` passes.

---

### Task 1.2 — Replace Jaccard dedup with content-hash exact-dupe check

**Summary:** Delete the four dead/redundant dedup functions from `core/transcript_ingest.py` and replace `_dedupe_observations` with a content-hash implementation of identical return shape `(deduped, n_dropped)`.

**Files owned:**
- `core/transcript_ingest.py`
- `tests/test_transcript_ingest.py`

**Depends on:** none

**Decisions:**
- DELETE (no shims): `_is_duplicate` (lines 218–236), the Jaccard `_dedupe_observations` (lines 239–259), `REFINE_PROMPT` (lines 262–288), `_refine_observations` (lines 291–338). Per CLAUDE.md: delete cleanly, no deprecation shims (CONTEXT §Bug 4, §Decisions).
- RETAIN: `_normalize_for_dedupe` (lines 211–215). It is used by the new content-hash function.
- NEW `_dedupe_observations`: hashes `obs.get("content","") + "|" + "|".join(obs.get("facts",[]))` (or equivalent stable string) via `hashlib.md5` (consistent with existing `Memory.compute_hash` idiom). Keeps first occurrence per hash; returns `(deduped, n_dropped)`.
- Call site at line 453 (`deduped, dropped = _dedupe_observations(all_obs)`) — unchanged signature, no call-site edits required.
- The DROPPED section comment in `scripts/audit_pipeline_dimensions.py` still references Jaccard — do NOT edit that file in this task (Task 2.3 owns it).

**Acceptance criteria:**
1. `_is_duplicate`, `REFINE_PROMPT`, and `_refine_observations` are absent from `core/transcript_ingest.py`.
2. New `_dedupe_observations` uses content hash (no Jaccard), returns `tuple[list[dict], int]`.
3. `_normalize_for_dedupe` still present and referenced by the new implementation.
4. New test class `TestContentHashDedup` in `tests/test_transcript_ingest.py`:
   - Exact duplicate (identical content) → second copy dropped, `n_dropped == 1`.
   - Paraphrase (different wording, same meaning) → both kept (no false-positive collapse).
   - Empty list → `([], 0)`.
5. `python3 -m pytest tests/test_transcript_ingest.py -v` passes.

---

## Wave 2 — Semantic and output-shape changes

**Parallelism:** Tasks 2.1 and 2.2 are assigned to ONE agent (sequential execution) because both own `core/transcript_ingest.py` and `tests/test_transcript_ingest.py`. Task 2.3 runs in parallel against a disjoint file.

**Wave 2 agent for 2.1+2.2 must pull Wave 1's changes before editing.**

**Verify after wave:**
```
python3 -m pytest tests/ -v
python3 scripts/run_selected_sessions.py --report   # if fixture sessions available
python3 scripts/audit_pipeline_dimensions.py <report.json>
```
Expect: `affect_blind_spot` fires reduced in new report; no silent `[]` from dense windows; orphans rendered with full text in audit MD.

---

### Task 2.1 — Merge issue-card affect into session affect summary

**Summary:** Add `_merge_card_affect(cards, base_affect)` and call it after `_aggregate_session_affect` so LLM-derived `user_reaction` / `user_affect_valence` from issue cards populate the session affect dict.

**Files owned:**
- `core/transcript_ingest.py`
- `tests/test_transcript_ingest.py`

**Depends on:** Wave 1 Task 1.2 (file must not contain deleted Jaccard functions)

**Decisions:**
- ~20 LOC, no schema change, no new LLM call (CONTEXT §Bug 2, §Decisions).
- `_merge_card_affect(cards: list[dict], base: dict) -> dict` signature. Returns updated `base` (mutates a copy, not in-place).
- Merges: collect all non-None `user_reaction` values from cards; collect all non-neutral `user_affect_valence` values. If any card has friction valence and `base["dominant_valence"]` is neutral, override. Append any card-level `user_reaction` strings to a new `card_reactions` key (list). No existing key is removed.
- Call site: in `extract_observations_hierarchical`, after `affect_summary = _aggregate_session_affect(affect_signals)` and before `synthesize_issue_cards(...)`. Replace `affect_summary` with `_merge_card_affect(cards, affect_summary)` — but note: cards are not available until after `synthesize_issue_cards`. Correct call order: call `_merge_card_affect` on the `cards` returned by `synthesize_issue_cards`, then store the merged dict. The merged dict is for reporting/downstream use, not re-fed into `synthesize_issue_cards`.
- Return dict merged affect in the `extract_observations_hierarchical` return value under `"session_affect"` key (additive, no existing key changed).

**Acceptance criteria:**
1. `_merge_card_affect` defined in `core/transcript_ingest.py`, called after `synthesize_issue_cards` returns.
2. `extract_observations_hierarchical` return dict includes `"session_affect"` key containing merged result.
3. New test class `TestMergeCardAffect`:
   - Cards with `user_affect_valence="friction"` override neutral base valence.
   - Cards with `user_reaction` strings accumulate in `card_reactions`.
   - Empty card list returns `base` unchanged.
4. Existing `synthesize_issue_cards` call site shape unchanged — no argument modification.
5. `python3 -m pytest tests/test_transcript_ingest.py -v` passes.

---

### Task 2.2 — Stage 1 max_tokens bump + JSON repair on truncated array

**Summary:** Bump `max_tokens` to 4096 on the Stage 1 `call_llm_batch` call site; add JSON repair logic to `_parse_extract_response` for truncated arrays; persist skip-reason text in the report.

**Files owned:**
- `core/transcript_ingest.py`
- `tests/test_transcript_ingest.py`

**Depends on:** Wave 1 Task 1.2 (file must not contain deleted functions); Task 2.1 must be committed first (sequential within agent)

**Decisions:**
- Do NOT change `core/llm.py` global defaults (CONTEXT §Bug 5, §Decisions).
- Call site is `call_llm_batch(prompts, max_concurrency=4)` at line ~413. Change to `call_llm_batch(prompts, max_concurrency=4, max_tokens=4096)`.
- JSON repair in `_parse_extract_response`: if `json.loads(raw)` raises `JSONDecodeError`, attempt repair before giving up: truncate `raw` to the position of the last `}`, append `]`, retry `json.loads`. If repair succeeds, log `"_parse_extract_response: JSON repaired (truncated array)"` at WARNING level and add `parse_error_repaired=True` field to the obs (or track in `drop_stats` — prefer a separate `drop_stats["parse_errors_repaired"]` counter). If repair also fails, fall through to existing `return []` path.
- Skip-reason persistence: when `_parse_extract_response` hits the `skipped=True` branch, include `"skip_reason"` in the skip record appended to `skips` list in `extract_observations_hierarchical`. Currently the skip record has `"outcome": "empty_or_skipped"` with no reason text; add `"skip_reason": reason` to that dict.

**Acceptance criteria:**
1. `call_llm_batch(prompts, max_concurrency=4, max_tokens=4096)` at the Stage 1 batch call site.
2. `_parse_extract_response` attempts JSON repair on `JSONDecodeError` before returning `[]`.
3. Repaired parse logged at WARNING; `drop_stats["parse_errors_repaired"]` incremented.
4. Skip records in `skips` list include `"skip_reason"` field when reason is available.
5. New test class `TestJsonRepair` (or test methods on existing class):
   - Truncated array `[{"content":"x","importance":0.5}` (missing `]`) → repaired, returns 1 obs.
   - Fully corrupt input → returns `[]`, no exception raised.
   - Well-formed input → unchanged path, no repair attempted.
6. `python3 -m pytest tests/test_transcript_ingest.py -v` passes.

---

### Task 2.3 — Orphan rendering in audit MD

**Summary:** Add full orphan observation rendering under the PICKED section of `scripts/audit_pipeline_dimensions.py`, mirroring `fmt_card` style.

**Files owned:**
- `scripts/audit_pipeline_dimensions.py`

**Depends on:** none (reads report JSON fields already present)

**Decisions:**
- Mirror `fmt_card` style: show `text`/`content`/`facts`, `importance`, `kind`, evidence quotes (CONTEXT §Defect 3).
- The existing `fmt_obs` function at lines 60–65 is a stub (single-line summary). Expand it or introduce `fmt_orphan` alongside it with full detail rendering.
- Update `render_session` orphan loop (line ~156) to call the expanded formatter.
- Update the DROPPED Jaccard comment (line ~178) to say "content-hash" instead of "Jaccard ≥ 0.7" — the dedup algorithm changed in Wave 1.
- No new imports needed. No schema change.

**Acceptance criteria:**
1. `fmt_obs` (or `fmt_orphan`) renders: full text (from `text`, `body`, `summary`, or `facts` fields in that priority order), `importance`, `kind`, evidence quotes (up to 2, truncated at 160 chars, same as `fmt_card`).
2. Orphan observations section in `render_session` uses the expanded formatter.
3. Jaccard reference in DROPPED section updated to "content-hash exact-duplicate check".
4. Manual smoke-test: `python3 scripts/audit_pipeline_dimensions.py <any_existing_report.json>` produces output where orphan entries show multi-line detail rather than single-line stub.
5. `python3 -m pytest tests/test_scripts.py -v` passes (or no regression if `audit_pipeline_dimensions` is not currently covered — confirm and note).
