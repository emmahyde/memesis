# Discussion Log: tier3-audit-fixes

**Date:** 2026-04-28
**Mode:** Panel discussion (--auto)
**Stakeholders:**

- **Linnea Nguyen** — pipeline architect & prompt engineer — bias: prompt-side discipline over post-process validators
- **Marcus Tran** — schema/persistence engineer (peewee + sqlite-vec) — bias: write-site discipline, idempotent migrations, no silent field drops
- **Ines Rivera** — affect/cognitive science specialist (somatic, Kensinger, mixed-valence) — bias: signal preservation across pipeline boundaries

---

## Wave 1: Initial Positions

### Linnea Nguyen

- **#29 (evidence_obs_indices)**: Validator in `core/card_validators.py` — new `_card_evidence_indices_valid(card, window_count)` alongside existing `_card_evidence_load_bearing`. Mechanical/objective; no prompt rule buys anything.
- **#30 (orphan reframe)**: Surgical prompt rewrite — one consolidating sentence at top of Rules 6/7/8 block: *"Orphaning is a quality gate. Prefer emitting zero cards to forcing a cluster."* Don't add Rule 13 — at instruction-overload boundary.
- **#33 (per-session-type)**: `SESSION_TYPE_GUIDANCE` dict in `prompts.py` + `{session_type_guidance}` template var. Avoids 4x branched prompt family.
- **#34 (skip friction)**: Sub-rule under existing `SKIP_PROTOCOL` block, not new top-level rule.

