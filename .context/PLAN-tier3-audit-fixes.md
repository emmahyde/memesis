# Plan: Tier 3 Audit Fixes

**Source:** .context/CONTEXT-tier3-audit-fixes.md
**Generated:** 2026-05-06
**Status:** Ready for execution

## Overview

Addresses six remaining tier3 pipeline audit items (#29, #30, #32, #33, #34, #36) in memesis. Delivers schema columns for new card fields, stops ongoing Memory.importance data corruption at the consolidator write site, tightens prompt discipline, and wires defensive nulls at all non-card Memory.create() call sites.

**Waves:** 4 (A, B, C, D)
**Total tasks:** 6 (Wave A: 1, Wave B: 3, Wave C: 2, Wave D: deferred — 0 implementable tasks)

---

## Wave A: Foundation + Data-Corruption Fix

**Prerequisite:** None (first wave)

### Task A1: Schema migration + #32 consolidator importance fix + D1 prompt Rule 3 removal

Adds `criterion_weights` and `rejected_options` columns to the `Memory` model and database; ships a PRAGMA-gated migration script; fixes `_execute_keep()` to use `card.importance` as the base importance value with clamp + fallback + Kensinger +0.05 for friction valence; atomically removes synthesis prompt Rule 3 (Kensinger bump now sole-site at consolidator per D1).

These three changes ship as one atomic task: the #32 fix and the Rule 3 removal are coupled by D1 (if one ships without the other, the Kensinger bump is either absent or double-applied).

- **Files owned:**
  - `core/models.py`
  - `core/consolidator.py`
  - `core/prompts.py`
  - `scripts/migrate_tier3_fields.py`
  - `tests/test_consolidator.py`
- **Depends on:** None
- **Decisions:** D1 (Rule 3 removal at consolidator), D2 (#32 in Wave A), D4 (clamp + log warning + fallback on malformed importance), ARCH-#32-site (consolidator `_execute_keep` is the reconciliation site), ARCH-schema-precedes-wiring (columns must exist before Wave B write-site wiring)
- **Acceptance criteria:**
  - [ ] `PRAGMA table_info(memories)` returns rows for `criterion_weights` (TEXT, null) and `rejected_options` (TEXT, null) after running `scripts/migrate_tier3_fields.py`
  - [ ] Running the migration script twice produces no errors and no duplicate columns (idempotent via PRAGMA guard, matching `scripts/migrate_stage15_fields.py` pattern)
  - [ ] `core/models.py` `Memory` class declares `criterion_weights = TextField(null=True)` and `rejected_options = TextField(null=True)`
  - [ ] `_execute_keep()` in `core/consolidator.py` uses `card.importance` clamped to [0.0, 1.0] when a card is present; `max(0.0, min(1.0, float(card_importance)))` is the clamp form
  - [ ] When `card.importance` is not a valid float (TypeError or ValueError), `_execute_keep()` falls back to `0.5 + importance_boost` and logs a WARN
  - [ ] Kensinger +0.05 bump is applied only when `card_fields.get("affect_valence") == "friction"` and only within the card branch
  - [ ] Non-card path (`card_importance is None`) is unchanged: `min(0.5 + importance_boost, 1.0)`
  - [ ] `ISSUE_SYNTHESIS_PROMPT` in `core/prompts.py` no longer contains the text of Rule 3 (grep `core/prompts.py` for the removed rule text returns no matches)
  - [ ] Test in `tests/test_consolidator.py` asserts card-path importance flows from card dict, not from a fixed 0.5 base
  - [ ] Test asserts friction valence triggers the +0.05 bump; non-friction card does not receive it
  - [ ] Test asserts malformed importance (e.g., `"not-a-number"`) falls back gracefully without raising

**Wave A status:** pending

---

## Wave B: Write-Path Correctness

**Prerequisite:** Wave A complete

Tasks B1, B2, B3 are parallel within this wave — file ownership is disjoint.

### Task B1: #29 — evidence_obs_indices validation + demotion

Adds `_card_evidence_indices_valid` detector to `core/card_validators.py` (already drafted per git status — verify it matches the CONTEXT spec); wires demotion action co-located with existing strip code at `core/issue_cards.py:synthesize_issue_cards` ~lines 288-301; increments `cards_invalid_indices_demoted` stat.

- **Files owned:**
  - `core/card_validators.py`
  - `core/issue_cards.py`
  - `tests/test_card_validators.py`
  - `tests/test_issue_cards.py`
- **Depends on:** Task A1 (Wave A complete)
- **Decisions:** ARCH-#29-split (detection in `card_validators.py` named private fn; demotion action at `issue_cards.py` strip-code site)
- **Acceptance criteria:**
  - [ ] `_card_evidence_indices_valid(card, window_count)` exists in `core/card_validators.py` and returns `False` when all indices are out of range `[0, window_count)` or indices list is empty
  - [ ] Returns `True` when at least one index is within `[0, window_count)`
  - [ ] `synthesize_issue_cards()` in `core/issue_cards.py` calls `_card_evidence_indices_valid` at the strip-code site (~lines 288-301) and demotes cards with all-invalid indices
  - [ ] `cards_invalid_indices_demoted` stat is incremented each time a card is demoted for invalid indices
  - [ ] Test in `tests/test_card_validators.py` covers: all-out-of-range indices, at least one valid index, empty indices list
  - [ ] Test in `tests/test_issue_cards.py` asserts a card with indices `[999]` against a 5-window corpus is demoted and the stat increments

### Task B2: #36-A — consolidator card branch wiring for new fields

In `core/consolidator.py:_execute_keep()`, passes `criterion_weights` and `rejected_options` from `extract_card_memory_fields()` into `Memory.create()` when a card is present. Performs write-site checklist: grep all `Memory.create()` calls in the codebase to confirm no other card-branch sites are missed.

- **Files owned:**
  - `core/consolidator.py`
  - `tests/test_consolidator.py`
- **Depends on:** Task A1 (columns exist in schema + `_execute_keep()` shape established)
- **Decisions:** D3 (affect_valence "neutral" for card-derived paths — B2 applies this for the consolidator card branch), ARCH-write-site-checklist (grep all `Memory.create()` before closing)
- **Acceptance criteria:**
  - [ ] `Memory.create()` in the card branch of `_execute_keep()` passes `criterion_weights=json.dumps(criterion_weights)` and `rejected_options=json.dumps(rejected_options)` where those values come from `extract_card_memory_fields()`
  - [ ] When `extract_card_memory_fields()` returns `None` or missing keys, `criterion_weights` and `rejected_options` default to `None` (not an exception)
  - [ ] Card branch passes `affect_valence="neutral"` (not NULL) per D3, unless friction is detected (then `"friction"`)
  - [ ] Round-trip: a Memory created via the card branch can have `json.loads(memory.criterion_weights)` return the original dict
  - [ ] Grep of `Memory.create(` across all `core/` and `scripts/` files is documented in the PR (write-site checklist complete)
  - [ ] Test in `tests/test_consolidator.py` asserts `criterion_weights` and `rejected_options` are stored on the created memory

### Task B3: #33 + #34 — SESSION_TYPE_GUIDANCE dict + SKIP_PROTOCOL friction sub-rule

Adds `SESSION_TYPE_GUIDANCE` dict to `core/prompts.py`; adds `{session_type_guidance}` template variable to `OBSERVATION_EXTRACT_PROMPT` populated at render time (no branched prompt family); appends friction sub-rule to existing `SKIP_PROTOCOL` block.

- **Files owned:**
  - `core/prompts.py`
  - `tests/test_prompts.py`
- **Depends on:** Task A1 (Wave A removed Rule 3; B3 must read A1's version of `prompts.py` before editing)
- **Decisions:** ARCH-no-new-numbered-rules (no new numbered rule in `ISSUE_SYNTHESIS_PROMPT`; guidance injected via template var), ARCH-#33 (`SESSION_TYPE_GUIDANCE` dict + `{session_type_guidance}` var, not 4x branched prompt family), ARCH-#34 (sub-rule appended to existing `SKIP_PROTOCOL` block)
- **Acceptance criteria:**
  - [ ] `SESSION_TYPE_GUIDANCE` dict exists in `core/prompts.py` with keys for each supported session type
  - [ ] `OBSERVATION_EXTRACT_PROMPT` contains `{session_type_guidance}` as a template variable
  - [ ] Rendering `OBSERVATION_EXTRACT_PROMPT` with a known session type produces a string containing the corresponding guidance entry from `SESSION_TYPE_GUIDANCE`
  - [ ] `SKIP_PROTOCOL` block in `core/prompts.py` contains the friction sub-rule as an appended sub-item (no new top-level numbered rule)
  - [ ] Test in `tests/test_prompts.py` asserts each session type produces its guidance text in the rendered prompt
  - [ ] Test asserts `SKIP_PROTOCOL` text contains the friction sub-rule string

**Wave B status:** pending

---

## Wave C: Prompt Discipline + Defensive Hardening

**Prerequisite:** Wave B complete

Tasks C1 and C2 are parallel within this wave — file ownership is disjoint.

### Task C1: #30 — Orphan-as-quality-gate reframe in ISSUE_SYNTHESIS_PROMPT

Inserts a single consolidating sentence at the top of the Rules 6/7/8 block in `ISSUE_SYNTHESIS_PROMPT`: *"Orphaning is a quality gate. Prefer emitting zero cards to forcing a cluster."* No new numbered rule; surgical insertion only.

- **Files owned:**
  - `core/prompts.py`
  - `tests/test_prompts.py`
- **Depends on:** Task B3 (Wave B B3 owns `core/prompts.py`; C1 must build on B3's version)
- **Decisions:** ARCH-no-new-numbered-rules (consolidating sentence only; no numbered rule added), ARCH-#30-sentence (exact text: "Orphaning is a quality gate. Prefer emitting zero cards to forcing a cluster.")
- **Acceptance criteria:**
  - [ ] `ISSUE_SYNTHESIS_PROMPT` contains the exact sentence "Orphaning is a quality gate. Prefer emitting zero cards to forcing a cluster." appearing before or at the top of the Rules 6/7/8 block
  - [ ] The sentence appears exactly once in the prompt (grep count = 1)
  - [ ] No new numbered rule (e.g., "Rule 9:") has been added to `ISSUE_SYNTHESIS_PROMPT`
  - [ ] Total rule count in `ISSUE_SYNTHESIS_PROMPT` is unchanged from Wave B's version
  - [ ] Test in `tests/test_prompts.py` asserts the reframe sentence is present in `ISSUE_SYNTHESIS_PROMPT`

### Task C2: #36-B — Defensive null pass-through at non-card write sites + D3 affect_valence convention

Audits and patches all non-card `Memory.create()` call sites in `core/crystallizer.py`, `core/self_reflection.py`, `core/ingest.py`, and `scripts/seed.py` to pass `criterion_weights=None`, `rejected_options=None`, and `affect_valence=None` (D3: NULL for non-card paths). Applies D3 `affect_valence` default convention at all Memory.create() call sites in scope.

- **Files owned:**
  - `core/crystallizer.py`
  - `core/self_reflection.py`
  - `core/ingest.py`
  - `scripts/seed.py`
  - `tests/test_crystallizer.py`
  - `tests/test_self_reflection.py`
  - `tests/test_ingest.py`
- **Depends on:** Task A1 (columns exist in schema), Task B2 (card-branch wiring establishes the D3 affect_valence precedent)
- **Decisions:** D3 (NULL for non-card `affect_valence`; "neutral" for card-derived), ARCH-write-site-checklist (all `Memory.create()` sites must be covered)
- **Acceptance criteria:**
  - [ ] Every `Memory.create()` call in `core/crystallizer.py`, `core/self_reflection.py`, `core/ingest.py`, and `scripts/seed.py` explicitly passes `criterion_weights=None` and `rejected_options=None`
  - [ ] None of those call sites pass a non-None `affect_valence` unless the path is explicitly card-derived (D3 convention: NULL for non-card)
  - [ ] No existing call site raises a `TypeError` for unexpected keyword arguments after the new columns are added
  - [ ] Test in `tests/test_crystallizer.py` asserts a crystallized memory has `criterion_weights=None` and `affect_valence=None`
  - [ ] Test in `tests/test_self_reflection.py` asserts self-reflection-created memories have `criterion_weights=None` and `affect_valence=None`
  - [ ] Test in `tests/test_ingest.py` asserts ingested native memories have `criterion_weights=None` and `affect_valence=None`

**Wave C status:** pending

---

## Wave D: Deferred — Explicit Out of Scope

**Prerequisite:** Wave C complete (if ever activated)

### Deferred: Retrieval read-path affect wiring (#36-C / retrieval track)

Wire `Memory.affect_valence` into `_crystallized_hybrid` RRF scoring in `core/retrieval.py`. The `affect_score` placeholder in `_last_hybrid_candidates` currently returns `0.0`. This requires a dedicated retrieval ticket.

**Status:** Explicitly deferred by user decision. Tier 3 ships write-side only. Do not implement in this plan.

**Wave D status:** deferred

---

## File Ownership Map

| File | Owner |
| --- | --- |
| `core/models.py` | Task A1 |
| `core/consolidator.py` | Task A1, Task B2 |
| `core/prompts.py` | Task A1, Task B3, Task C1 |
| `scripts/migrate_tier3_fields.py` | Task A1 |
| `tests/test_consolidator.py` | Task A1, Task B2 |
| `core/card_validators.py` | Task B1 |
| `core/issue_cards.py` | Task B1 |
| `tests/test_card_validators.py` | Task B1 |
| `tests/test_issue_cards.py` | Task B1 |
| `tests/test_prompts.py` | Task B3, Task C1 |
| `core/crystallizer.py` | Task C2 |
| `core/self_reflection.py` | Task C2 |
| `core/ingest.py` | Task C2 |
| `scripts/seed.py` | Task C2 |
| `tests/test_crystallizer.py` | Task C2 |
| `tests/test_self_reflection.py` | Task C2 |
| `tests/test_ingest.py` | Task C2 |

## Cross-Wave Ownership Handoffs

Files that are owned by different tasks across waves. The team lead MUST ensure the earlier wave is complete before the later wave's task touches the file.

| File | Wave N Owner | Wave M Owner | Handoff Notes |
| --- | --- | --- | --- |
| `core/prompts.py` | Task A1 (remove synthesis Rule 3) | Task B3 (add SESSION_TYPE_GUIDANCE dict + `{session_type_guidance}` var + SKIP_PROTOCOL sub-rule) | B3 must read A1's version — Rule 3 is already gone; B3 must not re-introduce it |
| `core/prompts.py` | Task B3 (SESSION_TYPE_GUIDANCE + SKIP_PROTOCOL) | Task C1 (insert reframe sentence at top of Rules 6/7/8 block) | C1 must read B3's version — reframe sentence is appended to the block as B3 left it; C1 must not revert B3's additions |
| `core/consolidator.py` | Task A1 (`_execute_keep` importance fix: card base + clamp + Kensinger) | Task B2 (#36-A: pass `criterion_weights` + `rejected_options` from `extract_card_memory_fields()` into `Memory.create()`) | B2 must read A1's `_execute_keep` shape before modifying; B2 adds field pass-through inside the card branch that A1 established |
| `tests/test_consolidator.py` | Task A1 (add importance fix tests) | Task B2 (add criterion_weights/rejected_options storage tests) | B2 adds tests to the same file; must not remove or override A1's test cases |
| `tests/test_prompts.py` | Task B3 (add SESSION_TYPE_GUIDANCE + SKIP_PROTOCOL tests) | Task C1 (add reframe sentence assertion) | C1 adds to the same test file; must not conflict with B3's test structure |

**Handoff protocol:** When a file appears here, the later task's implementer MUST:

1. Read the file as modified by the earlier task (not the original)
2. Build on those changes, not revert them
3. If the earlier task's changes conflict with the later task's needs, escalate to team lead

## Decision Traceability

| Decision | Description | Tasks |
| --- | --- | --- |
| D1 | Remove synthesis prompt Rule 3; consolidator `_execute_keep` is sole Kensinger application site; ships atomically with #32 | Task A1 |
| D2 | #32 fix placed in Wave A (not deferred); stops ongoing data corruption immediately | Task A1 |
| D3 | `affect_valence` convention: "neutral" for card-derived paths, NULL for non-card | Task B2, Task C2 |
| D4 | Malformed LLM importance: clamp + log warning + fallback to old path; no hard-fail | Task A1 |
| ARCH-#32-site | `_execute_keep()` in `core/consolidator.py` is the reconciliation site for importance (write-time, not extraction or retrieval) | Task A1 |
| ARCH-#29-split | Detection in `core/card_validators.py` (named private fn); demotion action co-located with existing strip code at `core/issue_cards.py:synthesize_issue_cards` ~lines 288-301 | Task B1 |
| ARCH-schema-precedes-wiring | `criterion_weights` and `rejected_options` columns must exist before #36-A write-site wiring | Task A1 (adds columns), Task B2 (depends on columns) |
| ARCH-no-new-numbered-rules | `ISSUE_SYNTHESIS_PROMPT` is at instruction-overload boundary; no new numbered rules may be added | Task B3, Task C1 |
| ARCH-#30-sentence | `#30` = single consolidating sentence at top of Rules 6/7/8 block; exact text locked in CONTEXT | Task C1 |
| ARCH-#33 | `SESSION_TYPE_GUIDANCE` dict + `{session_type_guidance}` template var; no branched prompt family | Task B3 |
| ARCH-#34 | Friction sub-rule appended to existing `SKIP_PROTOCOL` block; scoped to existing structure | Task B3 |
| ARCH-write-site-checklist | When `extract_card_memory_fields()` gains a return key, grep every `Memory.create()` call before closing | Task B2, Task C2 |
| ARCH-migration-idempotency | PRAGMA `table_info` check before `ALTER TABLE ADD COLUMN`; mirror `scripts/migrate_stage15_fields.py` exactly | Task A1 |
| ARCH-json-storage | `criterion_weights` and `rejected_options` as `TextField(null=True)` storing `json.dumps()` output; consistent with `linked_observation_ids`, `tags` | Task A1, Task B2 |
| ARCH-retrieval-deferred | Retrieval read-path (`_crystallized_hybrid` RRF wiring of `Memory.affect_valence`) is explicit out-of-scope; `affect_score` placeholder remains 0.0 | Wave D (deferred) |
