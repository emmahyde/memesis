# Requirements: Memesis Memory Intelligence

**Defined:** 2026-03-29
**Core Value:** When a memory is injected, it should feel like recognition — not lookup.

## v1 Requirements

### Cleanup

- [ ] **CLEAN-01**: Commit Peewee ORM migration (Tasks 1-3) with atomic commits
- [ ] **CLEAN-02**: Write all 5 research files to disk from agent output transcripts
- [ ] **CLEAN-03**: Remove `file_path` from Memory model (greenfield, no migration needed)
- [ ] **CLEAN-04**: Stop creating stale stage directories (consolidated/, crystallized/, instinctive/) in database.py
- [ ] **CLEAN-05**: Add timestamp defaults to NarrativeThread model
- [ ] **CLEAN-06**: Remove unused imports (BooleanField, CharField, ForeignKeyField) from models.py

### Foundation

- [ ] **FOUND-01**: Implement Hybrid RRF retrieval — fuse FTS5 BM25 + sqlite-vec KNN using Reciprocal Rank Fusion (~30 lines, 0 deps)
- [ ] **FOUND-02**: Wire user prompt text into Tier 2 injection path (currently context-free, only project match)
- [ ] **FOUND-03**: Implement Thompson sampling for memory selection using Beta(usage_count+1, unused_count+1) from stdlib
- [ ] **FOUND-04**: Add provenance signals at injection time — "established across N sessions over M weeks" metadata in injection format

### Observation Quality

- [ ] **OBSV-01**: Create OrientingDetector — rule-based patterns catching corrections ("no, that's wrong"), emphasis ("remember this"), error spikes, pacing breaks
- [ ] **OBSV-02**: Create habituation baseline — per-project event frequency model suppressing routine events (habituation_factor = 1.0 - expected_frequency)
- [ ] **OBSV-03**: Implement somatic markers — emotional valence classification (neutral/friction/surprise/delight) at observation time with importance bump
- [ ] **OBSV-04**: Add replay priority — sort observations by salience (correction > pushback > novelty > recency) before presenting to consolidation LLM

### Memory Lifecycle

- [ ] **LIFE-01**: Implement SM-2 spaced injection — three new fields (next_injection_due, injection_ease_factor, injection_interval_days), hard suppression when not due
- [ ] **LIFE-02**: Add reconsolidation at PreCompact — when injected memories appear in session, check if session content confirms/contradicts/refines them, update before session ends
- [ ] **LIFE-03**: Implement saturation decay — penalize memories with high injection_count but low usage_count in relevance formula (saturation_penalty = min(0.3, unused_injections * 0.05))
- [ ] **LIFE-04**: Add integration factor to relevance — memories with no thread membership, no tag co-occurrence, no reinforcement after 30 days get accelerated decay

### Advanced Retrieval

- [ ] **RETR-01**: Implement 1-hop graph expansion — after hybrid search seeds, expand to thread neighbors and topical edges (new memory_edges table, pre-computed nightly)
- [ ] **RETR-02**: Create ghost coherence check — periodic LLM call comparing self-model claims against actual memory evidence, flag divergences as contradictions

## v2 Requirements (Future Phases)

### Prospective Memory
- **PROS-01**: Prospective memory system — "when X happens, remind me Y" via new prospective_memories table with trigger conditions
- **PROS-02**: Intent detection in observations — flag "next time," "remind me," "when we're working on" language

### Sensory Fusion
- **SENS-01**: ContextPercept multisensory fusion — fuse user text + errors + tests + git state into joint signal with temporal binding window
- **SENS-02**: ActivePrimeSet — injected memories prime the observation gate for topically related events with per-turn decay

### Advanced Learning
- **LEARN-01**: Context-conditioned importance updates — stop penalizing memories for wrong-project injections
- **LEARN-02**: Constitutive vs instrumental memory tagging — identity/preference memories resist crystallization freezing

## Out of Scope

| Feature | Reason |
|---------|--------|
| Full GraphRAG entity extraction | Too expensive for plugin context — LLM processing at index time for every memory |
| HNSW/ANN indexes | Brute force KNN is 1-3ms at N=1000, not a bottleneck until 50K+ |
| Cross-encoder re-ranker training | Need ~500 labeled (query, memory, was_used) triples first |
| LLM re-ranker for passive injection | 200-500ms latency exceeds 3s hook timeout budget |
| Memory browser UI | Separate project |
| Encoding specificity (multi-dim context matching) | Deferred — high value but requires new encoding_context JSON field + multi-dim scoring |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CLEAN-01 | Phase 1 | Pending |
| CLEAN-02 | Phase 2 | Pending |
| CLEAN-03 | Phase 3 | Pending |
| CLEAN-04 | Phase 4 | Pending |
| CLEAN-05 | Phase 5 | Pending |
| CLEAN-06 | Phase 6 | Pending |
| FOUND-01 | Phase 7 | Pending |
| FOUND-02 | Phase 8 | Pending |
| FOUND-03 | Phase 9 | Pending |
| FOUND-04 | Phase 10 | Pending |
| OBSV-01 | Phase 11 | Pending |
| OBSV-02 | Phase 12 | Pending |
| OBSV-03 | Phase 13 | Pending |
| OBSV-04 | Phase 14 | Pending |
| LIFE-01 | Phase 15 | Pending |
| LIFE-02 | Phase 16 | Pending |
| LIFE-03 | Phase 17 | Pending |
| LIFE-04 | Phase 18 | Pending |
| RETR-01 | Phase 19 | Pending |
| RETR-02 | Phase 20 | Pending |

**Coverage:**
- v1 requirements: 20 total
- Mapped to phases: 20
- Unmapped: 0

---
*Requirements defined: 2026-03-29*
*Last updated: 2026-03-29 after initial definition*
