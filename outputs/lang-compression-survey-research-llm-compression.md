# Research: Modern LLM Prompt Compression Techniques

## Evidence Table

| # | Source Title | URL | Key Finding | Date | Type |
|---|---|---|---|---|---|
| 1 | LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models | https://arxiv.org/abs/2310.05736 | Up to 20x compression with ~1.5 point performance drop; coarse-to-fine pipeline using small LM perplexity scoring | Oct 2023 | Conference (EMNLP 2023) |
| 2 | LLMLingua-2: Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression | https://arxiv.org/abs/2403.12968 | 3x-6x faster than LLMLingua; 2x-5x compression with 1.6x-2.9x end-to-end latency reduction; token classification framing | Mar 2024 | Conference (ACL 2024) |
| 3 | LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression | https://arxiv.org/abs/2310.06839 | +21.4% performance on NaturalQuestions with ~4x token reduction (GPT-3.5-Turbo); adds document reordering and question-aware compression | Oct 2023 | Preprint |
| 4 | In-context Autoencoder for Context Compression in a Large Language Model (ICAE) | https://arxiv.org/abs/2307.06945 | 4x compression of 512-token contexts into 128 memory slots; ~1% additional parameters via LoRA; soft-token approach | Jul 2023 | Preprint (v4 May 2024) |
| 5 | Adapting Language Models to Compress Contexts (AutoCompressor) | https://arxiv.org/abs/2305.14788 | Recursive compression to 150 summary vectors for 6,144 tokens; unsupervised training on OPT/Llama-2 up to 30,720 tokens | May 2023 | Conference (EMNLP 2023) |
| 6 | Learning to Compress Prompts with Gist Tokens | https://arxiv.org/abs/2304.08467 | 26x prompt compression; 40% FLOP reduction; 4.2% wall-time speedup; attention mask modification only, no extra training cost | Apr 2023 | Conference (NeurIPS 2023) |
| 7 | RECOMP: Improving Retrieval-Augmented LMs with Compression and Selective Augmentation | https://arxiv.org/abs/2310.04408 | Compression to 6% of original retrieved document length with minimal performance loss on QA and LM tasks; extractive + abstractive dual approach | Oct 2023 | Conference (ICLR 2024) |
| 8 | Lost in the Middle: How Language Models Use Long Contexts | https://arxiv.org/abs/2307.03172 | U-shaped performance curve: best recall at start/end of context, significant degradation for middle-positioned information | Jul 2023 | Journal (TACL 2023) |
| 9 | Compressing Context to Enhance Inference Efficiency of Large Language Models (Selective Context) | https://arxiv.org/abs/2310.06201 | 50% context reduction → 36% memory reduction, 32% latency reduction; BERTscore drop of only 0.023 | Oct 2023 | Conference (EMNLP 2023) |
| 10 | Characterizing Prompt Compression Methods for Long Context Inference | https://arxiv.org/abs/2407.08892 | Extractive compression outperforms abstractive at equivalent ratios; up to 10x with minimal accuracy degradation; head-to-head benchmark across LongBench tasks | Jul 2024 | Preprint |
| 11 | xRAG: Extreme Context Compression for Retrieval-augmented Generation with One Token | https://arxiv.org/abs/2405.13792 | Single token per retrieved document; >10% improvement on 6 knowledge-intensive tasks; 3.53x FLOP reduction; modality fusion bridges retrieval embeddings to LM | May 2024 | Conference (NeurIPS 2024) |
| 12 | Efficient Streaming Language Models with Attention Sinks (StreamingLLM) | https://arxiv.org/abs/2309.17453 | KV cache compression via attention sink retention; enables 4M+ token streaming; 22.2x speedup over sliding-window recomputation | Sep 2023 | Conference (ICLR 2024) |
| 13 | Language Modeling Is Compression | https://arxiv.org/abs/2309.10668 | Chinchilla 70B compresses ImageNet to 43.4% and LibriSpeech to 16.4% of raw size, beating domain-specific compressors; LLMs as universal compressors | Sep 2023 | Conference (ICLR 2024) |
| 14 | The Benefits of a Concise Chain of Thought on Problem-Solving in Large Language Models (CCoT) | https://arxiv.org/abs/2401.05618 | 48.7% response length reduction; 22.67% per-token cost reduction; GPT-3.5 math accuracy drops 27.69% — task-dependent performance cliff | Jan 2024 | Preprint |
| 15 | Token-Budget-Aware LLM Reasoning (TALE) | https://arxiv.org/abs/2412.18547 | 68.9% token reduction in CoT reasoning with <5% accuracy loss; explicit token budget in prompt controls output length; demonstrates CoT redundancy | Dec 2024 | Conference (ACL 2025 Findings) |
| 16 | Low-Resource Text Classification: A Parameter-Free Classification Method with Compressors | https://aclanthology.org/2023.findings-acl.426.pdf | GZIP + k-NN achieves results competitive with non-pretrained DL methods; outperforms BERT on 5 out-of-distribution/low-resource datasets | Jul 2023 | Conference (ACL 2023 Findings) |
| 17 | LazyLLM: Dynamic Token Pruning for Efficient Long Context LLM Inference | https://arxiv.org/abs/2407.14057 | 2.34x prefilling speedup on Llama-2-7B for multi-doc QA; dynamic per-step token selection (vs. static pruning); no fine-tuning required | Jul 2024 | Preprint |
| 18 | Prompt Compression for Large Language Models: A Survey | https://arxiv.org/abs/2410.12388 | Taxonomy of hard vs. soft prompt compression; benchmark summary: GIST up to 26x, LLMLingua up to 20x, ICAE 4x-16x; all methods increase hallucination under heavy compression | Oct 2024 | Survey |
| 19 | Contextual Compression in Retrieval-Augmented Generation for Large Language Models: A Survey | https://arxiv.org/abs/2409.13385 | Comprehensive taxonomy of RAG compression: lexical-based (token/sentence selection, summarization) vs. embedding-based (dense vector compression) | Sep 2024 | Survey |
| 20 | An Empirical Study on Prompt Compression for Large Language Models | https://arxiv.org/html/2505.00019v1 | Short context: all methods degrade with compression ratio increase; long context: moderate compression can improve performance; compression uniformly increases hallucination | May 2025 | Empirical study |
| 21 | Caveman Compression (GitHub / empirical blog studies) | https://github.com/wilpel/caveman-compression | 45% output token reduction vs. baseline; 39% vs. "be concise" instruction; Concise CoT cut response length 48.7%; math accuracy cliff at 27.69% on GPT-3.5 | 2024 | Empirical/blog |
| 22 | Found in the Middle: Calibrating Positional Attention Bias Improves Long Context Utilization | https://arxiv.org/abs/2406.16008 | Attention bias calibration partially recovers "lost in the middle" degradation; positional attention scores directly predictive of utilization | Jun 2024 | Preprint |
| 23 | Tokenization Is More Than Compression | https://arxiv.org/html/2402.18376v1 | Tokenization choices affect downstream task performance beyond pure compression ratio; vocabulary tradeoffs between sequence length and embedding size | Feb 2024 | Preprint |

