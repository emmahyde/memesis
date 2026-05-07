# Historical Compression Patterns Applied to LLM Memory: Steelmanned Analysis and Recommendations

## Executive Summary

This brief investigates which historical and human-cultural language compression techniques can be productively applied to LLM context/memory compression (specifically for the memesis plugin), and which cannot. The analysis steelmans each technique before identifying knock-down arguments.

**Key findings:**
1. **Three historical patterns map directly to LLM compression**: codebook compression (telegraphy/brevity codes), systematic deletion (headlinese), and vocabulary compression (pidgin-style semantic broadening). Two do not: shorthand (symbolic substitution) and SMS text-speak (ad hoc abbreviation).
2. **ML research confirms extractive compression outperforms token pruning and summarization** for most tasks, achieving up to 10× compression with minimal accuracy degradation [Characterizing Prompt Compression, 2024]. LLMLingua achieves up to 20× on reasoning tasks (GSM8K) but with measurable quality loss on harder tasks.
3. **The strongest applicable technique for memesis is headlinese-style systematic deletion** — grammatical omissions that are recoverable from context. This maps to memesis's existing caveman-style compression but can be made more systematic and principled.
4. **Codebook compression (telegraphy/brevity code pattern) is the second strongest** but requires a shared codebook between encoder and decoder. For LLM memory, this means defining domain-specific abbreviation schemes that the LLM can reliably decode — which is exactly what structured memory schemas do.
5. **Shorthand (symbolic substitution) is inapplicable**: LLMs don't read stroke-based symbols, and creating a novel symbolic system that an LLM decodes reliably has no empirical support and high failure risk.
6. **The empirical evidence shows moderate compression (2–5×) preserves quality well; aggressive compression (10–20×) degrades quality on reasoning tasks**. For memesis, targeting 2–4× compression for consolidated/crystallized memories is the evidence-backed sweet spot.

**Confidence level**: High for mapping analysis and failure modes; Moderate for specific compression ratio recommendations (empirical results vary by task type and model).

---

## 1. Mapping Historical Patterns to LLM Compression

### 1.1 The Pattern Taxonomy

Historical compression systems cluster into five mechanistic families:

| Pattern | Mechanism | Historical Examples | Compression Ratio | Decoding Cost |
|---------|-----------|---------------------|-----------------|---------------|
| **Symbolic substitution** | Replace alphabet with novel symbol system | Pitman, Gregg, Teeline shorthand | 4–10× throughput | Specialist training (months–years) |
| **Codebook lookup** | Map phrases to opaque tokens via shared dictionary | Phillips Code, commercial telegraphy, NATO brevity | 4–10× per phrase | Codebook required at both ends |
| **Systematic deletion** | Remove predictable/recoverable grammatical elements | Headlinese | 2.5–5× word count | Near-zero (literate readers) |
| **Ad hoc abbreviation** | Abbreviate individual words, blend consonant patterns | SMS text-speak, Twitter compression | 20–50% per abbreviated word | Near-zero (peer group) |
| **Vocabulary compression + semantic broadening** | Small word set covers large conceptual territory via circumlocution | Tok Pisin, Hawaiian Creole | 60:1 vocabulary; message tokens often *higher* | Months (L2); native (L1) |

### 1.2 Direct Applicability Assessment

#### PATTERN 1: Symbolic Substitution (Shorthand) → **NOT APPLICABLE**

**Steelman**: Shorthand achieves the highest throughput ratios (4–10×) and is near-universal in domain — it can capture any spoken content. If we could create a "shorthand for LLMs" — a compressed token representation that preserves all semantic content — we could achieve dramatic context window savings.

**Knock-down**: LLMs process token embeddings, not visual strokes. The shorthand advantage (physical writing speed) doesn't transfer to a token-processing system. Creating a novel symbolic system that LLMs reliably decode would require:
1. A stable mapping from natural language to compressed symbols
2. The LLM to reliably reconstruct full meaning from symbols
3. Both encoder and decoder to agree on the symbol set

