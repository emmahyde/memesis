# Linguistic Theory and Constructed Languages as Compression Systems

## Executive Summary

Human natural language operates under a remarkable constraint: despite enormous variation in syllable-level information density (5–8 bits/syllable across languages), spoken communication converges on approximately 39 bits/second across all tested natural languages [3]. This suggests a processing equilibrium that may constrain constructed languages — though no empirical measurements of information rate exist for any constructed language, so the ~39 bits/second figure serves as a benchmark rather than a proven limit.

Constructed languages cluster into two poles on the compression spectrum. Vocabulary-minimalist systems (Toki Pona at 120–137 words [10], Basic English at 850 words [15]) achieve surface-level token reduction by shifting disambiguation cost to the receiver through polysemy and contextual inference. Precision-maximalist systems (Lojban with machine-verified unambiguous grammar [13], Ithkuil with extreme morphological density [20]) eliminate ambiguity at the cost of increased verbosity and encoding burden. Structural restrictions like E-Prime target specific redundancy types without changing vocabulary size. The consistent pattern across all systems is the ambiguity-verbosity trade-off: compression on one dimension expands cost on another. Whether constructed languages are subject to the same ~39 bits/second convergence as natural languages is unknown — no information-rate measurements exist for any constructed language.

## 1. The Information-Theoretic Baseline: Entropy, Redundancy, and Zipf's Law

Claude Shannon's 1948 "Mathematical Theory of Communication" [2] introduced entropy as a measure of information and, crucially, the concept of linguistic redundancy. Shannon defined redundancy as the ratio of actual entropy to maximum possible entropy: for a language using N symbols, maximum entropy equals log₂(N) bits/symbol; real language falls far short because letters, words, and grammatical structures are heavily constrained by prior context.

In the 1951 follow-up paper "Prediction and Entropy of Printed English" [1], Shannon used human prediction experiments to bound English's true entropy. When only individual letter frequencies are considered, English has ~4.14 bits/character. Accounting for statistical structure over an 8-letter window, entropy drops to ~2.3 bits/character and the corresponding redundancy reaches ~50%. When long-range structure (whole paragraphs, discourse coherence, topic) is included, Shannon estimated entropy falls to 0.6–1.3 bits/character — implying redundancy of roughly 75% [1]. The ~1 bit/character figure frequently cited in the literature represents this long-range, high-redundancy estimate; the ~50% redundancy figure is Shannon's own language (at the 8-letter scale) from the 1948 paper [2].

Zipf's Law provides the frequency side of this picture. Empirically, word frequency follows a power law with exponent approximately −1: the most common word occurs roughly twice as often as the second most common, and so on [21]. This produces extreme concentration: the top 100 words account for approximately 50% of all tokens in running English text; the top 250 words ~65%; the top 500 ~75%; and the top 1,000 ~80% [23]. To reach 98% text coverage — the threshold Nation (2001) identifies as necessary for autonomous reading comprehension — requires knowledge of roughly 8,000 word families [22]. This steep coverage curve is the empirical foundation for minimalist vocabulary projects: Ogden and Lang both exploited the fact that a tiny core vocabulary does enormous work.

Piantadosi's 2014 critical review [5] confirms Zipf's law is robust across languages and domains but finds that no single theoretical account — information optimization, random text, preferential attachment, or semantic clustering — fully explains it. The frequency distribution has systematic structure beyond the power law, including meaning-driven regularities (e.g., number words follow Zipfian distribution by magnitude). Futrell and Hahn (2022) formalize this information-theoretic perspective as a bridge between language function and form, framing the optimal language as a constrained minimization of complexity [8].

## 2. Universal Channel Capacity: Cross-Linguistic Information Rate

The most significant recent development in the information-theoretic framing of language is the finding that human languages appear to converge on a universal information transmission rate. Pellegrino, Coupé, and Marsico (2011) [4] studied 7 languages and found a negative correlation between syllabic information density and speech rate: languages that pack more information per syllable are spoken more slowly. While the 2011 study found a tendency toward uniformity — not a strict constant — the relationship was clear enough to motivate the hypothesis of a universal rate.