---

## Findings

### 1. The LLMLingua Family: Token-Level Perplexity-Based Compression

The most cited and practically deployed compression framework is the LLMLingua series from Microsoft Research [1][2][3]. The original LLMLingua (EMNLP 2023) uses a small proxy language model (GPT2-small or LLaMA-7B) to score the "self-information" (negative log probability) of each token. Tokens with low self-information — those the model predicts confidently — are treated as redundant and pruned. A budget controller allocates different compression ratios to different prompt segments (instructions vs. demonstrations vs. questions), operating coarse-to-fine.

Claimed results: up to 20x compression across GSM8K (math reasoning), BBH (complex instruction following), ShareGPT (conversation), and Arxiv-March23 (summarization), with a maximum performance drop of 1.5 points. This figure should be treated with care: 1.5 points is an average — individual tasks vary significantly, and the 20x regime is tested primarily on in-context learning demonstrations, not dense factual retrieval.

LLMLingua-2 (ACL 2024) reframes compression as a binary token classification problem, trained via data distillation from GPT-4. This eliminates the dependency on a proxy LM at inference time and achieves task-agnostic compression. Speed improvement is 3x-6x over LLMLingua itself, with end-to-end latency reduction of 1.6x-2.9x at 2x-5x compression ratios [2]. The key architectural shift is from perplexity scoring (generative) to discriminative classification — this changes the failure mode: LLMLingua can hallucinate low-perplexity tokens as "unimportant" when context domain shifts; LLMLingua-2's classifier can mis-classify based on training distribution.

LongLLMLingua [3] adds question-aware compression and document reordering specifically for long-context retrieval scenarios. On NaturalQuestions with GPT-3.5-Turbo, it gains +21.4% performance at approximately 4x token reduction. When combined with LLMLingua-2's coarse-grained pass, average performance on NaturalQuestions improves by 25.3% over LLMLingua-2 alone.

