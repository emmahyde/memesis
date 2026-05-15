# F3 — Stable Observation Key via `obs_ids` Audit

**Verdict: CONCERN**

The ordinal-key mechanism is structurally sound but carries two latent defects: a DB-vs-memory ordinal skew (cosmetic/audit risk) and a fully-silent unknown-ordinal drop path with no downstream safeguard against double-counting in the multi-id case.

---

## 1. End-to-End Key Contract

The numbered pipeline is:

1. `sort_by_salience(filtered_content)` at consolidator.py:118 — post-sort content is assigned to `filtered_content`.
2. `_record_observations(filtered_content=filtered_content, ...)` at consolidator.py:119-124 — iterates `filtered_parts` in order, assigns `ordinal: index + 1` to each ref dict (consolidator.py:380).
3. `_inject_observation_ids(filtered_content, observation_refs)` at consolidator.py:140 — also splits `filtered_content` and numbers blocks `i + 1` (consolidator.py:413).

Both `_record_observations` and `_inject_observation_ids` receive the same `filtered_content` string, both split it with `_split_observation_blocks`, and both enumerate from zero then add 1. Because they operate on the same string in the same call frame, the ordinal-to-block mapping is consistent. **The alignment guarantee is: same input string, same splitter, independent enumeration that produces identical ordinals.**

This holds as long as `_split_observation_blocks` is deterministic (no internal sorting or randomization). No evidence of non-determinism was observed.

---

## 2. Off-by-One

**DB column vs. in-memory ref dict are intentionally skewed.** `Observation.create(ordinal=index, ...)` at consolidator.py:369 stores 0-indexed ordinals in the database. The ref dict at consolidator.py:380 stores `ordinal: index + 1` (1-indexed). The LLM is told to echo `OBSERVATION_ID: N` where N is 1-indexed (consolidator.py:413).

`_refs_for_obs_ids` at consolidator.py:396 builds `ordinal_to_id` from the ref dict's `ordinal` field, which is already 1-indexed. So the lookup is internally consistent.

**Concern:** the DB row's `ordinal` column is 0-indexed while every other component uses 1-indexed. This does not produce a functional bug today — `_refs_for_obs_ids` ignores the DB column entirely — but any future query that joins on `Observation.ordinal` to reconstruct pairing (e.g., in a backfill script or diagnostic query) will get ordinals that are off by one relative to the prompt and LLM response. This is a latent audit/tooling hazard. No test guards the DB column value.

---

## 3. Multi-ID Decisions: `obs_ids: [1, 3, 5]`

`_refs_for_obs_ids` at consolidator.py:387-397 returns a flat list of all resolved DB row IDs. For `obs_ids: [1, 3, 5]`, this produces `[id_of_obs1, id_of_obs3, id_of_obs5]`.

For `action=keep` at consolidator.py:200-218: `_execute_keep` is called once, producing a single memory. `_mark_observations(refs, "kept", memory_id)` then marks all three observation rows as `"kept"` pointing to the same memory ID. This is correct behavior — three source observations collapsed into one memory — and the orphan sweep at consolidator.py:315-326 will see all three IDs in `touched_ids`, so none are orphaned.

No multi-ID bug exists in the current execution path. The one structural assumption baked in: a multi-id decision always produces a single output artifact. If a future action type is added that should produce N outputs for N obs_ids, this logic would need revision.

---

## 4. Fallback Path: Legacy `_refs_for_observation`

When `obs_ids` is empty or absent, consolidator.py:176-177 falls through to `_refs_for_observation(observation, observation_refs)`. This performs:

- Exact string match (`r["content"].strip() == normalized`) at consolidator.py:428-430.
- Substring match in either direction at consolidator.py:432-436, capped at 3 results.

**This path is silently activated whenever the LLM omits `obs_ids`.** The prompt at prompts.py:119-120 says echoing obs_ids "is required for accurate pairing," but this is advisory text to the LLM — it is not enforced by the schema. `obs_ids` defaults to `[]` in schemas.py:148, so a response that omits the field entirely is valid and routes silently to fragile text matching.

`_validate_decision_ids` at consolidator.py:701 does not check for missing or empty `obs_ids`. There is no logging at the point where the fallback is chosen (consolidator.py:176-177). An operator reading logs cannot distinguish a correctly-paired decision from one that fell back to substring matching.

---

## 5. Logging / Observability: Unknown Ordinals

`_refs_for_obs_ids` at consolidator.py:394 says "Unknown ordinals are silently skipped." The implementation at consolidator.py:397 confirms: `[ordinal_to_id[oid] for oid in obs_ids if oid in ordinal_to_id]`. An LLM response with `obs_ids: [99]` (out of range) produces an empty list with no log line.

The orphan sweep at consolidator.py:310-326 will eventually mark the unmatched observation as `"orphaned"` and log it — but the log message does not indicate whether the orphan was caused by a missing LLM decision, an unknown ordinal, or a fallback-match failure. The sweep fires after all decisions are processed, so the causal connection is lost.

**The gap:** when `_refs_for_obs_ids` returns `[]` due to an unknown ordinal, `decision["_observer_refs"]` is set to `[]`, the decision executes (a memory may be created), and the source observation is still orphaned. A memory is created with no audit linkage to the source observation.

---

## 6. Race / Re-Entry

`_processed_keys` at consolidator.py:191-194 is an instance-level set. If the same `Consolidator` instance processes the same session twice (e.g., retry after partial failure), the idempotency key suppresses duplicate decisions. However, `_record_observations` is not guarded by this check — calling `consolidate_session` twice on the same instance with the same ephemeral content will insert a second set of `Observation` rows with new IDs and new ordinals. The second run's refs will have different IDs than the first, so ordinals are consistent within each run but the DB accumulates duplicate rows.

The `mark_observations(captured_ids, "failed")` error handler at consolidator.py:150-157 only fires for LLM exceptions, not for pre-existing pending rows. A partial retry creates orphaned pending rows from the first attempt alongside the second attempt's rows.

---

## 7. Concrete Failure Mode Still Producing Orphans with F3

**Scenario:** LLM returns a valid decision with `obs_ids: [2]` for a session that had 3 observations. Ordinal 2 is correct, but `_inject_observation_ids` and `_record_observations` both use `_split_observation_blocks`. If the ephemeral content has a block that contains the separator pattern used by `_split_observation_blocks` embedded inside it (e.g., an observation that quotes a `---` separator line from a prior session transcript), the block count seen by `_inject_observation_ids` differs from the count produced during `_record_observations`. The ref dict is built from `filtered_parts` at consolidator.py:358-359; the numbered content is built from `blocks = _split_observation_blocks(content)` at consolidator.py:406. If these two calls produce different split counts due to content ambiguity, ordinal 2 in the prompt maps to a different block than ordinal 2 in the ref dict. The LLM's `obs_ids: [2]` resolves to the wrong observation row, and the actual observation the LLM described is orphaned. No validation catches this cross-split skew.