Coupé, Oh, Dediu, and Pellegrino (2019) [3] expanded this to 17 languages (Basque, English, Cantonese, Catalan, Finnish, French, German, Hungarian, Italian, Japanese, Korean, Mandarin, Serbian, Spanish, Thai, Turkish, Vietnamese) and found convergence on approximately 39 bits/second across all of them. The variance in information density per syllable is large: Japanese encodes ~5 bits/syllable (roughly 32 possible distinct syllables in active use); English ~7 bits/syllable; Vietnamese ~8 bits/syllable (aided by 6 tones) [3]. Yet faster-speaking languages compensate with lower density and vice versa, producing the ~39 bits/second convergence.

The mechanism suggested is a cognitive processing equilibrium: the human auditory system processes speech at rates near 39 bits/second across natural languages. Whether this rate is a fixed biological limit or a learned equilibrium remains an open question — musicians and polyglots show enhanced auditory processing, suggesting possible plasticity. For constructed languages, the implication is that a language achieving compression through higher syllable information density (like Ithkuil) may require slower speaking or impose greater cognitive load — but no empirical tests exist [3].

Levy and Jaeger's Uniform Information Density (UID) hypothesis [6][7] describes a related but distinct phenomenon: speakers modulate syntactic choices, lexical selections, and phonetic reductions to maintain roughly constant surprisal per time unit within a single language. English contraction patterns, optional complementizer that-deletion, and reduced pronunciation of predictable words all follow UID predictions. The relationship between UID (intra-speaker syntactic smoothing) and the cross-linguistic ~39 bits/second rate [3] is unclear — the papers describe different levels of analysis and no causal link has been established.

## 3. Toki Pona: Radical Vocabulary Minimalism

Toki Pona is the most extreme vocabulary-minimalist constructed language with an active speaker community. Created by Canadian translator Sonja Lang starting in 2001 and first published online, it was conceived as a therapeutic language — a way to reduce cognitive complexity during a period of depression. The core design philosophy is elimination of lexical redundancy: rather than having separate words for concepts that can be compositionally described, Toki Pona uses polysemous roots plus contextual interpretation.

The vocabulary count depends on which edition: "pu" (Toki Pona: The Language of Good, 2014) contains 120 main words plus 3 treated as synonyms (123 total); "ku" (Toki Pona Dictionary, 2021) adds 16 new "essential" words (nimi ku suli) bringing the essential vocabulary to 137, with up to 187 words overall in the extended edition [10]. The phonology is also minimal: 14 letters, ~100 possible syllables, and approximately 10 grammatical rules [9][11].

The compression mechanism is wholesale polysemy with compositional disambiguation. The word suli means "big," "long," and "important" simultaneously; context determines which reading applies. Complex nouns are constructed as noun phrases: tomo tawa ("house moving") = car; telo nasa ("strange liquid") = alcohol; jan pona ("good person") = friend [9]. This shifts the compression cost from the encoding side (sender uses fewer words) to the decoding side (receiver must infer from context). The arXiv formal analysis [11] identifies four structural ambiguity sources: small vocabulary causing semantic underspecification; prepositions functioning as any part of speech; absence of the subject-marker li after pronouns mi (I) and sina (you); and frequent elliptical sentences.

The disambiguation cost is real and has community implications. The sentence "mi jo e tomo tawa waso" is genuinely ambiguous between "I have an airplane" and "I have a house for birds," and only context or follow-up resolves it [11]. Community surveys (2024 census: 1,997 respondents) [12] show an active online community of learners and speakers, primarily in Europe and North America, suggesting the disambiguation cost is manageable for motivated users in constrained topics but limits use in high-stakes or high-precision contexts.

Toki Pona's compression ratio relative to English is hard to measure precisely, but examples circulate in community discussions of sentences that in English require 8–15 words being expressed in 3–5 Toki Pona words. The trade-off: loss of referential precision, gain in combinatorial economy and reduced vocabulary burden. Community reports suggest a learner can achieve basic conversational fluency in days rather than months, though no formal acquisition studies have been conducted.

## 4. Lojban: Unambiguous Predicate Logic at Vocabulary Scale