This is essentially the problem of creating a new encoding scheme for text, which LLMs have no training data for. Empirical evidence from prompt compression research shows that maintaining natural language form (even heavily compressed) consistently outperforms symbolic schemes [Characterizing Prompt Compression, 2024; Li et al., 2025]. The "compress to gibberish tokens" approach fails because LLMs lose the distributional semantics they rely on for reconstruction.

**Verdict**: ❌ Not applicable. The core advantage (physical stroke efficiency) doesn't transfer, and the decoding reliability problem is unsolved.

#### PATTERN 2: Codebook Lookup (Telegraphy/Brevity Codes) → **APPLICABLE WITH CAVEATS**

**Steelman**: Telegraph codes achieved 4–10× compression per phrase by mapping predictable multi-word expressions to short tokens. NATO brevity codes achieve 6–10× per term. The key insight: **in constrained domains, most communication uses a finite set of recurrent phrases**. A shared codebook between encoder and decoder enables high compression with zero ambiguity.

For LLM memory, this maps to **structured memory schemas**: defining a vocabulary of typed, named fields that encode observations in a compressed format. Instead of storing "The user prefers Python over JavaScript for scripting tasks" (56 chars), store `pref_lang:py>js:scripting` (24 chars) — a 2.3× compression. The "codebook" is the schema definition.

Memesis already uses this pattern implicitly: observations have structured types (preference, pattern, correction, friction_signal) with typed fields. Expanding the schema vocabulary and enforcing compressed field values is a direct application of the telegraphy pattern.

**Caveats**:
- **Domain constraint**: Codebooks only work within their domain. Outside the pre-defined vocabulary, you must fall back to natural language. This is fine for memory (which has a bounded taxonomy of observation types) but limits compression to the predictable portions.
- **Codebook maintenance**: Telegraph codes required matching editions at both ends. For LLM memory, the "codebook" must be included in system context, partially offsetting compression gains. The break-even point depends on how many memories share the schema.
- **Novelty handling**: Brevity codes have no mechanism for novel situations. The memesis schema must include escape-hatch fields for observations that don't fit the codebook.

**Knock-down attempt**: The system context overhead for the codebook definition eats into compression gains. If a schema definition costs 200 tokens and saves 10 tokens per memory, you need >20 memories before break-even. This is easily achieved in practice (memesis consolidates hundreds of observations), but the overhead is real and must be measured.

**Verdict**: ✅ Applicable. Already partially in use. Best for the **predictable, recurrent** portion of memories (preferences, patterns, corrections). Not suitable for novel or complex observations.

#### PATTERN 3: Systematic Deletion (Headlinese) → **STRONGLY APPLICABLE**

**Steelman**: Headlinese achieves 2.5–5× compression by deleting grammatical elements that are predictable from context: articles, auxiliary verbs, copula, pronouns, conjunctions. Verbs shift to simple present. The result is fully recoverable by literate readers with near-zero training.

For LLM context compression, this is exactly what systems like caveman implement: drop articles, auxiliary verbs, filler phrases, and verbose connectors while preserving all semantic content. The ML evidence strongly supports this approach:

1. **Extractive compression outperforms all other approaches** including token pruning and abstractive summarization for most tasks, achieving up to 10× compression with minimal accuracy degradation [Characterizing Prompt Compression, 2024].
2. **LLMLingua's token-level iterative compression** (which deletes low-perplexity tokens — essentially "systematic deletion of predictable elements") achieves 5–20× compression with small quality loss on reasoning tasks [Jiang et al., 2023].
3. **Moderate compression can improve performance**: For long contexts, the Characterizing study found that moderate compression (2–5×) actually *improved* task performance by reducing noise [Characterizing, 2024]. This mirrors headlinese — removing redundant elements improves signal-to-noise ratio.

