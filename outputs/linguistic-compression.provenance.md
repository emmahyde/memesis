# Provenance: linguistic-compression

- **Date:** 2026-05-07
- **Topic:** Linguistic Theory and Constructed Languages as Compression Systems
- **Research rounds:** 1 (user-provided comprehensive evidence table with 25 sources)
- **Researcher agents:** 0 (user supplied primary research; 1 explore agent for memesis architecture mapping)
- **Sources consulted:** 25
- **Sources in final:** 25
- **Verification:** PASS with notes (minor citation gaps, 2 unused sources — all fixed)
- **Review verdict:** REVISE → ACCEPT (3 FATAL issues identified and resolved, 8 MAJOR addressed, 9 MINOR noted)
- **Fatal issues:** 3 identified, 3 resolved (hard ceiling reframed as benchmark, UID/capacity decoupled, Wikipedia confidence downgraded)
- **Plan:** outputs/.plans/linguistic-compression.md
- **Research files:**
  - outputs/linguistic-compression.md (final brief)
  - outputs/linguistic-compression-draft.md (original draft)
  - outputs/linguistic-compression-verified.md (verification report)
  - outputs/linguistic-compression-review.md (adversarial review)

## Implementation Log

### Recommendations Evaluated

1. **Compression audit metric** — IMPLEMENTED
   - Added `compression_ratio` column to `ConsolidationLog` model
   - Added migration in `core/database.py`
   - Computed in `core/consolidator.py` as output_tokens / input_tokens
   - Tests pass (256/256)

2. **Coverage tracking for instinctive memories** — IMPLEMENTED
   - Added `get_instinctive_coverage()` method to `LifecycleManager`
   - Computes Zipf-style concentration curve: top N memories → % of sessions covered
   - Uses existing `RetrievalLog` data; no schema changes required
   - Tests pass (256/256)

3. **Disambiguation cost measurement** — SKIPPED (commented)
   - Added research-gap comment in `core/crystallizer.py`
   - Deferred until eval harness supports session-level outcome metrics
   - Theoretical framing documented (Toki Pona polysemy ≈ crystallization)

4. **Channel capacity awareness** — IMPLEMENTED (documented)
   - Added comprehensive comment block in `core/retrieval.py`
   - Connects 8% token budget to Coupé et al. ~39 bits/second universal rate
   - Documents Uniform Information Density hypothesis connection

### Code Changes

| File | Change | Lines |
|------|--------|-------|
| core/retrieval.py | Channel capacity documentation | +23 |
| core/crystallizer.py | Disambiguation cost research gap | +15 |
| core/lifecycle.py | Instinctive coverage method | +85 |
| core/models.py | compression_ratio column | +4 |
| core/database.py | Migration for compression_ratio | +1 |
| core/consolidator.py | Compression ratio computation | +12 |

## Connection to Memesis Architecture

The research revealed that Memesis already implements the core insights from linguistic compression theory:

- **Tiered injection** mirrors the ~39 bits/second channel capacity constraint
- **Instinctive memories** exploit Zipf concentration (small core does most work)
- **Crystallization** strips episodic detail like Toki Pona polysemy shifts cost to decoder
- **Rich metadata schema** (W2 taxonomy, Stage 1.5) is Memesis's morphological density (Ithkuil-style explicit marking)
- **SM-2 spaced repetition + Thompson sampling** implements Uniform Information Density (smooth surprisal)

The ambiguity-verbosity trade-off is the governing principle: Memesis compresses at multiple stages (transcript→ephemeral→consolidated→crystallized) with the understanding that each compression step shifts disambiguation cost to the retrieval/injection phase.
