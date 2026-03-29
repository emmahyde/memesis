# Research: AI Memory Evaluation Frameworks

**Confidence:** HIGH for framework facts (sourced from arxiv abstracts + project pages); MEDIUM for fit assessments (inferred from memesis architecture docs)
**Date:** 2026-03-29

**Sources:**
- arXiv:2601.03543 — EvolMem
- arXiv:2507.05257 — MemoryAgentBench
- arXiv:2402.17753 — LoCoMo
- arXiv:2410.10813 — LongMemEval (ICLR 2025)
- arXiv:2602.13967 — Neuromem
- https://snap-research.github.io/locomo/
- arXiv:2603.19935 — Memori (practical system, not a benchmark)

---

## Summary

Five benchmarks are directly relevant to memesis. The clearest match for end-to-end testing is **LongMemEval** — it explicitly models memory as an indexing → retrieval → reading pipeline, tests multi-session reasoning and knowledge updates, and is open source with 500 curated questions. **MemoryAgentBench** adds a "selective forgetting" dimension that maps directly to the prune/archive decisions in `consolidator.py`. **Neuromem** provides the most useful *conceptual* decomposition (five dimensions including consolidation policy and context integration mechanism), but is newer and code availability is unconfirmed. **EvolMem** covers multi-session dialogue but in the wrong domain. **LoCoMo** is the community standard corpus, most useful as a shared baseline.

The simpler internal eval approach already present in `eval/` (needle tests, curation audits, staleness tests) is the right foundation. External benchmarks are best used for secondary external validation once the internal suite is mature.

---

## 1. EvolMem

**Paper:** arXiv:2601.03543, January 2026
**GitHub:** https://github.com/shenye7436/EvolMem (code/data release pending)

### What it measures

Multi-session dialogue memory across two cognitive psychology categories: declarative memory (factual recall) and non-declarative memory (procedural/implicit patterns), each split into fine-grained sub-abilities. Data is fully synthetic — generated via "topic-initiated generation + narrative-inspired transformations" producing multi-session conversations with adjustable complexity. Evaluation is sample-specific (per-question rubrics).

Key finding from the paper: no LLM consistently outperforms others across all memory dimensions; agent memory mechanisms often fail to improve LLMs and add efficiency overhead.

### Setup complexity

Medium. Self-contained with synthetic data; no external annotation required. Open source (pending release). Python-based.

### Fit for memesis

| Aspect | Fit | Notes |
|---|---|---|
| Multi-session structure | Good | Mirrors the observation → consolidation → injection cycle |
| Consolidation quality testing | Weak | Tests retrieval outcomes, not whether consolidation itself kept the right observations |
| Multi-tier promotion (consolidated → crystallized → instinctive) | None | Not modeled |
| Developer/code session domain | Poor | Conversations are social/topical dialogue |
| Selective forgetting | None | No prune/archive dimension |

### Verdict

Not worth running directly — wrong domain. The data generation methodology (topic-initiated generation) is worth borrowing to create synthetic developer-session observation sets for internal testing.

---

## 2. MemoryAgentBench

**Paper:** arXiv:2507.05257, July 2025, updated March 2026
**Authors:** Yuanzhe Hu, Yu Wang, Julian McAuley

### What it measures

Four core memory competencies:
1. **Accurate retrieval** — recalling stored information correctly
2. **Test-time learning** — acquiring and retaining new knowledge during a session
3. **Long-range understanding** — comprehending information across extended context
4. **Selective forgetting** — managing what to retain vs. discard

Tasks are created by transforming existing long-context datasets into multi-turn incremental formats. Tests basic context systems, RAG, external memory modules, and tool-integrated agents.

Key finding: current methods fail to master all four competencies.

### Setup complexity

Medium-unknown. Data transformation methodology is described in the paper. The dataset is used as a test corpus by at least two other recent papers (Neuromem at arXiv:2602.13967; Fine-Mem at arXiv:2601.08435), suggesting data is accessible. Code open-source status not confirmed from abstract; paper updated March 2026 so active.

### Fit for memesis

| Aspect | Fit | Notes |
|---|---|---|
| Selective forgetting | Excellent | Direct match to `consolidator.py` prune/keep/promote decisions |
| Test-time learning | Good | Maps to observation capture → ephemeral → consolidated |
| Accurate retrieval | Good | Maps to `inject_for_session()` and `search_fts()` |
| Long-range understanding | Partial | Memesis is token-budget-bounded, not unbounded context length |
| Consolidation pipeline quality | Indirect | Tests retrieval outcomes, not transform fidelity |