The headlinese pattern maps directly to memesis's consolidation stage: observations contain redundant grammatical structure that an LLM can reconstruct from context. Systematic deletion of predictable elements preserves meaning while reducing token count.

**Key insight from headlinese research**: The deletion patterns are *systematic and recoverable*. Halliday (1967) identified specific grammatical categories that can be deleted: articles, auxiliaries, copula, pronouns. Mardh (1980) and van Dijk (1988) showed these deletions are constrained by recoverability — readers can reconstruct what was deleted because the remaining elements carry enough context. LLMs have even stronger reconstruction ability than human readers because they model the full distributional statistics of language.

**Knock-down attempt**: Headlinese has documented ambiguity problems ("British Left Waffles on Falklands"). For LLM memory, ambiguous deletions could cause the LLM to reconstruct incorrect information. However, the empirical evidence suggests this risk is manageable: LLMLingua achieves good quality at 2–5× compression, and the 2025 survey found that all compression methods increase hallucination slightly, but systematic deletion methods increase it less than summarization methods [Li et al., 2025 NAACL survey].

**Verdict**: ✅✅ Strongly applicable. This is the highest-confidence, most evidence-backed pattern. Memesis should implement systematic deletion as the primary compression technique for consolidated and crystallized memories.

#### PATTERN 4: Ad Hoc Abbreviation (SMS/Text-Speak) → **WEAKLY APPLICABLE**

**Steelman**: SMS text-speak achieves 20–50% character reduction per abbreviated word with near-zero learning cost. The strategies are simple: vowel deletion ("dctnry"), acronym formation ("LOL"), and consonant compression ("kybrd"). These are low-effort, universally understood compression tactics.

**Knock-down**: The empirical evidence from the Twitter natural experiment is devastating for this pattern: when the 140→280 character expansion happened, only 9% of English tweets had been hitting the old cap, and compression behavior *reversed* when the constraint lifted [Chang et al., 2019]. This means:
1. Ad hoc abbreviation is **constraint-driven, not efficiency-driven** — people stop doing it when they don't have to.
2. The actual compression achieved is modest: <10–20% of messages used SMS abbreviations even at peak constraint [Crystal, 2008].
3. LLMs are not character-constrained in the same way — they process tokens, not characters. Vowel deletion in text doesn't reliably reduce token count (many "abbreviated" words tokenize identically or worse in BPE).

For memesis specifically: tokenizers like BPE/GPT already do subword tokenization, which means "abbreviated" forms may not save tokens. "Dictionary" and "dctnry" may tokenize to similar or identical token counts. The abbreviation savings are in character count, not token count.

**Verdict**: ❌ Weakly applicable at best. The compression mechanism (character-level abbreviation) doesn't reliably transfer to token-level compression. The behavior is constraint-driven, not efficiency-driven.

#### PATTERN 5: Vocabulary Compression + Semantic Broadening (Pidgin/Creole) → **PARTIALLY APPLICABLE — WITH MAJOR CAVEATS**

**Steelman**: Tok Pisin achieves a 60:1 vocabulary compression ratio by having a small set of words cover large conceptual territory, supplemented by circumlocution. The grammar is dramatically simplified (4 prepositions, no inflectional morphology). This is essentially what LLM memory schemas do: a small vocabulary of observation types (preference, pattern, correction, friction_signal) covers a large space of possible human behaviors.

**The critical distinction**: Pidgin compression is in vocabulary and morphology, not in message token count. A Tok Pisin speaker often uses *more tokens* than an English speaker to express the same concept ("skru bilong lek" = 3 tokens for "knee" = 1 token). The compression is in the *lexicon*, not the *message*.

For LLM memory, this maps to two separate techniques:
1. **Vocabulary compression** (applicable): Restrict memory content to a bounded schema vocabulary. This is already done in memesis (observation types, stage names, field names). Expanding this to include standardized value vocabularies (e.g., `lang:py` instead of "Python") is the telegraphy/codebook pattern.
2. **Circumlocution** (counter-productive): Using more tokens to express a concept that a single word could convey. This is the opposite of what memesis should do. Circumlocution *increases* token count, which defeats the purpose of context compression.

