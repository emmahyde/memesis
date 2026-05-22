---
title: Memory Kind Taxonomy
type: concept
tags: [memory_kind, taxonomy, classification, validators]
---

# Memory Kind Taxonomy

The curated enum stored in `memories.memory_kind`. Distinct from `kind` (the observation-extraction taxonomy in `KIND_VALUES`). Governs retrieval scoring and promotion behavior.

Defined in `core/validators.py:44` (`MEMORY_KIND_VALUES`). Enforced by DB triggers added in migration 0018 (`core/migrations/sql/20260517_0018_memory_kind_check.py`).

## Values

| Kind | Description |
|---|---|
| decision | Chose between alternatives; carries rationale and rejected options |
| lesson | Pattern extracted from ≥2 incidents; prescribes future behavior |
| gotcha | Trap that caused a real problem; concrete and reproducible |
| goal | North-star statement that shapes future decisions |
| invariant | Fragile coupling that future refactors must preserve |
| opinion | Stance on whether something is right or wrong, with rationale |
| bias | Systematic LLM or system failure mode (anti-checklist use) |
| todo | Action item with a concrete done-state predicate |
| debt | Known issue or cleanup with status tracking |
| fact | Small, code-derivable fact worth pinning (rare; most code facts are PRUNE) |

## NULL Semantics

`memory_kind = NULL` is valid and correct for:
- Ephemeral rows (not yet classified)
- `kind = 'open_question'` rows (lifecycle state, not a knowledge kind)

NULL is rejected at promotion from consolidated → crystallized unless the row is an open_question. (`core/lifecycle.py:287`)

## DB Enforcement

Migration 0018 adds two BEFORE-write triggers (`memory_kind_check_insert`, `memory_kind_check_update`) that `RAISE(ABORT)` when `memory_kind IS NOT NULL AND memory_kind NOT IN (...)`. SQLite has no `ALTER TABLE ADD CONSTRAINT`, so triggers are the enforcement mechanism. (`core/migrations/sql/20260517_0018_memory_kind_check.py`)

`hypothesis` is **not** in `MEMORY_KIND_VALUES` but is used internally by [[SelfReflector]] for accumulation rows. Hypothesis rows are ephemeral stage and do not reach the promotion trigger check until `can_promote_hypothesis` clears the kind to NULL.

## Relationship to Observation kind

`KIND_VALUES` (extraction taxonomy): `decision | finding | preference | constraint | correction | open_question`

`MEMORY_KIND_VALUES` (memory taxonomy): the 10-value curated enum above.

A deterministic map `_OBSERVATION_TO_MEMORY_KIND` in `core/validators.py:60` translates extraction kinds to memory kinds for the classification pass. `goal`, `bias`, `todo`, and `debt` are not directly derivable from the extraction taxonomy — they require a richer classification pass.

## Importance Default Bands

Kind interacts with the [[importance-rubric]] default band assignment:
- `correction`, `constraint` (extraction kinds) → default 0.85–1.00 band
- `decision`, `preference` → default 0.75–0.84 band

Memory kinds do not currently have their own band defaults in the consolidation prompt — the band defaults are keyed on extraction `kind`, not `memory_kind`.

## Related

- [[importance-rubric]] — kind-based default importance bands
- [[promotion-gates]] — memory_kind NULL blocks crystallization
- [[SelfReflector]] — uses hypothesis kind internally