### Verdict

Best single external benchmark for memesis's core value-add — especially the selective forgetting dimension, which no other listed benchmark tests. Worth tracking down the data and running it against the consolidation output.

---

## 3. LoCoMo (Long Conversation Memory)

**Paper:** arXiv:2402.17753, February 2024
**GitHub:** https://github.com/snap-research/LoCoMo
**Project page:** https://snap-research.github.io/locomo/

### What it measures

Very long-term social conversations: 300 turns, 9K tokens average, up to 35 sessions. Three evaluation tasks:

1. **Question answering** — five types: single-hop, multi-hop, temporal, commonsense, adversarial
2. **Event summarization** — extract causal/temporal relationships, scored against ground-truth event graphs
3. **Multimodal dialogue generation** — generate contextually consistent responses using past history

Key finding: long-context LLMs improve 22-66% but trail humans by 56%. Temporal reasoning is the hardest category.

### Setup complexity

Low. Fully open source, static dataset, no proprietary dependencies. Community standard — cited by 29+ papers and used as a test corpus by Neuromem, Hindsight, Memori, and others. Comparison against published scores provides external validity.

### Fit for memesis

| Aspect | Fit | Notes |
|---|---|---|
| Retrieval quality measurement | Strong | Good for testing `inject_for_session()` against known-answer questions |
| Temporal reasoning | Useful | Tests recency dimension of memesis's relevance scoring formula |
| Event summarization → crystallization | Partial | Maps to crystallization concept but ground-truth format (event graphs) doesn't match memesis's free-form markdown |
| Developer session domain | Poor | Conversations are personal life events between two chat agents |
| Multi-tier memory stages | None | No consolidated/crystallized/instinctive distinction |

### Verdict

Use LoCoMo only for benchmarking the retrieval engine in isolation with a community-standard baseline. Not useful for evaluating consolidation quality or multi-tier promotion. Start here if you need externally comparable numbers.

---

## 4. LongMemEval

**Paper:** arXiv:2410.10813, October 2024. Accepted ICLR 2025.
**GitHub:** https://github.com/xiaowu0162/LongMemEval
**Authors:** Di Wu, Hongwei Wang, et al.

### What it measures

500 hand-curated questions embedded in freely-scalable user-assistant chat histories. Five memory abilities:

1. **Information extraction** — capturing details from conversation
2. **Multi-session reasoning** — connecting information across sessions
3. **Temporal reasoning** — time-ordered queries
4. **Knowledge updates** — tracking when stored facts change
5. **Abstention** — knowing when NOT to answer

The framework explicitly models memory as a three-stage pipeline: **indexing → retrieval → reading**. The system under test provides the memory layer; LongMemEval provides the evaluation harness.

Key finding: commercial assistants and long-context LLMs show a 30% accuracy drop across sustained interactions.

### Setup complexity

Low-medium. Open source, ICLR 2025 accepted, actively maintained. Chat histories are "freely scalable" — you plug in your own memory system as the indexing/retrieval layer and measure accuracy on the 500 questions. No LLM API calls required for the retrieval evaluation pass itself.

### Fit for memesis