**Apples-to-oranges warning:** LLMLingua's 20x figure and LLMLingua-2's 2x-5x figure are not directly comparable — the original paper tests on different tasks and uses performance "drop" framing (from full-prompt baseline), while LLMLingua-2 reports speed/latency and uses held-out QA benchmarks. Neither has been independently replicated across identical conditions in the published record as of 2025.

### 2. Soft-Prompt / Learned Compression: Gist Tokens, AutoCompressor, ICAE

A distinct paradigm replaces discrete token pruning with learned continuous representations. These systems train a model to map long contexts to compact "summary" vectors that the main LM can condition on.

**Gist tokens** [6] (NeurIPS 2023) modifies Transformer attention masks during instruction fine-tuning so that all information in the original prompt must be compressed into a small set of special "gist" tokens. No separate training phase is needed beyond standard instruction tuning — the masking is the compression mechanism. Results: 26x compression, 40% FLOP reduction, 4.2% wall-time speedup, minimal output quality loss on a range of instruction-following tasks. Unlike prefix-tuning (which distills a single fixed task), gisting generalizes zero-shot to new instructions via meta-learning.

**AutoCompressor** [5] (Princeton, EMNLP 2023) fine-tunes OPT and Llama-2 to recursively summarize context segments into soft summary vectors. Segments are processed iteratively: each sub-prompt is compressed into a fixed set of vectors, which are concatenated with the next sub-prompt for further compression. The system handles up to 30,720 tokens using 150 summary vectors (50 per segment, 3 segments). Training is unsupervised — language modeling loss over compressed representations. On in-context learning benchmarks, summary vectors serve as effective substitutes for plain-text demonstrations, improving accuracy while reducing inference cost. The Princeton group also demonstrates benefits for retrieval-augmented LM and passage re-ranking.

**ICAE** (In-Context Autoencoder) [4] uses LoRA to train a lightweight encoder (adding ~1% additional parameters) that compresses contexts into 32, 64, or 128 memory slots. The decoder is the frozen LLM itself. Achieves 4x compression of 512-token contexts based on Llama with measurable improvements in latency and GPU memory. v4 of the paper (May 2024) expands evaluation coverage.

The soft-prompt family shares a critical limitation: the compressed representations are model-specific and non-transferable. A Gist model trained on LLaMA-7B cannot share its compression with GPT-4. This makes deployment in multi-model systems or API-only contexts impractical. AutoCompressor partially mitigates this with cross-model transfer experiments but the gap is non-trivial.

### 3. RAG-Specific Compression: RECOMP and xRAG

Retrieval-augmented generation creates a specific compression problem: retrieved documents add hundreds to thousands of tokens of context that may be largely irrelevant to the specific query. Two architecturally distinct solutions dominate the literature.

**RECOMP** [7] (ICLR 2024) trains separate extractive and abstractive compressors. The extractive compressor uses contrastive learning to select sentences from retrieved documents whose inclusion improves the target output. The abstractive compressor is distilled from an extreme-scale LM (GPT-4 class) to synthesize multi-document summaries. Compression to 6% of original retrieved length with minimal performance loss on open-domain QA and language modeling tasks. Transfer across LM backbones is demonstrated. The 6% figure is striking but the benchmark conditions (fixed retrieval quality, relatively short retrieval sets) limit generalizability — real-world retrievals are longer and noisier.

**xRAG** [11] (NeurIPS 2024) takes a radically different approach: rather than compressing text, it routes the dense retrieval embedding (already computed) directly into the LM's representation space via a modality bridge, effectively treating each retrieved document as a single token. Only the bridge is trainable; both retriever and LM remain frozen. Adding one document token improves performance by >10% on six knowledge-intensive benchmarks vs. prior compression methods, with 3.53x FLOP reduction vs. uncompressed RAG. The critical caveat: xRAG requires a compatible dense retriever and access to the LM's internal representation space — it cannot be used with API-only deployments.

The 2024 survey on contextual compression in RAG [19] categorizes the full space into lexical-based (token pruning, sentence extraction, summarization) and embedding-based (ICAE-style, xRAG-style), noting that embedding-based methods achieve more extreme compression ratios but sacrifice interpretability and portability.

### 4. Attention-Based Compression and the "Lost in the Middle" Problem