**Knock-down**: The pidgin pattern teaches us that vocabulary compression works, but the mechanism (semantic broadening + circumlocution) is exactly wrong for LLM context compression. We want *fewer* tokens per concept, not more. The pidgin insight is useful only insofar as it validates bounded-vocabulary schemas — the rest of the pattern (circumlocution, grammar loss) is counter-productive for an LLM that needs precise, unambiguous memory.

**Verdict**: ⚠️ Partially applicable. The vocabulary compression insight (bounded schema) is valid and already in use. The circumlocution and grammar loss aspects are counter-productive for LLM memory.

---

## 2. ML Research Evidence on Prompt Compression

### 2.1 Key Empirical Findings

**LLMLingua family** (Microsoft Research):
- LLMLingua (2023): Coarse-to-fine token-level compression using small LM perplexity. Up to 20× compression on GSM8K with 1.52-point EM drop. 5–7× on BBH with 8.5–13.2 point drops [Jiang et al., 2023].
- LongLLMLingua (2024): Question-aware compression for long contexts. Up to 21.4% performance improvement at 4× compression on NaturalQuestions. 94% cost reduction on LooGLE [Jiang et al., 2024].
- LLMLingua-2 (2024): Data distillation approach. Token classification (not perplexity-based). 3–6× faster than LLMLingua. Better compression rate adherence [Pan et al., 2024].

**Critical finding from empirical study** (Chang et al., 2024):
> "Surprisingly, we find that **extractive compression often outperforms all other approaches**, and enables up to 10× compression with minimal accuracy degradation. Interestingly, we also find that despite several recent claims, token pruning methods often lag behind extractive compression."

This validates the headlinese pattern: **extractive compression** (deleting predictable elements while keeping the rest verbatim) is more effective than **abstractive compression** (summarizing/rewriting) or **token pruning** (removing individual tokens based on perplexity).

**Data distribution matters** (2025 study):
- Input entropy negatively correlates with compression quality
- Decoder alignment is more important than encoder alignment
- The gap between training data distribution and compressed content distribution significantly impacts compression gains
- This means: **compressed content should match the LLM's expected distribution** — which headlinese-style systematic deletion achieves naturally (it produces natural language, just shorter).

**Compression and hallucination** (Li et al., 2025 NAACL survey):
> "All methods appeared to increase hallucinations, primarily due to information loss."

This is the fundamental trade-off: more compression = more information loss = more hallucination risk. The safe zone is 2–5×; beyond 10×, quality degrades significantly on reasoning tasks.

**End-to-end latency study** (2025):
- LLMLingua achieves up to 18% end-to-end speed-up (not the 5.7× claimed by compression ratio alone)
- LLMLingua-2 is 3–6× faster at compression than LLMLingua
- Maximum practical benefit occurs at >5K token prompts
- For shorter prompts, compression overhead exceeds decoding savings

### 2.2 Existing Implementations Relevant to Memesis

**Caveman plugin** (JuliusBrussee):
- Output compression: ~75% token reduction on LLM responses by "talking like caveman" (systematic deletion)
- Input compression (caveman-compress): ~46% average token reduction on CLAUDE.md files
- Validation: Preserves code blocks, URLs, file paths byte-for-byte
- Three compression levels: lite, full, ultra
- Key technique: **Headlinese-style systematic deletion** — drop articles, auxiliaries, filler phrases, merge redundant bullets, keep technical terms exact

**Cavemem** (JuliusBrussee):
- Persistent memory with compressed-at-rest storage
- Deterministic grammar for compression (~40–60% token reduction)
- Round-trip guarantee: compressed content can be expanded back to human-readable form
- Progressive MCP retrieval: agents pull only what they need
- Hybrid search: SQLite FTS5 (keyword) + local vector index (semantic)

---