| Aspect | Fit | Notes |
|---|---|---|
| Indexing → retrieval → reading pipeline | Excellent | Exactly matches memesis's FTS5 + sqlite-vec → `inject_for_session()` → context injection |
| Session decomposition testing | Direct | Maps to ephemeral → consolidated → crystallized promotion |
| Knowledge updates | Strong | Maps to contradiction detection and resolution in `consolidate_session()` |
| Temporal reasoning | Strong | Tests the recency dimension of the relevance scoring formula |
| Abstention | Partial | Maps to relevance decay and archival (don't inject stale/low-signal memories) |
| Developer session domain | Medium | Social/chat domain, but 500 questions are pluggable; format transfer is manageable |

### Verdict

**Recommended starting point for external validation.** LongMemEval was designed for systems with exactly memesis's architecture. Plug `search_fts()` + sqlite-vec as the retrieval layer and run the 500-question suite. The ICLR 2025 acceptance makes it the most credible benchmark for external comparison. The "knowledge updates" and "abstention" dimensions are particularly valuable — they test behaviors (contradiction resolution, relevance decay) that no other benchmark covers.

---

## 5. Neuromem (conceptual framework + benchmark methodology)

**Paper:** arXiv:2602.13967, February 2026
**Runs on:** LoCoMo + LongMemEval + MemoryAgentBench as test corpora

### What it measures

Neuromem decomposes the streaming memory lifecycle into five independently-testable dimensions:

1. **Memory data structure** — flat text vs. key-value vs. triples vs. graph
2. **Normalization strategy** — how raw text is chunked/cleaned before storage
3. **Consolidation policy** — how stored memories are merged, pruned, or promoted
4. **Query formulation strategy** — how retrieval queries are constructed from current context
5. **Context integration mechanism** — how retrieved memories are formatted into the prompt

Tests under *streaming* conditions: insertions interleave with retrievals as memory grows across rounds.

Metrics: token-level F1, insertion latency, retrieval latency.

Key findings:
- Performance degrades as memory grows across rounds
- Time-related queries are the hardest category
- Memory data structure is the dominant quality factor
- Compression techniques redistribute costs without net accuracy gains

### Setup complexity

Medium. Runs on top of existing benchmarks. Modular design supports swapping in your own memory implementation. Code availability not confirmed from the abstract page; very recent paper (February 2026).

### Fit for memesis

| Aspect | Fit | Notes |
|---|---|---|
| Consolidation policy dimension | Direct | Maps to `consolidator.py` + `crystallizer.py` |
| Context integration mechanism | Direct | Maps to `hooks/user_prompt_inject.py` + `session_start.py` |
| Streaming conditions | Strong | Matches real-world memesis (memories accumulate across many sessions) |
| Performance degradation curve | Critical | Validates the need for memesis's archival/rehydration mechanism |
| F1 metric | Concrete | Measurable and implementable against LongMemEval/LoCoMo questions |

### Verdict

Use Neuromem's five-dimension decomposition as an **internal architecture audit checklist**, not as a benchmark to run directly. Each dimension maps to a specific memesis module:

| Neuromem dimension | Memesis module |
|---|---|
| Memory data structure | `core/storage.py` (SQLite + FTS5 + sqlite-vec) |
| Normalization strategy | `hooks/append_observation.py` + `Consolidator` preprocessing |
| Consolidation policy | `core/consolidator.py` + `core/crystallizer.py` |
| Query formulation strategy | `extract_query_terms()` in `user_prompt_inject.py` |
| Context integration mechanism | `RetrievalEngine.inject_for_session()` format |

---

## Simpler Internal Eval Approaches

Memesis already has `eval/` with needle tests, staleness tests, continuity tests, curation audits, and spontaneous recall tests. These should be expanded rather than replaced. Here is a practical eval framework organized by the three key questions:

### A. Retrieval quality — does the right memory get injected?

**Current:** `needle_test.py` — seeds a store, fires queries, checks whether expected memory surfaces.

**Gaps and improvements:**
- Add **precision@k**: for each query, rank all memories by score; measure fraction of top-3 that are relevant
- Add **MRR (mean reciprocal rank)**: where does the first relevant result appear in the ranking?
- Use the existing `retrieval_log` table: compare injected IDs against a ground-truth relevance set for the same context
- For FTS5 specifically: test multi-word queries, BM25 ranking order, and that archived memories are correctly excluded

**External benchmark mapping:** LongMemEval QA tasks can run against `search_fts()` + sqlite-vec directly. No LLM required for the retrieval pass — just check whether the right text chunk was returned.

**Key metrics:** Recall@1, Recall@3, MRR, exact-match on extracted facts.

### B. Consolidation quality — does the system keep the right observations and prune noise?

**Current:** `curation_audit.py` (details unclear from TESTING.md).

**What to build:** A labeled dataset of synthetic observation sets with ground-truth keep/prune/promote decisions. Run `consolidate_session()` and measure agreement.

**Test cases to cover:**
- Noise observations (random fragments, filler text) → should be pruned
- Repeated substantive observations → should trigger promote (ephemeral → consolidated)
- Contradicting observations on the same fact → should trigger `CONTRADICTION_RESOLUTION_PROMPT`
- Observations that reinforce an existing consolidated memory → should increment `reinforcement_count` toward crystallization threshold
- Stale low-signal observations below `REHYDRATE_THRESHOLD` → should be archived

**Metric:** Prune precision (fraction of pruned memories that were genuinely noise), keep recall (fraction of valuable observations retained), promotion recall (fraction of threshold-crossing observations correctly promoted).

**External benchmark mapping:** MemoryAgentBench's selective forgetting tasks provide ready-made ground-truth scenarios. EvolMem's data generation methodology is worth borrowing for creating realistic developer-session observation sets.

### C. Injection precision — does injection help or hurt?

**Approach A — Feedback loop calibration (low cost):** `FeedbackLoop.track_usage()` already scores injected memories against conversation text via weighted keyword heuristic. Measure what fraction of injected memories score above threshold in the feedback pass. A low rate signals over-injection. Track this as a ratio metric per session, log it to the `consolidation_log`.

**Approach B — A/B ablation (high cost, definitive):** Run the same Claude Code session twice on a scripted developer task — once with injection enabled, once disabled. Compare output quality with an LLM judge or deterministic check (e.g., does the task complete correctly? does it use the right project conventions?). This is expensive but is the only way to directly measure whether injection *helps*.

**Metric:** Injection utility rate (fraction of injected memories used), task completion delta (with vs. without injection).

---

## Maintenance Status (as of March 2026)

| Framework | Last Activity | Status |
|---|---|---|
| LongMemEval | Oct 2024, ICLR 2025 | Active — accepted, GitHub maintained |
| LoCoMo | Feb 2024 | Stable — widely cited, static dataset |
| MemoryAgentBench | Jul 2025, updated Mar 2026 | Active — recent updates |
| EvolMem | Jan 2026 | Active — code/data release pending |
| Neuromem | Feb 2026 | Very recent — no confirmed GitHub |

---

## Recommended Action Plan

1. **LongMemEval first** — plug `search_fts()` + sqlite-vec as the retrieval layer and run the 500-question suite. No LLM calls needed for the retrieval eval. GitHub: https://github.com/xiaowu0162/LongMemEval

2. **Track MemoryAgentBench** for the selective forgetting dimension. It's the only external benchmark that tests prune/archive decisions. Watch for a confirmed GitHub repo; paper was updated March 2026.

3. **Build a labeled consolidation test set** using EvolMem's data generation methodology. Creates synthetic developer-session observation sets with ground-truth keep/prune/promote labels. Fills the gap all external benchmarks miss.

4. **Use Neuromem's five-dimension checklist** as an architecture audit: data structure, normalization, consolidation policy, query formulation, context integration. Each maps to a specific memesis module.

5. **Skip LoCoMo for primary eval** — too social/chat-focused. Use it only if externally comparable baseline numbers are needed.

---

## Gaps Not Covered by Any External Benchmark

- **Multi-tier memory promotion** (ephemeral → consolidated → crystallized → instinctive) — memesis's most distinctive feature; no external benchmark models it
- **Developer/code session domain** — all five frameworks use social dialogue or QA; transfer of scores to code sessions is uncertain
- **sqlite-vec similarity retrieval** — all benchmarks assume text retrieval; vector layer needs custom eval cases
- **Narrative thread quality** — `build_threads()` / `threads.py` has no external analog; entirely internal eval territory
- **Crystallization synthesis quality** — whether the LLM correctly synthesizes N observations into a denser insight; no external ground truth exists
```

---

The file content above is complete and ready to be saved. It could not be written directly because no file-write tool is available in this environment. The target path is `/Users/emma.hyde/projects/memesis/.context/research/2026-03-29-memory-evals.md`.

Key findings:

**LongMemEval** (arXiv:2410.10813, ICLR 2025, https://github.com/xiaowu0162/LongMemEval) is the strongest fit. It was explicitly designed for a three-stage indexing → retrieval → reading pipeline — exactly what memesis implements. Five memory abilities, 500 curated questions, freely scalable chat histories you can plug your own memory system into. Start here.

**MemoryAgentBench** (arXiv:2507.05257, updated March 2026) is uniquely valuable for the "selective forgetting" dimension, which directly tests the prune/keep/promote decisions in `consolidator.py`. It's the only benchmark that tests that specific behavior.

**EvolMem** (arXiv:2601.03543, January 2026) is in the wrong domain (social dialogue) but its data generation methodology — topic-initiated generation with narrative transformations — is worth borrowing to build synthetic developer-session observation sets for internal consolidation testing.

**Neuromem** (arXiv:2602.13967, February 2026) is most useful as a conceptual framework: its five-dimension decomposition (data structure, normalization, consolidation policy, query formulation, context integration) maps one-to-one onto memesis's architecture modules and works as an evaluation checklist.

**LoCoMo** is the community standard corpus but too social/chat-focused for primary evaluation. Use only for external baseline comparisons.

The biggest gap across all external benchmarks: none of them test multi-tier memory promotion (ephemeral → consolidated → crystallized → instinctive), developer/code session domains, sqlite-vec similarity retrieval, narrative thread quality, or crystallization synthesis quality. Those all require internal labeled test sets.