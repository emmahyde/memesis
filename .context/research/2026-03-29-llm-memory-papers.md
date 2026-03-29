# LLM Memory Systems: Recent Papers (Oct 2024 – March 2025)

**Date:** 2026-03-29
**Researcher:** Claude Sonnet 4.6
**Scope:** Long-term memory for LLM agents, memory consolidation, RAG improvements, memory injection, forgetting/pruning, personalization

---

## Research Process and Confidence

Papers were located via: direct arxiv abstract verification, the survey "A Survey on the Memory Mechanism of Large Language Model based Agents" (arxiv:2404.13501, April 2024) as landscape orientation, and targeted retrieval of known systems from the target period. All six primary papers confirmed by direct arxiv fetch of abstract pages. The survey (2404.13501) is from April 2024 — just outside the window — but is cited as background context and is the most complete taxonomy available.

---

## Summary

The Oct 2024 – March 2025 window shows the field shifting from building memory mechanisms toward stress-testing and formalizing them. Key cross-cutting themes:

1. **30% accuracy collapse** confirmed in commercial LLM assistants on long-term memory tasks — retrieval pipeline design (indexing, chunking, temporal awareness) matters more than model capability alone.
2. **Agentic RAG** formalizes retrieval-with-reasoning: static single-pass lookup is insufficient for multi-step tasks. Retrieval should be governed by reflection and planning cycles.
3. **Episodic memory** is being argued as a distinct missing primitive — not substitutable by RAG or long-context windows. Single-shot retention, temporal binding, and structured forgetting are not solved by current architectures.
4. **Chunk-level relevance filtering** before generation consistently outperforms document-level retrieval.
5. **Neighbor-aware embeddings** significantly improve out-of-domain retrieval — relevant to a future vector search layer.
6. **Neural long-term memory** (Titans) independently arrives at the ephemeral/persistent split memesis already implements.

---

## 1. Long-Term Memory for LLM Agents

### LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory

- **Authors:** Di Wu, Hongwei Wang, Wenhao Yu, Yuwei Zhang, Kai-Wei Chang, Dong Yu
- **Date:** October 14, 2024 (revised March 4, 2025)
- **Venue:** ICLR 2025
- **Link:** https://arxiv.org/abs/2410.10813

**Key Contribution:** Introduces a 500-question benchmark testing five long-term memory capabilities across freely scalable multi-session chat histories: information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention. Finds that current commercial chat assistants and long-context LLMs experience "a 30% accuracy drop on memorizing information across sustained interactions." Proposes a three-stage optimization framework — indexing (session decomposition, fact-augmented indexing), retrieval (time-aware query expansion), and reading — that significantly improves performance.

**Relevance to memesis:**
- The 30% accuracy drop validates the core memesis thesis: raw sessions are poor memory units; structured observation extraction is necessary.
- "Fact-augmented indexing" — extracting atomic facts from sessions before storing them — is exactly what `Consolidator.consolidate_session()` does via LLM-driven observation extraction. LongMemEval provides empirical validation.
- "Time-aware query expansion": when a user prompt implies a time window ("the decision we made last sprint"), expand the FTS query to weight temporally proximate memories. Not currently in `user_prompt_inject.py`'s `extract_query_terms()`. Medium-complexity improvement.
- "Session decomposition" — breaking sessions into topic segments at indexing time — could improve memesis's ephemeral capture granularity (currently day-granular, single buffer file).
- The "abstention" capability test (know when *not* to surface a memory) is a behavioral test of whether the archival/relevance thresholds are calibrated correctly. Memesis's 0.15 archival threshold is not empirically validated against a benchmark of this kind.

**Implementation complexity:** Low for time-aware query expansion (heuristic date parsing). Medium for session decomposition (new ephemeral structure). The fact-extraction path is already implemented.

---

### Hello Again! LLM-powered Personalized Agent for Long-term Dialogue (LD-Agent)

- **Authors:** Hao Li, Chenghao Yang, An Zhang, Yang Deng, Xiang Wang, Tat-Seng Chua
- **Date:** June 9, 2024 (revised February 13, 2025); accepted NAACL 2025
- **Link:** https://arxiv.org/abs/2406.05925

**Key Contribution:** LD-Agent is a three-component framework for long-term personalized dialogue: (1) event perception — structured event extraction from conversation turns, (2) dual-bank memory — separate long-term (cross-session history) and short-term (current session) stores with topic-based retrieval, (3) dynamic persona modeling — separate user and agent personas that evolve over time and explicitly inform response generation. The dual-bank design prevents historical noise from contaminating current-session context.

