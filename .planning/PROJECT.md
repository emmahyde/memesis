# Memesis: Memory Intelligence

## What This Is

An intelligent memory injection and retrieval system for Claude Code that captures observations during coding sessions, consolidates them into durable memories through a multi-stage lifecycle, and injects relevant memories into future sessions. Currently uses FTS-only keyword matching for injection — this project adds semantic retrieval, noise filtering, feedback learning, and neuroscience-inspired memory lifecycle improvements.

## Core Value

When a memory is injected, it should feel like the assistant *recognized* something — not like it looked something up. The system should surface the right memory at the right time with high precision and zero noise.

## Requirements

### Validated

- Memory observation capture during sessions (ephemeral buffer)
- Multi-stage consolidation (ephemeral -> consolidated -> crystallized -> instinctive)
- FTS5 full-text search across memories
- sqlite-vec vector embeddings (512-dim, Bedrock Titan v2)
- Peewee ORM model layer (Memory, NarrativeThread, ThreadMember, RetrievalLog, ConsolidationLog)
- Relevance decay, archival, rehydration
- Narrative thread detection and synthesis
- Plugin packaging with venv + mise

### Active

**Cleanup (tech debt from ORM migration):**
- [ ] Commit Peewee migration, write research files, fix model remnants
- [ ] Remove file_path from Memory model, fix stage dirs, add timestamp defaults

**Foundation (enables all retrieval improvements):**
- [ ] Hybrid RRF retrieval — fuse FTS5 + sqlite-vec with Reciprocal Rank Fusion
- [ ] Wire user prompt into Tier 2 injection (currently context-free)
- [ ] Thompson sampling for memory selection using injection/usage counts
- [ ] Provenance signals at injection time ("established across N sessions over M weeks")

**Observation quality (reduce noise in, increase signal out):**
- [ ] OrientingDetector — rule-based patterns for corrections, emphasis, error spikes
- [ ] Habituation baseline — per-project event frequency model, suppress routine events
- [ ] Somatic markers — emotional valence classification, importance bump for friction/surprise
- [ ] Replay priority — sort observations by salience before consolidation LLM

**Memory lifecycle (make memories improve over time):**
- [ ] SM-2 spaced injection — memories the user engages with get longer re-injection intervals
- [ ] Reconsolidation at PreCompact — update memories when session content confirms/contradicts them
- [ ] Saturation decay — penalize memories injected repeatedly without was_used=1
- [ ] Integration factor — isolated memories (no threads, no tag overlap) decay faster

**Advanced retrieval (build on foundation):**
- [ ] 1-hop graph expansion over thread/topical edges after hybrid search
- [ ] Ghost coherence check — periodic LLM comparing self-model claims against evidence

### Out of Scope

- Full GraphRAG entity extraction (too expensive for plugin context)
- HNSW/ANN indexes (brute force is 1-3ms at N=1000, not a bottleneck)
- Cross-encoder re-ranker training (need ~500 labeled examples first)
- LLM re-ranker for passive injection (latency budget too tight)
- Prospective memory ("remind me when X") — future milestone
- ContextPercept multisensory fusion — future milestone
- Memory browser UI — separate project

## Context

Research conducted across 5 dimensions informing this design:
- **Algorithms**: Hybrid RRF, Thompson sampling, graph expansion, contextual bandits (`.context/research/2026-03-29-retrieval-algorithms.md`)
- **Arxiv papers**: LongMemEval, MemoryAgentBench, Neuromem, Self-RAG (`.context/research/2026-03-29-llm-memory-papers.md`)
- **Neuroscience**: Hippocampal replay, spreading activation, reconsolidation, SM-2 spacing, somatic markers (`.context/research/2026-03-29-neuroscience-memory-patterns.md`)
- **Sense memory**: Sensory gate, orienting response, habituation, priming (`.context/research/2026-03-29-sense-memory.md`)
- **Sci-fi hivemind**: Borg assimilation, Ancillary Justice distributed perception, Tines pack coherence (`.context/research/2026-03-29-hivemind-scifi.md`)

Key cross-dimensional convergence: every dimension independently identified that retrieval should change system state (Thompson sampling updates arm distributions, reconsolidation updates memories, priming lowers gate thresholds). "Living memory" = retrieval is metabolic, not read-only.

## Constraints

- **Latency**: UserPromptSubmit hook has 3s timeout. All per-prompt retrieval must complete within ~500ms.
- **Dependencies**: Plugin runs in a venv. Peewee + apsw + sqlite-vec + boto3 + nltk + scikit-learn. No PyTorch.
- **Bedrock API**: Embedding calls cost ~200ms each. Minimize per-prompt API calls (pre-compute at write time).
- **Scale**: ~1000 memories currently. Design for 10K but don't optimize for 100K+.
- **Signal quality**: `was_used` is a keyword heuristic in FeedbackLoop, not ground truth. Weight bandit/RL estimates conservatively.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Peewee ORM over raw sqlite3 | Ruby dev philosophy — models as API, legibility, convention over configuration | -- Pending |
| Hybrid RRF over pure vector search | FTS catches exact terms, vec catches semantics. RRF merges without score normalization | -- Pending |
| SM-2 for injection spacing | 259/271 meta-analytic cases favor spaced over massed repetition (neuroscience research) | -- Pending |
| Thompson sampling over UCB1 | Handles cold-start gracefully via Beta prior, no hyperparameters, stdlib-only | -- Pending |
| OrientingDetector as rule-based | Fast path for highest-signal moments (corrections, errors). LLM analysis deferred to consolidation | -- Pending |
| Sense Memory Layer upstream of buffer | Filter noise before ephemeral write, not during consolidation. Reduces LLM token waste | -- Pending |

---
*Last updated: 2026-03-29 after initialization*
