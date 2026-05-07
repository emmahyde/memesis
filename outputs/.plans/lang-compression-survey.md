# Plan: Language Compression Survey

## Topic
Most effective social/natural language compression algorithm — survey across history (shorthand, telegraphy, military brevity), constructed languages, and modern LLM-era prompt compression. Identify highest-compression-ratio system that remains decodable, with tradeoffs.

## Sub-questions
1. What compression ratios do historical shorthand systems achieve (Pitman, Gregg, Teeline)?
2. What do telegraphy codes (Phillips, ABC, Q-codes, ACP-131) prove about domain-specific compression?
3. What is the Shannon entropy bound for English, and how do real systems compare?
4. How do constructed minimal languages (Toki Pona, Lojban, Newspeak) compress vs natural language? Are tradeoffs (ambiguity, expressiveness) acceptable?
5. How do pidgins/creoles, headlinese, SMS-speak evolve compression naturally?
6. What does modern prompt-compression research (LLMLingua, LongLLMLingua, etc.) achieve, and how?
7. Which system applies best to LLM-to-LLM handoff documents (passoff context)?

## Researchers

### R1: Historical Human Compression Systems
- shorthand (Pitman, Gregg, Teeline) — ratios, learning cost
- telegraphy codes (Phillips, ABC, commercial codebooks) — ratios
- military brevity (Q-codes, NATO ACP-131, Brevity)
- headlinese, SMS-speak, Twitter constraint adaptation
- Output: `outputs/lang-compression-survey-research-historical.md`

### R2: Linguistic Theory + Constructed Languages
- Shannon entropy of English (~1.3 bits/char), Zipf's law
- constructed minimal languages: Toki Pona (137 words), Lojban (logical), Newspeak (cautionary)
- pidgins/creoles as natural compression
- classical Chinese / wenyan as compression
- Output: `outputs/lang-compression-survey-research-linguistic.md`

### R3: Modern LLM Prompt Compression
- LLMLingua, LongLLMLingua, LLMLingua-2 (Microsoft)
- Selective Context, ICAE, AutoCompressors
- BPE-friendly phrasings, tokenizer-aware techniques
- Compression ratios, fidelity on benchmarks
- Output: `outputs/lang-compression-survey-research-llm.md`

## Acceptance Criteria
- Each system: compression ratio (or estimate), decoding cost, audience, domain limits
- Cross-system comparison table
- Recommendation for LLM-to-LLM handoff (passoff use case)
- All claims cited