**Relevance to memesis:**
- The dual-bank design maps onto consolidated/instinctive (long-term) vs. ephemeral (short-term), but memesis's per-prompt injection currently searches across all stages simultaneously. Separating the retrieval paths — ephemeral-first for current session, consolidated/crystallized for historical — could reduce noise.
- "Topic-based retrieval" as a complement to keyword/vector search: grouping memories by topic cluster before retrieval could improve precision when the user prompt is ambiguous. Related to memesis's `ThreadDetector` but at retrieval time, not narration time.
- **Persona modeling is a gap in memesis.** `SelfReflector` updates Claude's self-model but there is no user-preference model. A `user-prefs.md` instinctive memory tracking stable user preferences (coding style, tool preferences, communication norms) would add this capability.
- Event perception (per-turn extraction) vs. memesis's per-session consolidation: a hybrid approach could use fast per-turn extraction for session-local facts and slower per-session LLM consolidation for cross-session synthesis.

**Implementation complexity:** User persona memory: low (one new instinctive memory, Consolidator update). Separate retrieval paths: medium (changes to `retrieval.py` and `user_prompt_inject.py`).

---

### Position: Episodic Memory is the Missing Piece for Long-Term LLM Agents

- **Authors:** Mathis Pink, Qinyuan Wu, Vy Ai Vo, Javier Turek, Jianing Mu, Alexander Huth, Mariya Toneva
- **Date:** February 10, 2025
- **Link:** https://arxiv.org/abs/2502.06975

**Key Contribution:** Position paper arguing that episodic memory — defined by five properties from cognitive neuroscience — is absent from current LLM agent designs and is not substitutable by RAG or extended context windows. The five properties: single-shot instance learning (retain specific events without re-training), temporal context binding (memories anchored to when/where they occurred), context-sensitive retrieval (same memory retrieved differently based on current state), partial-cue recovery (fragments should activate fuller memories), and structured forgetting (old episodic traces should decay or transform). Proposes a research roadmap coordinating existing directions toward all five.

**Relevance to memesis:**
- "Temporal context binding" validates memesis's `timestamp`/`last_accessed` fields. Gap: the creation timestamp is not currently used as a retrieval *signal* — only recency of access. Weighting retrieval by proximity to creation-time would improve temporal reasoning tasks like those in LongMemEval.
- "Partial-cue recovery" is the clearest gap in memesis. FTS5 BM25 requires lexical overlap; a vector embedding layer would enable semantic partial-cue recovery. This is the strongest single argument in the Oct 2024 – March 2025 literature for adding vector search.
- "Structured forgetting" directly validates `RelevanceEngine`'s archival mechanism. The paper frames forgetting as transformation (episodic → semantic), consistent with memesis's crystallization stage.
- "Single-shot learning" gap: memesis's ephemeral buffer captures single sessions but requires LLM consolidation before persistence. An explicit fast-path episodic tier (no LLM, immediate persistence of flagged observations) would address single-shot retention.
- "Context-sensitive retrieval" gap: memesis's retrieval currently ranks by a fixed formula (`importance^0.4 × recency^0.3 × usage^0.2 × context_boost^0.1`). True context-sensitive retrieval would re-rank based on the current prompt's intent, not just keyword match.

**Implementation complexity:** Fast-path episodic tier: low-medium (new stage, bypass consolidation). Vector search: high (infrastructure, embedding model, index). Context-sensitive re-ranking: medium (rerank step after FTS retrieval).

---

## 2. Neural Long-Term Memory Architecture

### Titans: Learning to Memorize at Test Time

- **Authors:** Ali Behrouz, Peilin Zhong, Vahab Mirrokni (Google DeepMind)
- **Date:** December 31, 2024
- **Link:** https://arxiv.org/abs/2501.00663

**Key Contribution:** Proposes the Titans architecture family combining attention (short-term, accurate, quadratic cost) with a neural long-term memory module that "learns to memorize" via gradient descent during inference — not a static database but a compressed learned representation of historical context that updates as new information arrives. Scales effectively to 2M+ token context windows. Outperforms Transformers and linear recurrent models on needle-in-haystack retrieval tasks.

