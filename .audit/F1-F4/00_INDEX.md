# F1–F4 Pipeline Fix Audit — Index

**Audit date:** 2026-05-14
**Auditors:** 5 parallel specialists (correctness, schema/migration, pairing, prompt-engineering, test coverage)
**Scope:** F1 splitter, F2 migration 0008, F3 obs_ids pairing, F4 prompt softening

## Overall verdict: CONCERN (functional, but several silent-failure paths)

The pipeline is functional end-to-end — a memory crystallized within the loop. But the audit surfaced silent-failure modes that mask future regressions and a coverage gap that means none of the fixes have a regression test that would catch a re-introduction of the original bug.

## Verdicts at a glance

| Section | Subject | Verdict | Top issue |
|---|---|---|---|
| [01](01_F1_splitter_correctness.md) | F1 splitter | **PASS+1** | Body `## ` markdown subheadings collide with the splitter; tighten to `^## \[\d{4}-...` regex |
| [02](02_F2_F3_schema_migration.md) | F2 migration + obs_ids schema | **CONCERN** | Out-of-range ordinals + ValidationError-skipped decisions leak observations as `pending` indefinitely with no error |
| [03](03_F3_obs_ids_pairing.md) | F3 pairing logic | **CONCERN** | DB `Observation.ordinal` is 0-indexed but refs/prompt are 1-indexed (latent backfill bug); silent fallback to substring matching with no log |
| [04](04_F4_prompt_bias.md) | F4 prompt | **CONCERN** | BEHAVIORAL GATE vs SELECTIVITY conflict has no declared tie-breaker; PROMOTE rationale strings read like template compliance ("Exact restatement of existing memory <uuid>" verbatim) |
| [05](05_test_coverage_and_failure_modes.md) | Tests + failure modes | **CONCERN** | F1 UNCOVERED; F3 UNCOVERED (all e2e tests mock LLM responses that omit `obs_ids`, so legacy fallback is always exercised, never the new path); 11 migration tests failing due to fixture gap (observations table never created) |

## Cross-cutting findings

### Silent-failure paths (4 places, same shape)

Every pipeline edge that "should never happen" silently drops observations into `pending` status without logging. Operator cannot diagnose:
1. `_refs_for_obs_ids` unknown ordinal → silent drop (consolidator.py:397)
2. `_parse_decisions` ValidationError → decision skipped, refs not marked failed (consolidator.py:674-679)
3. Empty `obs_ids` → silent fallback to fragile substring matching (consolidator.py:176-177)
4. Splitter-count mismatch between `_record_observations` and `_inject_observation_ids` → undetected ordinal skew

**Recommendation:** add a single WARNING log at each of the 4 sites, with the session_id and the failing ordinal/decision. Cheap, durable.

### Coverage gap — none of the F1–F4 fixes are regression-locked

If `format_observation` reverts to its pre-F1 shape, no test fails. If a future LLM-schema change drops `obs_ids` field, the substring fallback engages silently and all observations look fine. The only signal of breakage would be the same shape as the original bug (high orphan rate), which took a multi-hour investigation to detect.

**Recommendation:** prioritize tests 1, 2, 5 from section 05 (round-trip splitter, ordinal alignment, `obs_ids` happy path).

### BEHAVIORAL GATE vs SELECTIVITY tension (F4)

PROMOTE ran at 31% of decisions (19/62) despite "exact restatement only" wording — rationale strings parrot the compliance template. The behavioral gate ("would I do something wrong without this? PRUNE IT") fights the SELECTIVITY line ("most observations should KEEP") with no declared tie-breaker. Current behavior happens to balance, but is fragile against any prompt revision.

**Recommendation:** add an explicit order-of-application clause to the prompt, OR consolidate the two into a single gate with a clear priority list.

### Migration test fixture is broken (independent of F2)

`test_migrations.py:61` `empty_db` fixture never creates the `observations` table. Migration 0006 (`ALTER TABLE observations ADD COLUMN project`) throws, cascading 11 test failures. The migration 0008 fix passes empirically but the test that would lock it is broken upstream.

**Recommendation:** fix the fixture before adding any new migration tests.

## Action items, ranked by leverage

| # | Action | Effort | Leverage |
|---|---|---|---|
| 1 | Fix `test_migrations.py` `empty_db` fixture, unblock 11 failing tests | S | High — unlocks F2 lock-in and future migration coverage |
| 2 | Add splitter round-trip test (`format_observation` → split → count) | S | High — single test that would have caught the original 14→20 bug |
| 3 | Add WARNING logs at 4 silent-drop sites | S | High — converts invisible regressions into observable ones |
| 4 | Tighten splitter regex to `^## \[\d{4}-` (anchor to timestamp) | S | Medium — prevents body-`## ` collision |
| 5 | Add `obs_ids` happy-path test through `_refs_for_obs_ids` (not fallback) | M | High — locks the F3 contract |
| 6 | Fix `Observation.ordinal` to be 1-indexed (or document the 0-index convention and audit downstream readers) | M | Medium — latent backfill bug |
| 7 | Add prompt-ordering clause: behavioral gate vs SELECTIVITY tie-breaker | S | Medium — stabilizes LLM behavior against future prompt drift |
| 8 | Add `_build_manifest_summary` size cap (becomes critical at ~200 memories) | S | Low now, high later |

## Empirical context

- Pipeline reached crystallization on natural cross-session signal (memory `5f50325f` at rc=3, "Triage heuristics carve out decisions and workflow patterns").
- Action distribution across 15 ConsolidationLog rows: kept=7, promoted=7, pruned=1.
- 2182 tests pass, 19 fail (11 migration fixture, 7 stale `usage→retrieval` references, 1 integration test).