Lojban takes the opposite philosophical stance to Toki Pona on the ambiguity-compression trade-off. Created by the Logical Language Group (founded by Bob LeChevalier in 1987, based on James Cooke Brown's earlier Loglan project), Lojban is designed for formal unambiguity: its grammar was machine-tested to ensure every sentence has exactly one parse [13]. The linguistic basis is predicate logic: every core expression is a predication — a claim that some objects stand in some relationship.

Lojban's vocabulary is larger than Toki Pona's but smaller than natural languages: approximately 1,300 gismu (root words), combinable into compound words (lujvo) to produce a vocabulary of millions of possible words [14]. Structural particles (cmavo) handle logical connectives, tense, evidentiality, and metalinguistic commentary. The grammar is regular with no exceptions, and the brochure claims it can be learned in "a couple of hours" for grammar, with vocabulary work requiring more time [14].

Lojban trades verbosity for precision. Where English "I believe it will rain" conflates epistemic commitment and future assertion, Lojban requires explicit marking of both. Natural language ambiguities that are resolved by pragmatics in English must be resolved by explicit grammatical markers in Lojban. The result is that Lojban translations of natural language texts tend to be longer than their English equivalents — precision is achieved at the cost of token count. No peer-reviewed studies measuring Lojban-to-English verbosity ratios were located; this remains an empirical gap.

The information density of Lojban per syllable is presumably higher than English given the machine-tested unambiguity, but because ambiguity resolution shifts from inference to explicit morphology, the total bits transmitted per communication act may be similar or higher. Lojban's channel is less noisy (in Shannon's sense) but not necessarily narrower.

## 5. Basic English: Controlled Vocabulary for International Use

C.K. Ogden published Basic English: A General Introduction with Rules and Grammar in 1930. The design criterion was empirical reduction of the Oxford Pocket English Dictionary: by eliminating synonyms, idioms requiring specialized knowledge, and words constructible from simpler combinations, Ogden arrived at 850 words [15]. The breakdown is structured: 100 operational words (prepositions, pronouns, operators like make/get/come/put), 400 general nouns, 200 picturable nouns, 100 general qualities (adjectives), and 50 opposites [16]. The coverage claim — that these 850 words can express 90% of the concepts in the full dictionary — was arrived at through Ogden's own iterative text rephrasing rather than formal corpus study, and empirical validation was never published [15].

Churchill endorsed Basic English in a 1943 Harvard address, briefly elevating its policy profile, though neither the UK nor US government adopted it [15].

Orwell's relationship with Basic English was complex. He corresponded with Ogden and in 1944 praised its deflating effect on pompous language ("High-sounding phrases, when translated into Basic, are often deflated in a surprising way") [24]. However, his 1946 essay "Politics and the English Language" raised broader concerns about linguistic restriction as a tool of control — concerns that directly inspired Newspeak in 1984. The Orwell Society notes that "Newspeak is generally presented as a satire of both cablese and Basic English" [24]. Orwell's position was ambivalent: the same vocabulary restriction that deflates pretension can also be weaponized to prevent the expression of dissident thought.

Basic English's key insight as a compression system is that the operational vocabulary (the ~100 function words and operators) handles the structural burden while a small content vocabulary handles most nominal reference. The 850-word vocabulary exploits Zipf's law: since those 850 words are drawn heavily from the high-frequency end of the distribution, they cover the majority of token occurrences in actual text even if they cannot express the full range of English concepts.

## 6. E-Prime: Structural Compression via Copula Elimination

E-Prime (English-Prime) is a constrained form of English that eliminates all forms of the verb "to be": be, being, been, am, is, are, was, were, and all contractions thereof [18]. D. David Bourland Jr. published the concept in 1965 in General Semantics Bulletin in an essay titled "A Linguistic Note: Writing in E-Prime," building on Alfred Korzybski's General Semantics framework from the 1920s–1940s [19].

The cognitive compression claim is specific: the verb "to be" conflates three logically distinct operations. The "is of identity" ("X is Y") asserts that two referents are identical, which is almost always an overstatement — Korzybski's point was that the map is not the territory. The "is of predication" ("The apple is red") attributes a property absolutely rather than relationally. E-Prime forces writers to replace these with process statements: "I feel depressed" instead of "I am depressed"; "The apple looks red to me" instead of "The apple is red." Korzybski anecdotally claimed an improvement "of one full letter grade" for students who avoided the infinitive in writing [19], though no controlled study has replicated this.