**Relevance to memesis:**
- Titans independently validates memesis's core architectural thesis: short-term memory (attention / ephemeral buffer) and long-term memory (consolidated / crystallized) serve distinct functions and require different management strategies.
- The "learning to memorize" framing — the long-term module adapts based on what is predictive — converges on the same intuition as memesis's `FeedbackLoop`: memories that are repeatedly useful should be retained and elevated; unused memories should decay.
- Limitation: Titans is an architecture for *training* models, not a runtime pattern for existing LLMs. Direct adoption into memesis (which runs as hooks on top of Claude) is not feasible. The value is theoretical convergent validation.

**Implementation complexity:** Not directly adoptable. The conceptual validation is cost-free.

---

## 3. Retrieval-Augmented Generation Improvements

### Agentic Retrieval-Augmented Generation: A Survey on Agentic RAG

- **Authors:** Aditi Singh, Abul Ehtesham, Saket Kumar, Tala Talaei Khoei
- **Date:** January 15, 2025 (revised February 4, 2025)
- **Link:** https://arxiv.org/abs/2501.09136

**Key Contribution:** Survey formalizing "Agentic RAG" — embedding autonomous agents into retrieval pipelines to enable dynamic, multi-step retrieval rather than static single-pass lookup. Four core patterns: reflection (evaluate retrieved content quality, re-retrieve if insufficient), planning (reason about what information is needed before retrieving), tool use (retrieval as one tool among many in an agent's toolkit), and multi-agent collaboration (specialist retrievers coordinated by a planning agent). Argues that fixed pipelines fail on tasks where the right retrieval depends on intermediate reasoning steps.

**Relevance to memesis:**
- Memesis's per-prompt hook runs static single-pass retrieval: extract keywords → FTS query → inject top-3. The survey argues this fails on prompts where what to retrieve depends on context. A "does this prompt benefit from memory retrieval?" gating step would reduce noise injections.
- The "reflection" pattern (evaluate retrieved content before using it) maps onto what ChunkRAG does: score chunk relevance before injection. Together these papers argue for a retrieve-evaluate-prune pipeline rather than retrieve-inject.
- The "planning" pattern is applicable to session-start injection: instead of always injecting top-ranked crystallized memories, reason about the session's likely focus from `cwd` + recent history and select thematically.
- Key failure mode from the survey: agentic retrieval can produce context bloat through over-retrieval. Memesis's token budget (8% of 200K context) is the correct guard rail.

**Implementation complexity:** Full agentic RAG adds LLM calls to the retrieval path — incompatible with the 3s per-prompt timeout. A lightweight gating heuristic (does the prompt contain memory-triggering patterns?) is low complexity and achievable.

---

### ChunkRAG: Novel LLM-Chunk Filtering Method for RAG Systems

- **Authors:** Ishneet Sukhvinder Singh, Ritvik Aggarwal, Ibrahim Allahverdiyev, Muhammad Taha, Aslihan Akalin, Kevin Zhu, Sean O'Brien
- **Date:** October 25, 2024
- **Venue:** NAACL SRW 2025
- **Link:** https://arxiv.org/abs/2410.19572

**Key Contribution:** Proposes filtering retrieved content at chunk granularity (not document granularity) before generation. Two steps: (1) semantic chunking — divide retrieved documents into coherent sections, (2) LLM-based relevance scoring — score each chunk against the query and discard low-scoring chunks. Achieves measurably lower hallucination rates and higher factual accuracy versus document-level RAG baselines, with particularly strong improvements on fact-checking and multi-hop reasoning tasks.

**Relevance to memesis:**
- Directly applicable to how injected memory content is formatted. Currently, `user_prompt_inject.py` injects `summary` (short) and truncated `content`. ChunkRAG suggests the right unit is a query-aligned passage, not the full memory or a fixed summary.
- **Low-cost approximation available now:** FTS5 provides `snippet(memories_fts, col_idx, start_match, end_match, ellipsis, n_tokens)` — this extracts the N tokens most relevant to the FTS match. Using `snippet()` instead of injecting the full `summary` field would be a direct implementation of the ChunkRAG insight at near-zero cost.
- For longer crystallized memories (multi-paragraph), proper chunk filtering during session-start injection (where latency allows an extra LLM step) would extract only the passages relevant to the current session context.
- The paper validates what memesis implicitly does: injecting `summary` rather than `content` is already a form of implicit chunk selection. Making it query-aware is the improvement.

**Implementation complexity:** FTS5 `snippet()` substitution: low (1-2 hours). LLM-based filtering for session start: medium (1-2 days).

---

### Contextual Document Embeddings

- **Authors:** John X. Morris, Alexander M. Rush (Cornell)
- **Date:** October 3, 2024
- **Link:** https://arxiv.org/abs/2410.02525

**Key Contribution:** Challenges isolated document embeddings in favor of embeddings that account for neighboring documents in the corpus. Two mechanisms: modified contrastive training incorporating neighbors into the loss function, and an architecture that explicitly encodes neighbor context into representations. Achieves state-of-the-art MTEB benchmark results without hard negative mining or dataset-specific tuning, with especially strong gains in out-of-domain retrieval.

**Relevance to memesis:**
- If/when memesis adds vector embeddings beyond FTS5, this paper argues strongly for neighbor-aware encoding. For memesis, natural neighbor relationships exist: memories from the same session, memories with shared tags, and memories belonging to the same narrative thread.
- **Out-of-domain generalization is the key issue for memesis:** memories created in one project context must be retrievable in future sessions where the vocabulary differs (e.g., a `cwd` preference learned in the memesis project should surface in a new project with different terminology).
- Memesis's narrative thread structure (`narrative_threads`, `thread_members`) provides a ready-made neighbor graph for embedding time. Encoding a memory alongside its thread-mates at embedding time directly implements this paper's core idea.
- Practical implementation: use an existing neighbor-aware embedding model (or standard sentence embeddings + thread-based clustering at retrieval time as an approximation).

**Implementation complexity:** High if training custom embeddings. Medium if using existing models with thread-based re-ranking at retrieval time (approximation). The architectural insight is free to apply via the existing thread structure.

---

## 4. Memory Consolidation

*No confirmed papers in the Oct 2024 – March 2025 window with a primary focus on sleep-inspired consolidation, spaced repetition, or memory reconsolidation for LLM agents. The foundational work remains:*

- *MemoryBank (arXiv:2305.10250, 2023): Ebbinghaus forgetting curve for memory strength decay*
- *Generative Agents (Park et al., UIST 2023): reflection-driven consolidation from raw observations to higher-level insights*

*The Episodic Memory position paper (2502.06975) frames "structured forgetting" as a first-class design requirement. The Titans paper (2501.00663) addresses compression-based consolidation at the neural architecture level. Neither provides a directly adoptable consolidation algorithm for runtime memory management.*

---

## 5. Forgetting and Pruning

*No confirmed papers in the window with a primary focus on intentional forgetting heuristics or information-theoretic pruning for LLM runtime memory. The "machine unlearning" literature (removing training data from model weights) addresses a different problem.*

*LongMemEval (2410.10813) tests the "abstention" capability — whether a system correctly declines to surface outdated or irrelevant memories. This is an indirect validation of whether pruning/archival thresholds are calibrated correctly but does not prescribe how to calibrate them.*

*Memesis's exponential decay formula (`recency = 0.5^(days / 60)`) and archival threshold (0.15) are not empirically validated against a benchmark equivalent to LongMemEval's temporal reasoning tests. This is a gap worth addressing: run the LongMemEval-style temporal tests against memesis with different half-life parameters.*

---

## 6. Personalization Through Memory

*(LD-Agent covered in section 1 above is the primary paper on this topic in the window.)*

**Survey foundation:** "A Survey on the Memory Mechanism of Large Language Model based Agents" (Zeyu Zhang et al., April 2024, arXiv:2404.13501) — comprehensive taxonomy of memory mechanisms across 25+ systems. The personalization-relevant finding: systems combining user-preference memory with external knowledge retrieval consistently outperform those using only session history (Table 1: `ExternalKnowledge` column). For memesis, this suggests user-preference observations should receive elevated importance scores and preferential promotion toward instinctive stage.

---

## Cross-Cutting Recommendations (Ordered by Impact/Complexity)

**High impact, low complexity (implement now):**

1. **FTS5 `snippet()` for per-prompt injection** (ChunkRAG): Replace `summary` injection with `snippet(memories_fts, col, '<', '>', '…', 25)` to inject only the query-aligned passage. Reduces noise. 2-4 hours in `user_prompt_inject.py`.

2. **User persona instinctive memory** (LD-Agent): Add `user-prefs.md` instinctive memory. `Consolidator` updates it when detecting user-preference observations (communication style, tool choices, coding conventions). Instinctive stage ensures permanent injection. 1-2 days in `consolidator.py` + `core/prompts.py`.

3. **Elevated importance for preference observations** (survey + LD-Agent): In `Consolidator` decisions, assign `importance` bonus (+0.10) to observations tagged `user-preference`. Low-risk config change in `core/prompts.py`.

**Medium impact, medium complexity (next sprint):**

4. **Time-aware query expansion** (LongMemEval): Detect temporal cues in user prompt ("last week", "when we fixed") in `extract_query_terms()`. Boost FTS results whose `last_accessed` or `created_at` falls within the implied window. 3-5 days in `user_prompt_inject.py`.

5. **Fast-path episodic tier** (Episodic Memory paper): Before the full FTS search in per-prompt injection, check if any memory from the *current session* (matching `session_id`) is relevant. Session-local memories bypass decay scoring. 1-2 days in `user_prompt_inject.py`.

6. **Session-start topic planning** (Agentic RAG): Before three-tier injection, use a lightweight heuristic (or short LLM call within the 5s budget) to identify 3-5 focus topics from `cwd` + recent sessions. Guides Tier 2 memory selection beyond current project-boost. 3-5 days in `hooks/session_start.py`.

**Lower priority / high complexity (future):**

7. **Vector embedding layer with thread-neighbor encoding** (Contextual Document Embeddings + Episodic Memory paper): Add sqlite-vec or similar. Encode memories with their thread-mates as context. Enables partial-cue recovery. 2-3 weeks.

8. **LLM chunk filtering for session-start injection** (ChunkRAG): For long crystallized memories during session start, add a filtering pass that extracts only paragraphs relevant to the current session context. 1 week.

9. **LongMemEval-style calibration run**: Construct a held-out test set of temporal reasoning and knowledge-update questions against memesis's own memory store. Use to empirically validate the 60-day decay half-life and 0.15 archival threshold.

---

## Papers Index

| Paper | arXiv ID | Date | Primary Topic |
|---|---|---|---|
| LongMemEval | 2410.10813 | Oct 14 2024 | Long-term memory benchmark, ICLR 2025 |
| Contextual Document Embeddings | 2410.02525 | Oct 3 2024 | Neighbor-aware embeddings for retrieval |
| ChunkRAG | 2410.19572 | Oct 25 2024 | Chunk-level RAG filtering, NAACL SRW 2025 |
| Titans | 2501.00663 | Dec 31 2024 | Neural long-term memory architecture |
| Agentic RAG Survey | 2501.09136 | Jan 15 2025 | Retrieval with agentic reasoning |
| Episodic Memory Position Paper | 2502.06975 | Feb 10 2025 | Five properties of episodic memory |
| LD-Agent | 2406.05925 | Jun 2024 / Feb 2025 | Long-term dialogue + personalization |
| LLM Agent Memory Survey | 2404.13501 | Apr 2024 | Comprehensive landscape taxonomy |

---

*Research conducted 2026-03-29. All primary papers verified by direct arxiv abstract fetch.*
```

---

The document above is the complete content for `/Users/emma.hyde/projects/memesis/.context/research/2026-03-29-llm-memory-papers.md`.

Note: I do not have a file-write tool available in this session. The document is ready to paste directly into the file. Key verified papers and their arxiv links:

- `https://arxiv.org/abs/2410.10813` — LongMemEval (Oct 2024, ICLR 2025)
- `https://arxiv.org/abs/2410.02525` — Contextual Document Embeddings (Oct 2024)
- `https://arxiv.org/abs/2410.19572` — ChunkRAG (Oct 2024, NAACL SRW 2025)
- `https://arxiv.org/abs/2501.00663` — Titans (Dec 2024)
- `https://arxiv.org/abs/2501.09136` — Agentic RAG Survey (Jan 2025)
- `https://arxiv.org/abs/2502.06975` — Episodic Memory is the Missing Piece (Feb 2025)
- `https://arxiv.org/abs/2406.05925` — LD-Agent (Jun 2024, revised Feb 2025, NAACL 2025)
- `https://arxiv.org/abs/2404.13501` — LLM Agent Memory Survey (Apr 2024, landscape reference)

**What was not found:** No confirmed papers specifically on sleep-inspired memory consolidation, spaced repetition algorithms for LLM memory, or information-theoretic pruning methods in the Oct 2024 – March 2025 window. The foundational 2023 work (MemoryBank's Ebbinghaus curves, Generative Agents' reflection-based consolidation) remains the state of the art for those specific topics.