**Wave grouping**: A(#29, #33, #34 parallel) → B(#30, #36) → C(#32 — blocked by merge-strategy decision lock).

**Top concern**: #32 needs explicit locked merge-strategy decision in CONTEXT before code. Options: max? synthesis-wins? somatic-wins?

### Marcus Tran

- **#29**: Existing strip code at `synthesize_issue_cards` lines 288–301 already strips out-of-range indices. Gap = missing demotion when cleaned indices empty. Add `cards_invalid_indices_demoted` stat.
- **#32**: Reconciliation site = `consolidator._execute_keep()`. Single expression: `if is_card and card_fields.get("affect_valence") == "friction": importance_boost = max(importance_boost, 0.05)`. Somatic wins if higher.
- **#36 CRITICAL FINDING**: `criterion_weights` and `rejected_options` are NOT in `Memory` model schema at all. `extract_card_memory_fields()` returns them, consolidator `Memory.create()` doesn't pass them, no columns in `models.py`. Silently dropped today. Two PRs: schema migration first, then consolidator wiring.

**Wave grouping**: Wave 1 (schema migration) → Wave 2 (#29, #32, #36-A) → Wave 3 (#36-B defensive nulls).

**Conventions**: migration idempotency PRAGMA-gated; JSON storage (TextField + json.dumps) for dict/list fields.

**Top concern**: re-embedding burden bounded but latent — affect_valence is sidecar (not in content_hash) but later reconsolidation that updates affect_valence + content together creates stale-vector risk.

### Ines Rivera

- **#32 PRIMARY**: Card synthesis already reports `importance: 0.85` with somatic boost baked in by `apply_affect_prior` at Stage 1. Consolidator currently THROWS THAT NUMBER AWAY and writes `min(0.5 + importance_boost, 1.0)` — different smaller scalar. Friction card extracted as 0.85 persisted as 0.5/0.6.

```python
card_importance = decision.get("importance") if is_card else None
if card_importance is not None:
    base_importance = float(card_importance)
    if card_fields.get("affect_valence") == "friction":
        base_importance = min(1.0, base_importance + 0.05)  # Kensinger
    mem_importance = min(1.0, base_importance)
else:
    mem_importance = min(0.5 + importance_boost, 1.0)
```

- **CRITICAL Kensinger double-apply**: Synthesis prompt Rule 3 already says "bump +0.05 if user_affect_valence is friction" — applied INSIDE Stage 1.5 LLM, baked into card.importance. If consolidator ALSO bumps, double-apply.
- **NEW SCOPE — retrieval.py**: `affect_score` in `_last_hybrid_candidates` always 0.0 (placeholder). Without retrieval-side affect boost from `Memory.affect_valence`, the Kensinger bump on importance doesn't affect RRF rank since RRF only consumes FTS+vector ranks. ~15 lines in `_crystallized_hybrid()`.
- **Convention**: `affect_valence` default to `"neutral"` not NULL at every `Memory.create()` with source card.

**Wave grouping**: Wave A (#32 consolidator + retrieval), Wave B (#34), Wave C (#29, #33).

**Top loss**: consolidator importance write throws away card's correctly-boosted importance.

---

## Wave 2: Debate and Convergence

### Linnea Nguyen (revised)

**Strongest objections:**
- *Marcus #32*: `max(importance_boost, 0.05)` operates on the wrong variable — leaves the throw-away bug intact. Persisted value still `min(0.5 + max(boost, 0.05), 1.0) ≈ 0.55`. Not a fix, noise around the wrong anchor.
- *Ines #32 + retrieval*: `decision.get("importance")` with no clamp before bump = silent corruption path if LLM emits 1.2 or string. Bundling retrieval.py into the same wave mixes write-path bug with read-path hot-path change — different blast radii.

**Concessions:**
- Ines correct on core diagnosis — consolidator throws away synthesized importance. Adopts Ines's fix shape with a guard.
- Marcus correct on schema gap and sequencing.

**Revised position:**

```python
card_importance = decision.get("importance") if is_card else None
if card_importance is not None:
    try:
        base_importance = max(0.0, min(1.0, float(card_importance)))  # clamp
    except (TypeError, ValueError):
        base_importance = 0.5 + importance_boost  # fallback + warn
    if card_fields.get("affect_valence") == "friction":
        base_importance = min(1.0, base_importance + 0.05)
    mem_importance = base_importance
else:
    mem_importance = min(0.5 + importance_boost, 1.0)
```

- **Kensinger resolution**: Remove bump from synthesis prompt Rule 3 entirely. Consolidator becomes sole application site. Persistence policy, not inference instruction. Ships as one-line prompt edit with consolidator fix to avoid split-deployment double-apply.
- **Retrieval.py**: Defer to Wave D. Separate failure mode, separate ticket.

### Marcus Tran (revised)

**Strongest objections:**
- *Linnea #29 validator*: Two demotion paths desync — strip-without-demotion in synthesizer, demotion-without-strip in validator. Validator fires too early.
- *Ines #32 passthrough*: Silent trust in LLM rule compliance. If model under-applied or skipped Kensinger bump, consolidator silently accepts the wrong number.

**Concessions:**
- Linnea: `card_validators.py` is the established pattern; instruction overload argument valid; schema migration sequencing maps cleanly.
- Ines: Kensinger double-apply correct in principle if rule 3 reliably fires. Retrieval `affect_score = 0.0` real correctness gap — writing affect_valence without read path is dead weight on facts.

**Revised position:**
- **#29**: Compromise — validator DETECTS (in `card_validators.py`); synthesizer ACTS (demotion at existing strip site). Preserves convention + co-locates operations.
- **#32**: Use card.importance as ground truth (Ines) BUT keep mechanical floor as guard against LLM non-compliance. Flagged as unresolved (D1).
- **Retrieval read-path**: Out of tier3 scope. Wave 4 / dedicated track.

### Ines Rivera (revised)

**Strongest objections:**
- *Linnea late-wave #32*: Schema gap (Marcus's #36 finding) doesn't affect #32 — `importance` IS in schema. Deferring #32 = ongoing data corruption across all consolidation runs until Wave C ships.
- *Marcus max() formulation*: `importance_boost` and `card.importance` are different scalars. `max(importance_boost, 0.05)` ignores `card.importance` entirely → applies Kensinger ZERO times at DB write, not twice. Marcus's fix solves a smaller problem.

**Concessions:**
- Marcus on schema: criterion_weights/rejected_options silently dropped is real. Schema migration must precede #36 consolidator wiring. Was not treating this as sequencing constraint; now is.
- Linnea on #30 + #33: defers, no objection.

**Holds:**
- #32 belongs in Wave A — schema gap doesn't apply, no schema blocker. Pattern-matching #32 to consolidator schema work is wrong; they're independent fields.
- card.importance replacement (not max() patch). Marcus's formulation applies Kensinger zero times at write site.
- Retrieval.py paired with #32 as read-side complement. Accepts Wave B compromise; rejects Wave C/D deferral.

---

## Consensus

**Universal agreement:**
- `#32` reconciliation site = `consolidator._execute_keep()`
- Schema migration (Marcus's `criterion_weights` / `rejected_options` blocker) precedes `#36` wiring
- `card_validators.py` is the established pattern for `#29`
- No new numbered prompt rules; `#30` = consolidating sentence reframe only
- `SESSION_TYPE_GUIDANCE` dict + template var for `#33`
- Sub-rule under existing `SKIP_PROTOCOL` for `#34`

**Resolved disagreements (majority/synthesis):**
- `#32` fix = Ines's `card.importance` + Linnea's clamp guard + Marcus's mechanical floor as fallback
- `#29` demotion = Marcus's compromise (validator detects in `card_validators.py`, synthesizer acts at strip site)
- Retrieval read-path deferred to Wave D (Linnea + Marcus 2-vs-1)

## Unresolved Disagreements

**D1 — Kensinger application location** (high impact)
- A: Keep prompt Rule 3 + add consolidator bump (Marcus original) → double-apply risk
- B: Trust `card.importance` only, no consolidator bump (Ines pure) → silent failure if LLM skips rule
- **C: Remove prompt Rule 3, consolidator sole site (Linnea + adopted by Marcus)** → single responsibility, ships as coordinated prompt+code change

**D2 — `#32` wave position** (timing)
- Wave A (Ines, data-corruption urgency)
- Wave B (Linnea, after schema migration)
- Wave C (Marcus revised)

**D3 — `affect_valence` null convention**
- Default `"neutral"` (Ines) at every `Memory.create()` with source card
- Allow nullable for non-card memories (default codebase pattern)

**D4 — `#29` fallback on malformed LLM importance**
- Clamp + log warning + fallback to old path (Linnea)
- Hard-fail (alternative)