A 2019 empirical study (small sample) tested E-Prime against affective and cognitive outcomes, finding that lower "to be" usage correlated with decreased irrational beliefs, providing preliminary support for the psychological benefits [18]. The evidence base remains thin, and the causal direction is unclear — lower "to be" usage may reduce irrational beliefs, or people with fewer irrational beliefs may naturally use less "to be." E-Prime's compression mechanism is not about reducing vocabulary size but about restructuring epistemic framing — it compresses the range of representational stances available, forcing specificity about who is perceiving, under what conditions, rather than allowing false generalization via identity claims.

E-Prime is notable in this survey because it targets a structural compression pathway not addressed by vocabulary restriction: it is compression via copula elimination rather than lexical minimalism. Whether this genuinely reduces redundancy in Shannon's sense is unclear; it may increase token count while reducing semantic ambiguity of a specific type.

## 7. Ithkuil: Morphological Maximalism as Compression

Ithkuil, created by John Quijada (first version 2004, revised 2011 and 2023), represents the opposite pole from Toki Pona: extreme morphological density rather than lexical minimalism. Quijada's goal was to express human cognitive processes "briefly yet overtly and clearly" — to make implicit semantic distinctions that natural languages leave vague into mandatory grammatical categories [20].

Ithkuil encodes evidentiality, cognitive framing, affective stance, and dozens of other parameters through morphological markers on every word. The compression claim is dramatic: approximately 3,600 root words encode the conceptual space covered by "hundreds of thousands" of English words, and Quijada provides examples of 19-word English sentences translating to 2-word Ithkuil utterances [20]. The compression is real at the surface token level, but the cognitive cost to the encoder is enormous — no speaker population has acquired Ithkuil natively, and even the creator cannot speak it fluently in real-time [20].

Ithkuil illustrates the fundamental limit on compression via morphological density: the information must still be specified somewhere. If English leaves semantic slots vague and context fills them in (cheap for the sender), Ithkuil requires those slots be explicitly filled (expensive for the sender). The net information transmitted per communication event may not differ substantially; the allocation of cognitive labor does.

## 8. Synthesis: The Ambiguity-Verbosity Trade-off

Across all these systems, a consistent trade-off emerges: reducing lexical or structural ambiguity requires increased verbosity (more tokens, more morphological marking), while reducing verbosity (fewer words, polysemy, ellipsis) increases disambiguation cost on the receiver's side.

This trade-off is formalized in information theory as the source-channel trade-off: efficient source coding (compression) and reliable channel coding (noise resistance through redundancy) are in tension. Natural languages settle at an intermediate point that appears universal — the ~39 bits/second rate [3] — through a combination of redundancy at the lexical level (Zipf concentration), redundancy at the grammatical level (agreement, case marking, word order cues), and pragmatic inference (world knowledge filling gaps).

Constructed languages attempt to shift this equilibrium:

- Toki Pona and Basic English reduce the lexical redundancy, achieving lower vocabulary burden at the cost of increased ambiguity and heavier pragmatic inference load.
- Lojban and Ithkuil reduce grammatical and lexical ambiguity, pushing more information into explicit form at the cost of increased verbosity and encoding burden.
- E-Prime targets a specific redundancy type (copula-based identity claims) without changing vocabulary size.

The Uniform Information Density hypothesis [6][7] predicts that speakers of all these systems, when using them in natural conversation, will adapt their production to smooth surprisal — inserting clarifying context before dense passages, speaking slower when morphological marking is heavy, omitting predictable material. This pattern is consistent with anecdotal reports from Toki Pona speakers: common compound noun phrases become conventionalized (jan pona → friend) and are processed with low decoding cost by fluent users, while novel compounds require slow contextual unpacking. However, no empirical study has tested UID predictions in any constructed language.

The linguistic relativity framework (Sapir-Whorf hypothesis) is relevant here as a claim about cognitive effects: restricting vocabulary may reshape how speakers categorize experience (the strong Whorfian claim) or merely make certain framings easier or harder to express (the weak version). Korzybski's General Semantics and Bourland's E-Prime represent a behavioral implementation of weak Whorfianism applied to the copula. The evidence for meaningful cognitive restructuring through vocabulary restriction is modest; the evidence that such restriction reliably reduces communicative precision is clearer.

## Structured Comparison Table