## 3. Failure Mode Analysis

### 3.1 Failure Modes by Pattern

| Pattern | Failure Mode | Likelihood | Severity | Mitigation |
|---------|-------------|------------|----------|------------|
| **Systematic deletion** (headlinese) | Ambiguous reconstruction ("Left" = direction or political party) | Medium | Medium | Preserve disambiguating context; compress less aggressively for high-stakes content |
| **Systematic deletion** | Critical information deleted along with redundancy | Low-Medium | High | Content-aware deletion (keep technical terms, proper nouns, numbers, code) — exactly what caveman does |
| **Systematic deletion** | Compressed text falls outside LLM's training distribution | Low | Low | Headlinese is well-represented in training data (news headlines, technical docs, bullet points) |
| **Codebook compression** (telegraphy) | Schema vocabulary doesn't cover novel observations | High | Medium | Always include natural-language escape hatch for novel content |
| **Codebook compression** | Schema definition overhead exceeds savings | Low | Medium | Break-even at ~20 memories; memesis handles hundreds — overhead is amortized |
| **Codebook compression** | Schema version mismatch across sessions | Low | Low | Include schema version in system context; backward-compatible evolution |
| **Symbolic substitution** (shorthand) | LLM cannot reliably decode novel symbols | Very High | Very High | **Don't use this pattern** |
| **Ad hoc abbreviation** (SMS) | Tokenizer doesn't reduce token count for abbreviated words | High | Medium | Prefer systematic deletion over word-level abbreviation |
| **Vocabulary compression** (pidgin) | Circumlocution increases token count | High | High | Use bounded vocabulary for field values, not for content expression |

### 3.2 Cross-Cutting Risk: Hallucination Under Compression

All compression methods increase hallucination risk [Li et al., 2025]. The mechanism is information loss: when details are removed, the LLM fills gaps with plausible but ungrounded content.

**For memesis specifically**:
- **Consolidated memories** (stage 2): Moderate compression (2–3×) is safe — these are already summaries
- **Crystallized memories** (stage 3): Aggressive compression (3–5×) is acceptable — these are durable patterns where the LLM can reconstruct detail from the pattern schema
- **Instinctive memories** (stage 4): Maximum compression (5–10×) is possible — these are behavioral heuristics that need only trigger conditions, not detailed context
- **Never compress**: Raw observations (stage 1) — these contain the ground truth that later stages reference

---

## 4. Steelmanned Recommendations

### 4.1 Recommendation 1: Implement Headlinese-Style Systematic Deletion (CONFIDENCE: HIGH)

**The strongest case**: Headlinese achieves 2.5–5× compression with near-zero decoding cost for literate readers. LLMs are "super-literately-readers" — they model the full distributional statistics of language and can reconstruct deleted elements more reliably than humans. The ML evidence confirms that extractive compression (deleting tokens while keeping the rest verbatim) outperforms abstractive compression and token pruning across tasks [Chang et al., 2024].

**Implementation for memesis**:
1. Define a deletion grammar based on Halliday's "economy grammar" principles:
   - Drop articles (a, an, the)
   - Drop auxiliary verbs (is, was, has, will, would)
   - Drop copula where context makes meaning clear
   - Shift verbs to present tense
   - Drop pronouns where referent is clear from adjacent context
   - Merge redundant bullets saying the same thing in different words
   - Replace verbose phrases with short synonyms ("big" not "extensive", "fix" not "implement a solution for")
   - Drop throat-clearing ("you should", "make sure to", "remember to" → just state the action)
2. Preserve EXACTLY: code, URLs, file paths, commands, technical terms, proper nouns, dates, numbers
3. Validate: After compression, verify all preserved elements are intact

**Measured benchmark**: Caveman achieves ~46% input token reduction (roughly 1.85×) with this exact approach. LLMLingua achieves 2–5× with more aggressive token-level pruning. The safe zone for memesis is 2–4×.

