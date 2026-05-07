# Deferred Compression Approaches

**Date:** 2026-05-07
**Source:** Research on Modern LLM Prompt Compression Techniques (23 sources)
**Context:** Applied to memesis memory lifecycle plugin

## Implemented

- **#2 Token Budget Benchmarking** — Eval suite at 4%, 2%, 1% budgets to find knee point
- **#4 Continuous Novelty Score** — SM-2 × habituation × recency as continuous relevance multiplier

## Deferred

### #1 Interleave Injection Ordering (Primacy/Recency Slots)

**Status:** DEFERRED — low value at current scale

**What:** Instead of pure importance-sort, reserve premium slots at top/bottom of injection block for highest-importance memories, interleave remaining memories to avoid a continuous band of low-importance content in the middle.

**Why deferred:** The "lost in the middle" research [Liu et al., TACL 2023] observed positional degradation with 20+ document contexts. Memesis injects 5-15 memories per session (~2K tokens). At that scale, the entire injection block fits in a single attention span — positional effects are negligible. The +21.4% gain from LongLLMLingua was on NaturalQuestions with hundreds of tokens of retrieved context, not a small memory block. This optimization targets a problem that doesn't exist at memesis's scale.

**Revisit if:** Injection block grows to 30+ memories or token budget exceeds 10K tokens.

**Code location:** `core/retrieval.py:742` (Thompson rerank after sort)

---

### #3 Extractive Key-Sentence Field on Memory

**Status:** DEFERRED — marginal value, schema cost not justified

**What:** Add a `key_sentence` TextField to Memory model. During consolidation, the LLM extracts one verbatim key sentence from the original observation alongside the summary. FTS5 matches the exact phrasing; semantic embedding matches the summary. Both paths contribute to RRF score.

**Why deferred:** Memesis already has two retrieval paths (FTS5 keyword + semantic embedding) over a small corpus (~100-1000 memories). At this scale, the existing paths already cover each other's blind spots. RECOMP's dual architecture [ICLR 2024] was designed for RAG systems retrieving from millions of documents where each path has significant blind spots. The schema migration cost (new column, prompt change, consolidation complexity) doesn't justify the marginal gain. The consolidation prompt change risks degrading summary quality from split attention.

**Revisit if:** Corpus grows to 10K+ memories, or FTS5 and semantic embedding show divergent recall patterns on the eval suite.

**Code location:** `core/consolidator.py:874` (`_format_markdown`), `core/prompts.py:111` (`CONSOLIDATION_PROMPT`)

---

### #5 Coherence → Compression Feedback Loop

**Status:** DEFERRED — high effort, premature

**What:** When the ghost coherence check flags a memory as contradictory, reduce compression aggression for that specific memory in the next injection cycle — preserve more content, add `[DISPUTED]` annotation. Feedback is per-memory, not global.

**Why deferred:** The coherence check itself isn't battle-tested (behind feature flag, rate-limited to once/day). Building a feedback loop on top of an unverified signal compounds uncertainty. If the coherence check produces false positives, the feedback loop would decompress and waste token budget on already-correct content. Multi-cycle latency (2+ sessions to resolve) makes debugging nearly impossible. The feature touches injection, reconsolidation, coherence check, and the Memory model (needs a `compression_confidence` field).

**Revisit if:** Ghost coherence check has been running for 30+ days with verified accuracy, and contradiction false-positive rate is below 5%.

**Code location:** `core/coherence.py:56` (ghost coherence check entry point)