| System          | Era          | Vocabulary Size                | Compression Mechanism                                        | Disambiguation Cost                | Ambiguity                   | Audience/Domain                            |
| --------------- | ------------ | ------------------------------ | ------------------------------------------------------------ | ---------------------------------- | --------------------------- | ------------------------------------------ |
| Natural English | —            | ~170,000 (OED) / 20,000 active | Redundancy, pragmatics, Zipf concentration                   | Low (shared world knowledge)       | High (polysemy, idiom)      | Universal                                  |
| Basic English   | 1930         | 850 words                      | Lexical selection from high-frequency zone; operators replace verbs | Medium (paraphrase required)       | Medium                      | International auxiliary; education         |
| Toki Pona (pu)  | 2001/2014    | 120–137 words                  | Polysemy + compositional compounding + context               | High (heavy pragmatic inference)   | Very high                   | Philosophical/meditative; small community  |
| Lojban          | 1987–present | ~1,300 gismu + cmavo           | Predicate-logic grammar; machine-verified parse              | Low (syntactic ambiguity eliminated)   | Syntactic: Very low; Semantic: Moderate | Logical communication; AI-adjacent         |
| E-Prime         | 1965         | ~English minus ~5% of tokens   | Copula elimination; forces relational framing                | Low per sentence (more words)      | Reduced for identity-claims | Writing discipline; therapy                |
| Ithkuil         | 2004–present | ~3,600 roots                   | Morphological density; explicit semantic slots               | Extremely high encoding cost       | Very low                    | Theoretical/experimental; no natural users |
| Interlingua     | 1951         | ~10,000 roots / 27,000 entries | Lexical convergence across European languages; regular grammar | Low (for Romance/English speakers) | Low                         | International scientific communication [17]     |

*Note: Ambiguity and disambiguation cost ratings are relative to natural English and assigned by the author based on cited sources. No standardized metric was used.*

## Conflicts & Open Questions

- **Shannon redundancy scale**: The 1948 paper gives ~50% at the ~8-letter scale; long-range estimates give ~75%. Some sources conflate the two without specifying scale [1][2].
- **Basic English 90% claim**: Ogden's own assertion through trial-and-error, not a formal corpus study; no independent validation found [15].
- **Ithkuil compression claim**: The "19-word → 2-word" example comes from Quijada's own documentation; no independent verification [20].
- **39 bits/second universality**: Contested after publication. The information density measure used (unigram entropy of syllables) may not capture full semantic throughput, so the claim may be stronger for speech mechanics than for full semantic communication [3].
- **E-Prime cognitive benefits**: The 2019 study had a small sample; Korzybski's grade-improvement claim is anecdotal [18][19].
- **Toki Pona word count**: pu advertises 120 but contains 123; ku says 137; extended set up to 187. All defensible depending on counting method [10].
- **Zipf coverage percentages**: Vary by corpus genre (spoken vs. written, formal vs. informal). Reliable as orders of magnitude, not precise thresholds [22][23].

## Gaps in the Literature

- No peer-reviewed studies directly measuring Lojban-to-English translation verbosity ratios.
- No empirical corpus studies validating Basic English's 90% coverage claim against running text.
- No controlled disambiguation-cost studies for Toki Pona.
- No formal information-density measurements for any constructed language in bits/syllable or bits/word — the most directly comparable figures to Coupé et al. [3].

## Implications

For language designers: The ~39 bits/second convergence among natural languages provides a relevant benchmark, though whether it applies to constructed languages is empirically untested. Compression strategies that increase information density per syllable (Ithkuil) likely impose greater cognitive load. Strategies that reduce vocabulary burden (Toki Pona, Basic English) are viable for constrained domains but impose pragmatic inference costs. The optimal constructed language for human use likely sits near the natural-language equilibrium: moderate vocabulary, moderate morphological marking, heavy reliance on pragmatic inference.

**Note on channel:** The ~39 bits/second finding comes from spoken language. Many constructed languages (Lojban, Ithkuil) are primarily written, where no articulatory rate constraint applies and text can be re-read. Written-channel compression may operate under different constraints than spoken-channel compression.