Liu et al. [8] (TACL 2023) document a fundamental failure mode in long-context LLMs: performance follows a U-shaped curve as a function of the position of relevant information within the context window. Models perform best when relevant content is at the beginning or end of the prompt; performance degrades significantly when it appears in the middle. This effect persists even in models explicitly designed for long contexts.

The implication for compression is direct: position-aware compression — placing retained content at primacy/recency positions — should outperform position-naive pruning. LongLLMLingua's document reordering step [3] operationalizes this insight. The follow-up "Found in the Middle" paper [22] demonstrates that positional attention bias is the mechanistic cause and that calibrating these scores partially recovers performance without any compression, though the effect is partial.

**StreamingLLM** [12] (ICLR 2024) exploits a related attention pattern: "attention sinks," where initial tokens receive disproportionately high attention scores regardless of semantic content. Retaining these sink tokens (even semantically empty ones) stabilizes KV cache compression in streaming contexts. This enables LLMs to process sequences of 4M+ tokens in a streaming fashion with a fixed-size KV cache, achieving 22.2x speedup over sliding-window recomputation. StreamingLLM is not a semantic compression method — it doesn't reduce information density — but it is a KV cache compression method that makes long-context inference tractable.

**LazyLLM** [17] extends dynamic token pruning to the KV cache level. Rather than pruning tokens once at input, LazyLLM selects different token subsets for different generation steps, allowing tokens pruned in early steps to re-enter consideration later. On multi-document QA with Llama-2-7B, prefilling accelerates by 2.34x with no fine-tuning required. The 2025 empirical study [20] confirms that dynamic pruning approaches generally outperform static pruning at equivalent compression ratios, particularly for tasks requiring recall of information at varying context positions.

### 5. Extractive vs. Abstractive Compression: The Trade-off

The 2024 characterization study [10] provides the clearest head-to-head comparison published in this period. Testing across LongBench benchmarks (NarrativeQA, Qasper, MultiFieldQA, HotpotQA), it finds that extractive compression consistently outperforms abstractive compression at equivalent compression ratios. Up to 10x extractive compression with minimal accuracy degradation; abstractive compression at 4.5x on 2WikiMultihopQA yields +7.89 F1 with extractive methods vs. -4.69 F1 with abstractive methods.

This is counterintuitive: summarization (abstractive) is the "higher-level" compression but performs worse on question-answering benchmarks because it introduces paraphrase artifacts that diverge from the exact phrasing models are trained to attend to. Extractive methods preserve verbatim text, which is what QA evaluation metrics and model attention patterns prefer.

The 2025 empirical study [20] adds nuance: at moderate compression ratios on long contexts, compression can actually improve performance by filtering noise — a result consistent with LongLLMLingua's +21.4% gain. The effect inverts at high compression ratios. All methods tested increase hallucination under heavy compression, with information loss as the primary mechanism.

### 6. Token Budget Studies and CoT Compression

A line of work addresses how much output — not just input — context LLMs actually need.

**CCoT (Concise Chain of Thought)** [14] shows that instructing GPT-3.5 and GPT-4 to reason "step-by-step but concisely" reduces response length 48.7% with negligible accuracy impact on most tasks. Average per-token cost reduction: 22.67%. The exception is math problem solving, where GPT-3.5 suffers a 27.69% accuracy penalty — the reasoning chain itself was load-bearing, not redundant.

**TALE** [15] (ACL 2025 Findings) provides the tightest results on this question. By dynamically estimating an appropriate token budget per task (using a separate budget estimator) and inserting that budget as a constraint in the prompt, TALE reduces CoT reasoning tokens by 68.9% on average with <5% accuracy loss. The key finding: current LLM reasoning is substantially redundant, not information-minimal. Budget-constrained prompting is sufficient to elicit this compression — no architectural changes required.

The "token complexity" concept [from arxiv.org/html/2503.01141] formalizes this: each task has a minimum token count below which accuracy degrades, but above which additional tokens are redundant. The boundary varies by task type and model scale, with larger models generally more tolerant of aggressive CoT compression (fewer tokens needed per unit of accuracy).

### 7. Tokenization as Compression: BPE and Beyond

Byte-Pair Encoding [original Sennrich et al. 2016, extended in modern LLMs] is the dominant tokenization algorithm, itself derived from a 1994 data compression algorithm. BPE iteratively merges the most frequent adjacent token pairs, building a vocabulary that compresses common subword sequences. Effective vocabulary size directly determines sequence length: a larger vocabulary produces shorter token sequences but larger embedding tables.