**What could knock this down**: If memesis memories contain high information density per token (which they should, since observations are already concise), deletion has less redundancy to remove. The 2.5–5× headlinese ratio assumes news-prose-level redundancy, which may not hold for already-compact memory content.

**Counter to knock-down**: Memesis observations in stage 1 (ephemeral) are verbose by design — they capture raw observations including context. The consolidation and crystallization stages have significant redundancy that systematic deletion can remove. The "British Left Waffles" ambiguity risk is mitigated by preserving technical terms and proper nouns.

### 4.2 Recommendation 2: Expand Codebook-Style Schema Vocabularies (CONFIDENCE: MEDIUM-HIGH)

**The strongest case**: Telegraph codes achieved 4–10× per phrase by mapping predictable expressions to short tokens. The key prerequisite — a shared codebook — maps naturally to memesis's existing schema definition. The schema already defines observation types (preference, pattern, correction, friction_signal). Expanding this to include standardized value vocabularies (e.g., `lang:py` instead of "Python", `tool:vim` instead of "Vim editor") gives codebook compression without creating a novel symbol system.

**Implementation for memesis**:
1. Define a bounded vocabulary for high-frequency values: programming languages, tools, frameworks, common error patterns, preference dimensions
2. Encode values using the short form in crystallized/instinctive memories
3. Include the vocabulary definition in system context (the "codebook")
4. Always allow natural-language escape for novel values not in the vocabulary
5. Evolve the vocabulary based on frequency analysis of stored memories

**Measured benchmark**: Telegraph codes achieved 4–10× per coded phrase. For memesis, expect 1.5–2× per observation (since not all content is codebook-mappable). Combined with systematic deletion, total compression of 3–5× is achievable.

**What could knock this down**: The system context overhead for the vocabulary definition. If the vocabulary costs 200 tokens and saves 10 tokens per observation, break-even is 20 observations. Since memesis stores hundreds, this is easily achieved.

### 4.3 Recommendation 3: Stage-Adaptive Compression Depth (CONFIDENCE: HIGH)

**The strongest case**: Different memory stages have different compression tolerances. The historical evidence shows that compression effectiveness depends on the domain and the reader's ability to reconstruct from context. Memesis's lifecycle stages naturally map to different compression depths:

| Stage | Current Behavior | Recommended Compression | Historical Analogy |
|-------|-----------------|------------------------|-------------------|
| Ephemeral | Raw observation | None (keep ground truth) | Longhand (full detail) |
| Consolidated | Pattern extraction + summary | 2–3× (systematic deletion only) | Headlinese (grammatical deletion) |
| Crystallized | Durable pattern | 3–5× (deletion + schema encoding) | Headlinese + Telegraph code |
| Instinctive | Behavioral heuristic | 5–10× (maximum compression) | NATO brevity code (single-word triggers) |

**What could knock this down**: If instinctive memories lose too much context, the LLM may hallucinate behavioral triggers. Mitigation: instinctive memories should include at minimum the trigger condition and the behavioral response, even in maximally compressed form.

### 4.4 Recommendation 4: Do NOT Implement Shorthand-Style Symbolic Substitution or SMS-Style Abbreviation (CONFIDENCE: HIGH)

**The case against shorthand**: LLMs process token embeddings, not visual strokes. Creating a novel symbolic system requires the LLM to reliably decode symbols it wasn't trained on, which has no empirical support. The ML evidence consistently shows that maintaining natural language form (even heavily compressed) outperforms symbolic encoding [Chang et al., 2024].

**The case against SMS abbreviation**: Character-level abbreviation doesn't reliably reduce BPE token count. "Dictionary" and "dctnry" may tokenize to similar token counts. The Twitter natural experiment showed that ad hoc abbreviation is constraint-driven and reverses when constraints lift — it's not an efficiency optimization, it's a coping mechanism.

---

## 5. Conflicts and Open Questions

### Conflicts