For AI and NLP (speculative): The ambiguity-verbosity trade-off suggests that systems trained on low-ambiguity data (Lojban-style explicit marking) may require more tokens but produce more parseable outputs, while systems trained on high-polysemy data (Toki Pona-style) may achieve greater token efficiency at the cost of interpretability. This is untested — no studies of LLM behavior with constructed languages were located.

## Sources

[1] Shannon, C.E. "Prediction and Entropy of Printed English" — https://www.princeton.edu/~wbialek/rome/refs/shannon_51.pdf (1951)
[2] Shannon, C.E. "A Mathematical Theory of Communication" — https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf (1948)
[3] Coupé, C., Oh, Y.M., Dediu, D., & Pellegrino, F. "Different languages, similar encoding efficiency" — https://pmc.ncbi.nlm.nih.gov/articles/PMC6984970/ (2019)
[4] Pellegrino, F., Coupé, C., & Marsico, E. "A Cross-Language Perspective on Speech Information Rate" — https://gwern.net/doc/cs/algorithm/information/2011-pellegrino.pdf (2011)
[5] Piantadosi, S.T. "Zipf's word frequency law in natural language: A critical review" — https://pubmed.ncbi.nlm.nih.gov/24664880/ (2014)
[6] Levy, R. & Jaeger, T.F. "Speakers optimize information density through syntactic reduction" — https://www.semanticscholar.org/paper/Speakers-optimize-information-density-through-Levy-Jaeger/bec3b18d0b74b7154882505545265b471bd7e68f (2007)
[7] Frank, A. & Jaeger, T.F. "Speaking Rationally: Uniform Information Density as Optimal Strategy" — https://www.researchgate.net/publication/284936955_Speaking_rationally_Uniform_information_density_as_an_optimal_strategy_for_language_production (2008)
[8] Futrell, R. & Hahn, M. "Information Theory as a Bridge Between Language Function and Language Form" — https://www.frontiersin.org/journals/communication/articles/10.3389/fcomm.2022.657725/full (2022)
[9] Toki Pona Wikipedia — https://en.wikipedia.org/wiki/Toki_Pona (2024)
[10] Sona pona: "How many words does Toki Pona have?" — https://sona.pona.la/wiki/number_of_words (2024)
[11] Almeida, M. & Rocha, R. "Basic concepts and tools for the Toki Pona minimal language" — https://arxiv.org/abs/1712.09359 (2017)
[12] Toki Pona census 2024 — https://tokiponacensus.github.io/results2024/ (2024)
[13] Lojban Wikipedia — https://en.wikipedia.org/wiki/Lojban (2024)
[14] The Lojban Brochure (official) — https://www.lojban.org/static/files/brochures/lojbroch.html (2024)
[15] Basic English Wikipedia — https://en.wikipedia.org/wiki/Basic_English (2024)
[16] Ogden's Basic English overview — https://zbenglish.net/sites/basic/basiceng.html (2024)
[17] Interlingua Wikipedia — https://en.wikipedia.org/wiki/Interlingua (2024)
[18] E-Prime Wikipedia — https://en.wikipedia.org/wiki/E-Prime (2024)
[19] "TO BE OR NOT TO BE: E-Prime as a Tool for Critical Thinking" — https://www.researchgate.net/publication/237804397_TO_BE_OR_NOT_TO_BE_E-Prime_as_a_Tool_for_Critical_Thinking_E-Prime_-_The_Fundamentals (2013)
[20] Ithkuil Wikipedia — https://en.wikipedia.org/wiki/Ithkuil (2024)
[21] Zipf's Law Wikipedia — https://en.wikipedia.org/wiki/Zipf%27s_law (2024)
[22] Nation, I.S.P. cited in "Lexical text coverage, learners' vocabulary size" — https://files.eric.ed.gov/fulltext/EJ887873.pdf (2010)
[23] Conti, G. "Zipf's Law and Vocabulary Teaching in ISLA" — https://gianfrancoconti.com/2025/04/14/zipfs-law-and-what-it-means-for-vocabulary-teaching-in-isla/ (2025)
[24] Orwell Society: "Good, Modern and Basic English" — https://orwellsociety.com/good-modern-and-basic-english/ (2024)
[25] ScienceDaily: "Similar information rates across languages, despite divergent speech rates" — https://www.sciencedaily.com/releases/2019/09/190905124520.htm (2019)