The "Tokenization Is More Than Compression" paper [23] argues that BPE's compression function and its linguistic structure function are separable — choices optimizing for compression ratio do not necessarily produce representations optimal for downstream task performance. Language-specific tokenization disparities are documented: high-resource languages (English) are efficiently tokenized while low-resource and morphologically complex languages produce longer token sequences for equivalent semantic content, a form of unintentional linguistic discrimination.

**Language Modeling Is Compression** [13] (Google DeepMind, ICLR 2024) completes this loop by demonstrating that prediction-as-compression is not merely a theoretical equivalence but a measurable fact: Chinchilla 70B compresses ImageNet patches to 43.4% of raw size (beating PNG at 58.5%) and LibriSpeech audio to 16.4% (beating FLAC at 30.3%). Large LLMs are empirically competitive with domain-specific compressors on non-text modalities, providing scaling law insights through the compression lens.

**GZIP + k-NN** [16] (ACL 2023 Findings) achieves the opposite: using classical compression as a proxy for semantic similarity. Compression ratio of concatenated text pairs serves as a distance metric. Without any training, this parameter-free method matches non-pretrained deep learning models and outperforms BERT on 5 out-of-distribution/low-resource datasets. This is primarily a classification technique, not a prompt compression method, but it demonstrates the deep equivalence between compression and semantic similarity.

### 8. Caveman-Style / Compressed-Register Prompting

A cluster of empirical work (mostly 2024, partly practitioner-driven) tests extreme register compression in natural language input/output: removing function words, determiners, prepositions, and grammatical markers while preserving content words — structurally analogous to SMS/telegraphic compression from the historical survey.

Results from the Caveman Compression project [21] and associated blog benchmarks: 45% output token reduction vs. standard baseline; 39% vs. a simple "be concise" instruction. These gains hold across 10 diverse prompt types. A study across 31 models (0.5B to 405B parameters, 1,485 problems) finds the effect is scale-dependent: larger models over-elaborate and benefit more from compressed-register constraints, with accuracy improvements of up to 26 percentage points on some benchmarks. The mechanism is "scale-dependent verbosity" — larger models generate more redundant text by default.

**Critical gap:** No peer-reviewed benchmark systematically tests caveman-style input compression (user prompt in compressed register) vs. output compression. The existing evidence is almost entirely on output compression (asking the model to respond tersely). Whether compressed-register input prompts preserve or degrade model comprehension remains under-studied.

### 9. Benchmark Comparability Issues

The prompt compression literature has a significant apples-to-oranges problem across papers:

- **Compression ratio definition varies.** Some papers report character/word compression ratios, others report token ratios (BPE-tokenized), others report FLOP reduction. LLMLingua's "20x" is token-based; RECOMP's "6%" is word-based vs. retrieved document length only.
- **Baseline prompts differ.** Papers using "full context" baselines with GPT-3.5/4 API are not comparable to papers using open-weight models with known context limits.
- **Task distribution.** QA benchmarks (NaturalQuestions, HotpotQA) favor extractive methods that preserve verbatim text. Reasoning benchmarks (GSM8K, BBH) test different compression tolerance profiles.
- **Model version drift.** Papers from 2023 using GPT-3.5-turbo-0301 and papers from 2024 using GPT-3.5-turbo-1106 are testing different models; performance gaps may reflect model changes rather than compression method differences.
- **Selective evaluation.** LLMLingua's headline 20x figure is from the ICL demonstration-compression regime. In RAG contexts, the same approach produces lower compression ratios with similar or worse performance than extractive alternatives [10].

---

## Structured Comparison Table