1. **Compression ratio claims vary wildly**: LLMLingua claims 20× on GSM8K with minimal loss; the empirical characterization study finds extractive compression more reliable than token pruning at high ratios. **Resolution**: The 20× figure is on a specific reasoning task with specific controls; real-world compression for memory content should target 2–5× as the safe zone.

2. **Headlinese ratio (2.5–5×) vs. caveman measured savings (~46% = 1.85×)**: The headlinese ratio assumes news-prose-level redundancy. Caveman compresses CLAUDE.md files that are already relatively concise, yielding less savings. **Resolution**: Both figures are correct for their domains. For memesis memories, expect 2–3× for consolidated content (which is more verbose than CLAUDE.md) and 3–5× for crystallized content (which can tolerate more aggressive deletion).

3. **Extractive vs. token pruning**: The 2024 characterization study found extractive compression outperforms token pruning. LLMLingua (token pruning) claims state-of-the-art results. **Resolution**: The distinction is task-dependent. Extractive compression (sentence/phrase-level deletion) preserves grammaticality; token pruning (removing individual tokens) can produce awkward text. For memory content where the LLM must reconstruct meaning, extractive compression (headlinese-style) is safer.

### Open Questions

1. **What is the optimal compression ratio for each memesis stage?** The empirical evidence gives ranges (2–5× safe, 10–20× with quality loss), but the optimal point for memesis's specific content type (developer behavior observations) hasn't been measured. **Recommendation**: Implement compression with configurable aggressiveness and A/B test on retrieval accuracy.

2. **Does compressed content shift the LLM's output distribution?** The 2025 data distribution study shows decoder alignment is critical. Compressed memories may cause the LLM to produce different outputs than uncompressed memories, even if the information content is preserved. **Recommendation**: Test retrieval accuracy and behavioral consistency with compressed vs. uncompressed memories.

3. **What is the actual token savings of the memesis schema vocabulary?** The codebook approach requires including the schema definition in system context. The net savings depends on how many memories share schema fields. **Recommendation**: Measure token counts before and after compression across a representative set of 100+ memories.

---

## 6. Implications and Long-Term Plan

### Implications for Memesis Architecture

1. **Consolidation should use headlinese-style deletion as the primary technique** — this is the most evidence-backed, lowest-risk approach.
2. **Crystallization should combine deletion with schema encoding** — this adds codebook compression on top of deletion for higher ratios.
3. **Instinctive memories should use maximally compressed brevity-code format** — single-trigger, single-response, no context window waste.
4. **Never compress ephemeral observations** — these are ground truth.
5. **Build compression validation into the pipeline** — after each compression step, verify that key information elements are preserved (technical terms, proper nouns, numbers, code, URLs).

### Long-Term Roadmap

**Phase 1** (Immediate): Implement systematic deletion grammar for consolidation stage. Target 2–3× compression. Validate with retrieval accuracy tests.

**Phase 2** (Short-term): Expand schema vocabulary for codebook compression. Define high-frequency value abbreviations. Target 3–5× for crystallized memories.

**Phase 3** (Medium-term): Implement stage-adaptive compression depth. Instinctive stage gets maximum compression (5–10×) with brevity-code format. Validate behavioral consistency.

**Phase 4** (Long-term): Consider LLMLingua-style perplexity-based token pruning as an optional aggressive compression mode for very long memory contexts. Requires benchmarking against systematic deletion on memesis-specific content.

---

## Sources

