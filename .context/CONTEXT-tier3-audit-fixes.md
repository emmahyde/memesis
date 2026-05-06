# CONTEXT: tier3-audit-fixes

**Slug:** tier3-audit-fixes
**Date:** 2026-04-28
**Mode:** Panel consensus (--auto)
**Status:** Ready for execution

## Scope

Address remaining tier3 items in memesis pipeline:
1. **#29** — evidence_obs_indices validation + demotion
2. **#30** — reframe orphan-as-quality-gate (prompt language)
3. **#32** — reconcile somatic + synthesis affect paths into Memory.importance
4. **#33** — per-session-type extraction guidance in OBSERVATION_EXTRACT_PROMPT
5. **#34** — skip friction sub-rule in SKIP_PROTOCOL
6. **#36** — Memory write-site flow-through audit for new fields

Out of tier3 scope:
- Retrieval-side affect read path (`_crystallized_hybrid` RRF wiring) — deferred to Wave D / dedicated track

## Locked Decisions (Panel Consensus)

### Architecture / Sequencing
- **#32 reconciliation site**: `core/consolidator.py:_execute_keep()` (write-time, not extraction or retrieval)
- **#29 split of concerns**: detection in `core/card_validators.py` (named private fn), action (demotion) co-located with existing strip code at `core/issue_cards.py:synthesize_issue_cards` lines 288–301
- **Schema migration precedes write-site wiring**: `criterion_weights` and `rejected_options` are NOT in `core/models.py` today (Marcus's blocker finding) — must add columns + migration before `#36` wiring
- **Retrieval read-path**: deferred. Tier3 ships write-side only; `affect_score` placeholder in `_last_hybrid_candidates` remains 0.0 until dedicated retrieval ticket

### #32 Fix Shape (consensus form)
```python
card_importance = decision.get("importance") if is_card else None
if card_importance is not None:
    try:
        base_importance = max(0.0, min(1.0, float(card_importance)))  # clamp
    except (TypeError, ValueError):
        base_importance = 0.5 + importance_boost  # fallback to old path; log WARN
    if card_fields.get("affect_valence") == "friction":
        base_importance = min(1.0, base_importance + 0.05)  # Kensinger
    mem_importance = base_importance
else:
    mem_importance = min(0.5 + importance_boost, 1.0)  # non-card path unchanged
```

### Prompt Discipline
- No new numbered rules in `ISSUE_SYNTHESIS_PROMPT` — at instruction-overload boundary
- `#30` = single consolidating sentence at top of Rules 6/7/8 block: *"Orphaning is a quality gate. Prefer emitting zero cards to forcing a cluster."*
- `#33` = `SESSION_TYPE_GUIDANCE` dict in `core/prompts.py` + `{session_type_guidance}` template var, populated at render time (no 4x branched prompt family)
- `#34` = sub-rule appended to existing `SKIP_PROTOCOL` block, scoped to existing structure

### Conventions to Enforce
- Post-synthesis validators live in `core/card_validators.py` (established pattern: `_card_evidence_load_bearing`)
- Migration idempotency: PRAGMA `table_info` check before `ALTER TABLE ADD COLUMN` — mirror `scripts/migrate_stage15_fields.py` exactly
- JSON storage for dict/list fields: `criterion_weights` and `rejected_options` as `TextField(null=True)` storing `json.dumps()` output (consistent with `linked_observation_ids`, `tags`)
- 1-line WHY comments only; no docstrings
- Write-site checklist: when `extract_card_memory_fields()` gains a return key, grep every `Memory.create()` call before closing the PR

### Concerns to Watch
- Stale embeddings on content rewrite (CONCERNS.md): `affect_valence` is sidecar (not in `content_hash`), so adding the column won't trigger re-embed cascade. Latent risk if reconsolidation later updates `affect_valence` + `content` together — flag in CONCERNS.md, don't block
- Naive vs UTC datetime mixing: not introduced by this work, but any new audit timestamp records must use consistent format
- Migration not atomic: don't add to `consolidation_log` migration pattern (DROP+CREATE+INSERT loop outside transaction); use ALTER TABLE only

### Reusable Code
- `core/card_validators.py` — `_card_evidence_load_bearing` as structural template for `#29`
- `core/issue_cards.py:extract_card_memory_fields()` — already returns 6 fields including new ones; extend rather than duplicate
- `scripts/migrate_stage15_fields.py` — copy verbatim for new column migration; only `NEW_COLUMNS` list changes
- `core/extraction_affect.py:apply_affect_prior` — already correct; somatic boost in observations before synthesis
- `core/transcript_ingest.py:_merge_card_affect` — existing synthesis→somatic reconciliation for session summary

## Locked Decisions (User-Resolved)

### D1 — Kensinger Application Location → **LOCKED: Option C**
Remove synthesis prompt Rule 3. Consolidator (`_execute_keep`) is the sole Kensinger application site. Ships as one coordinated prompt+code change. Single responsibility; eliminates split-deploy double-apply risk.

### D2 — #32 Wave Position → **LOCKED: Wave A**
`Memory.importance` (line 67 in `models.py`) is pre-existing — no schema blocker. #32 fix + D1 prompt-Rule-3 removal bundle into Wave A so the prompt+code change ships atomically. Deferring = ongoing data corruption across every consolidation run.

### D3 — affect_valence Null Convention → **LOCKED: "neutral" for card-derived, NULL for non-card**
Avoids false-neutral reads on non-card memories in retrieval filters. Explicit for card paths; absent (nullable) for everything else.

### D4 — #29 Fallback on Malformed LLM Importance → **LOCKED: Clamp + log warning + fallback to old path**
Hard-fail breaks ingestion for a validation issue. Matches codebase graceful-degradation pattern throughout.

## Recommended Wave Structure (for `/plan-waves`)

| Wave | Items | Blocking |
|------|-------|----------|
| **A — Foundation + data-corruption fix** | Add `criterion_weights` + `rejected_options` columns to `Memory`; migration script (PRAGMA-gated); `#32` consolidator importance fix; remove synthesis prompt Rule 3 (D1=C, ships atomically with #32) | Unblocks all write-sites; stops ongoing importance data corruption |
| **B — Write-path correctness (parallel)** | `#29` (validator + demotion); `#36-A` (consolidator wiring of new columns); `#33` (`SESSION_TYPE_GUIDANCE`); `#34` (`SKIP_PROTOCOL` sub-rule) | Wave A |
| **C — Prompt discipline + defensive hardening** | `#30` (Rules 6/7/8 reframe); `#36-B` (defensive null pass-through at non-card sites: crystallizer, self_reflection, ingest, seed); `affect_valence` default convention per D3 | Wave B |
| **D — Deferred / explicit scope** | Retrieval read-path (`_crystallized_hybrid` RRF wiring of `Memory.affect_valence`) | Explicit user decision |

## Stakeholder Recommendations Reference

Full panel transcript: `.context/DISCUSSION_LOG-tier3-audit-fixes.md`

## Tasks Mapped

- #29 → Wave B (validator + demotion)
- #30 → Wave C (prompt reframe)
- #31 → ALREADY COMPLETE (current `core/issue_cards.py` line 74 carries mixed-valence comment)
- #32 → Wave A (consolidator fix + prompt Rule 3 removal, atomic bundle per D1=C, D2=Wave A)
- #33 → Wave B (template var)
- #34 → Wave B (SKIP_PROTOCOL sub-rule)
- #35 → ALREADY COMPLETE (Rule 6 ENTITY GATE in current prompt at lines 109–112)
- #36-A → Wave B (consolidator card branch)
- #36-B → Wave C (defensive nulls at non-card sites)