| Technique | Year | Compression Ratio | Quality Preservation | Implementation Cost | Use Case |
|---|---|---|---|---|---|
| LLMLingua | 2023 | Up to 20x (token) | ~1.5 pt drop avg; task-variable | Medium — requires proxy LM at inference | ICL demonstrations, long prompts |
| LLMLingua-2 | 2024 | 2x-5x (token) | Competitive with LLMLingua; task-agnostic | Low — classifier, no proxy LM | Any prompt type; production deployment |
| LongLLMLingua | 2023 | ~4x (token) | +21.4% on NaturalQuestions (vs. full prompt) | Medium — requires document reordering | Long-context RAG |
| ICAE | 2023 | 4x-16x (token→slots) | Good on general tasks; model-specific | High — requires LoRA fine-tuning | Long context, single-model deployment |
| AutoCompressor | 2023 | ~4x (token→vectors) | Good on ICL; perplexity improvements | High — requires fine-tuning | Long document pre-computation, RAG |
| Gist Tokens | 2023 | Up to 26x | Minimal loss on instruction following | Medium — instruction fine-tuning required | Static/reusable prompt compression |
| RECOMP | 2023 | ~17x (documents to 6% length) | Minimal loss on QA, LM tasks | High — separate compressor training | RAG document compression |
| xRAG | 2024 | ~100-300x (doc→1 token) | +10% avg on knowledge-intensive tasks | High — modality bridge training; no API use | Dense-retrieval RAG only |
| Selective Context | 2023 | ~2x (50% reduction) | BERTscore drop 0.023; minor | Low — inference-time scoring only | General text, no fine-tuning |
| StreamingLLM | 2023 | KV cache (fixed window) | Stable perplexity; not semantic compression | Low — no training needed | Streaming/infinite context inference |
| LazyLLM | 2024 | 2.34x prefilling speedup | Matches full-context accuracy | Low — no fine-tuning | Long-context inference acceleration |
| Gist/CCoT output | 2023/2024 | 48.7% length reduction (output) | Negligible most tasks; math cliff (27.69%) | None — prompt-only | Output token cost reduction |
| TALE | 2024 | 68.9% CoT token reduction | <5% accuracy loss | None — prompt-only | Reasoning chain compression |
| Caveman | 2024 | 45% output reduction | Generally maintained; code unverified | None — style instruction | Output verbosity reduction |
| GZIP + k-NN | 2023 | N/A (similarity metric) | Beats BERT on OOD | None — no training | Low-resource text classification |
| BPE tokenization | 2016/ongoing | 3-5x vs. byte sequences | Baseline for all LLM work | Built into model training | Universal sequence compression |

---

## Coverage

**Verified with evidence:**
- LLMLingua and variants (LLMLingua-2, LongLLMLingua) — compression ratios, benchmarks, methodology
- Soft-prompt learned compression: Gist tokens, AutoCompressor, ICAE — architecture, training, results
- RAG-specific compression: RECOMP (extractive + abstractive), xRAG (embedding-based one-token extreme)
- "Lost in the middle" positional degradation and its mechanistic follow-ups
- KV cache compression: StreamingLLM (attention sinks), LazyLLM (dynamic token pruning)
- Token budget / CoT compression: CCoT and TALE framework
- Caveman/compressed-register output experiments — empirical token reduction results
- BPE as compression and the theoretical compression-prediction equivalence (Language Modeling Is Compression)
- GZIP as semantic similarity proxy (parameter-free classification)
- Survey-level benchmarking across methods (2024 prompt compression survey, characterization study)

**Uncertain:**
- AutoCompressor vs. ICAE direct comparison — both claim ~4x compression but on different context lengths and evaluation conditions; no common benchmark found
- Caveman-style input compression (not output) — anecdotal evidence only; no peer-reviewed paper
- Whether LLMLingua-2's speed advantage translates to quality parity or superiority under identical conditions vs. LLMLingua on reasoning-heavy tasks — the ACL 2024 paper tests different benchmark configurations than EMNLP 2023
- Long-term hallucination rates under compression — the 2025 empirical study flags this but doesn't quantify by method

**Not found:**
- Peer-reviewed systematic comparison of caveman/telegram-style input prompt compression on comprehension benchmarks
- Compression ratio benchmarks for structured outputs (JSON, code) under prompt compression — code quality impact uncharacterized
- Multi-lingual prompt compression benchmarks — tokenization efficiency disparities documented but not compression quality tradeoffs

**Conflicts:**
- LLMLingua "20x compression" vs. Characterizing Prompt Compression [10] finding that extractive methods outperform token pruning: the conflict resolves partially by task — LLMLingua's 20x is on ICL demonstrations where redundancy is high; extractive methods' advantage is on dense QA retrieval where verbatim preservation matters
- Abstractive compression performance: RECOMP's abstractive compressor performs well at 6% compression on open-domain QA, while the 2024 characterization study finds abstractive methods underperform extractive ones at 4.5x. Likely explanation: RECOMP's abstractive compressor is task-specifically distilled from GPT-4; the characterization study uses off-the-shelf summarization models — task-specific training changes the calculus significantly
- Caveman output compression claim of 45% token reduction with maintained quality conflicts with CCoT's documented 27.69% math accuracy drop on GPT-3.5 — both are right, both are measuring different things (Caveman measures diverse tasks averaged; CCoT isolates math reasoning specifically)