[1] Shorthand — Wikipedia. https://en.wikipedia.org/wiki/Shorthand (2024)
[2] Teeline Shorthand — Wikipedia. https://en.wikipedia.org/wiki/Teeline_Shorthand (2024)
[3] Gregg Shorthand — Wikipedia. https://en.wikipedia.org/wiki/Gregg_shorthand (2024)
[4] NCTJ Shorthand. https://www.nctj.com/qualifications-courses/shorthand/ (2024)
[5] Pitman Training — Speed Development. http://www.pitmanlondon.co.uk/shorthandspeed/ (2024)
[6] Phillips Code — Wikipedia. https://en.wikipedia.org/wiki/Phillips_Code (2024)
[7] Commercial Code (Communications) — Wikipedia. https://en.wikipedia.org/wiki/Commercial_code_(communications) (2024)
[8] Telegraph Codebooks — CS Columbia. https://www.cs.columbia.edu/~smb/papers/codebooks.pdf (2025)
[9] ACP 131 — Wikipedia. https://en.wikipedia.org/wiki/ACP_131 (2024)
[10] ACP 131(F) PDF. https://www.idahoares.info/_downloads/articles/DigitalComms/ACP131F09%20Operating%20(Z%20and%20Q)%20Signals.pdf (2009)
[11] Multi-service Tactical Brevity Code — Wikipedia. https://en.wikipedia.org/wiki/Multi-service_tactical_brevity_code (2024)
[12] Headline — Wikipedia. https://en.wikipedia.org/wiki/Headline (2024)
[13] News Headline as Text Compression — Springer. https://link.springer.com/chapter/10.1007/978-3-030-01159-8_13 (2018)
[14] Of headlines and headlinese — OpenEdition. https://journals.openedition.org/asp/2523 (2012)
[15] SMS Language — Wikipedia. https://en.wikipedia.org/wiki/SMS_language (2024)
[16] How Character Limit Affects Language Usage in Tweets — Nature. https://www.nature.com/articles/s41599-019-0280-3 (2019)
[17] Adoption of Twitter's New Length Limit — ResearchGate. https://www.researchgate.net/publication/344277133 (2020)
[18] Tok Pisin corpus study — ResearchGate. https://www.researchgate.net/publication/248905753 (2008)
[19] Tok Pisin — Wikipedia. https://en.wikipedia.org/wiki/Tok_Pisin (2024)
[20] Hawai'i Creole English — UH Sato Center. https://www.hawaii.edu/satocenter/langnet/definitions/hce.html (2024)
[21] Phillips Code Archive — Internet Archive. https://archive.org/details/book_20190917 (1879/2019)
[22] Words Per Minute — Wikipedia. https://en.wikipedia.org/wiki/Words_per_minute (2024)
[23] Brevity Code — Wikipedia. https://en.wikipedia.org/wiki/Brevity_code (2024)
[24] LLMLingua — Jiang et al. https://aclanthology.org/2023.emnlp-main.825/ (2023)
[25] LongLLMLingua — Jiang et al. https://arxiv.org/html/2310.06839v2 (2024)
[26] LLMLingua-2 — Pan et al. https://arxiv.org/abs/2403.12968v2 (2024)
[27] Characterizing Prompt Compression Methods — Chang et al. https://arxiv.org/html/2407.08892v1 (2024)
[28] Prompt Compression Survey — Li et al. https://aclanthology.org/2025.naacl-long.368/ (2025)
[29] Data Distribution Matters — arXiv. https://arxiv.org/html/2602.01778v1 (2025)
[30] Empirical Study on Prompt Compression — arXiv. https://arxiv.org/html/2505.00019v1 (2025)
[31] End-to-End Latency Study — arXiv. https://www.arxiv.org/pdf/2604.02985 (2025)
[32] Semantic Compression for Context Extension — arXiv. https://arxiv.org/abs/2312.09571v1 (2023)
[33] LLMZip: Lossless Text Compression — arXiv. http://arxiv.org/abs/2306.04050 (2023)
[34] Language Modeling Is Compression — Delétang et al. https://arxiv.org/html/2309.10668v2 (2023)
[35] Entropy and Redundancy in English — Stanford. https://cs.stanford.edu/people/eroberts/courses/soco/projects/1999-00/information-theory/entropy_of_english_9.html
[36] Caveman plugin — JuliusBrussee. https://github.com/JuliusBrussee/caveman (2026)
[37] Cavemem — JuliusBrussee. https://github.com/JuliusBrussee/cavemem (2026)