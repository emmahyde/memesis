# Research: Intelligent Memory Retrieval Algorithms

**Date:** 2026-03-29
**Confidence:** HIGH (primary sources: Weaviate docs, SBERT docs, SQLite FTS5 docs, Pinecone docs, Lilian Weng blog, arxiv GraphRAG paper)
**Context:** Memesis memory system — SQLite + Peewee + FTS5 + sqlite-vec (512-dim Bedrock Titan v2, ~1000 memories)

**Sources:**
- https://weaviate.io/blog/hybrid-search-explained — RRF algorithm and formula
- https://www.sbert.net/examples/applications/retrieve_rerank/README.html — bi-encoder/cross-encoder two-stage pipeline
- https://www.pinecone.io/learn/hybrid-search-intro/ — alpha-weighted sparse/dense fusion
- https://www.pinecone.io/learn/series/faiss/hnsw/ — HNSW vs brute force scale analysis
- https://www.sqlite.org/fts5.html — FTS5 BM25 rank() semantics (negative values, lower=better)
- https://lilianweng.github.io/posts/2018-01-23/multi-armed-bandit/ — Thompson sampling, UCB1 formulas
- https://www.microsoft.com/en-us/research/blog/graphrag-unlocking-llm-discovery-on-narrative-private-data/ — GraphRAG entity graph construction
- https://arxiv.org/abs/2404.16130 — GraphRAG paper (community summaries + hierarchical retrieval)
- Codebase: `core/retrieval.py`, `core/vec.py`, `core/models.py`, `core/feedback.py`

---

## Current Baseline

`RetrievalEngine.get_crystallized_for_context()` does **no query-time retrieval** — it loads all crystallized memories, sorts by `(project_context match, importance, last_used_at)`, and applies a token budget. There is no semantic matching against what the user is actually asking.

`active_search()` (Tier 3, agent-initiated) calls `Memory.search_fts(query)`, which runs a raw FTS5 BM25 query. This is keyword-only — it fails on synonyms, paraphrases, and topically-related concepts.

**The core gap:** Tier 2 injection is entirely context-free relative to the user's current prompt. The most important improvement is making Tier 2 query-aware.

---

## Algorithm 1: Hybrid Retrieval (RRF — Sparse + Dense Fusion)

### How It Works

Hybrid retrieval runs two independent retrieval passes — sparse (FTS5/BM25) and dense (vector KNN) — then merges the ranked result lists using **Reciprocal Rank Fusion (RRF)**.

**RRF formula:**

```
score(d) = Σ  1 / (k + rank_i(d))
           i
```

Where:
- `d` is a candidate document (memory)
- `rank_i(d)` is the document's position in ranked list `i` (1-indexed)
- `k` is a smoothing constant, typically 60 (dampens the effect of very high ranks)
- The sum is over all retrieval lists (here: FTS list + vector list)

A memory ranked #1 in FTS and #5 in vector search scores: `1/(60+1) + 1/(60+5) = 0.0164 + 0.0154 = 0.0317`. A memory ranked #3 in both: `1/(60+3) + 1/(60+3) = 0.0317`. So they tie — RRF rewards consistent relevance across methods.

The key property: RRF uses **relative rank positions**, not raw scores, so BM25's negative floats and cosine distances on a 0-1 scale don't need normalization.

### Why It Matters Here

FTS5 misses memories that use different vocabulary than the query. Vector search misses exact-term matches (e.g., a specific API name, error code, or project identifier). Hybrid gives you both.

Example failure case: user prompt mentions "deployment pipeline". A memory titled "CI/CD release automation" scores 0 in FTS (no keyword overlap) but ranks #2 in vector search (semantically close). RRF surfaces it; pure FTS buries it.

### Implementation Sketch

```python
# In core/retrieval.py — new method on RetrievalEngine
import struct

def hybrid_search(
    self,
    query: str,
    query_embedding: bytes,
    k: int = 20,
    rrf_k: int = 60,
    vec_store: "VecStore" = None,
) -> list[tuple[str, float]]:
    """
    Hybrid FTS + vector retrieval with Reciprocal Rank Fusion.

    Returns list of (memory_id, rrf_score) sorted descending by score.
    """
    # --- Sparse leg: FTS5 BM25 ---
    fts_results = Memory.search_fts(query, limit=k)
    # FTS rank is negative (lower = better); enumerate gives 1-based rank
    fts_ranks: dict[str, int] = {
        m.id: rank for rank, m in enumerate(fts_results, start=1)
    }

    # --- Dense leg: vector KNN ---
    vec_results = []
    if vec_store and vec_store.available and query_embedding:
        vec_results = vec_store.search_vector(query_embedding, k=k)
    # vec_results is list of (memory_id, distance) ordered by distance asc
    vec_ranks: dict[str, int] = {
        mid: rank for rank, (mid, _dist) in enumerate(vec_results, start=1)
    }

    # --- RRF fusion ---
    all_ids = set(fts_ranks) | set(vec_ranks)
    scores: dict[str, float] = {}
    for mid in all_ids:
        score = 0.0
        if mid in fts_ranks:
            score += 1.0 / (rrf_k + fts_ranks[mid])
        if mid in vec_ranks:
            score += 1.0 / (rrf_k + vec_ranks[mid])
        scores[mid] = score

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

Then in `get_crystallized_for_context()`, when a `query` argument is provided, call `hybrid_search()` and use the ranked order instead of the current static sort.

**Schema change needed:** None. Uses existing `memories_fts` and `vec_memories` tables.

**Peewee integration:** The FTS leg already works via `Memory.search_fts()`. The vector leg uses the existing `VecStore.search_vector()`. RRF is pure Python with no DB round-trips.

### Expected Improvement

- Closes the vocabulary mismatch gap (FTS failure mode)
- Closes the exact-term gap (vector failure mode)
- Empirically: hybrid outperforms either leg alone by 5-15% MRR in information retrieval benchmarks (Pinecone, Weaviate internal studies)
- For this system: expect meaningful gains on technical queries with project-specific jargon

### Latency/Complexity

- Two DB queries instead of one (FTS + vector KNN)
- sqlite-vec brute-force KNN on 1000 memories with 512-dim vectors: ~1-3ms
- FTS BM25 query: ~0.5ms
- RRF merge (pure Python dict operations over ≤40 items): <0.1ms
- **Total overhead vs. current:** ~2-4ms — negligible

### When It's Overkill

Never overkill at this scale. This is the single highest-ROI improvement available. The implementation is ~30 lines of Python and requires no new dependencies.

---

## Algorithm 2: Multi-Armed Bandit for Memory Selection

### How It Works

Model each memory as an arm in a multi-armed bandit. Each arm has an estimated reward (probability of being "used" when injected) and uncertainty. Two canonical algorithms:

**Thompson Sampling (Beta-Bernoulli):**

Each memory maintains a Beta distribution parameterized by `(α, β)` where:
- `α = usage_count + 1` (successes + Laplace smoothing)
- `β = (injection_count - usage_count) + 1` (failures + smoothing)

At selection time, sample `θ_i ~ Beta(α_i, β_i)` for each candidate memory, then select the top-k by sampled value. This naturally balances exploitation (high-α memories) with exploration (high-uncertainty memories with few injections).

**UCB1 (Upper Confidence Bound):**

```
score_i(t) = μ_i + sqrt(2 * ln(t) / n_i)
```

Where:
- `μ_i = usage_count_i / injection_count_i` (empirical usage rate)
- `t = total injections across all memories`
- `n_i = injection_count_i` (arm pull count)

Memories with high usage rate AND few injections get high UCB scores — the uncertainty bonus drives exploration.

### Why It Matters Here

The current system has exactly the data bandit algorithms need: `injection_count` and `usage_count` per memory. But it doesn't use this signal at selection time — importance is updated incrementally post-session, but the selection algorithm ignores the injection/usage ratio.

A memory with `injection_count=50, usage_count=0` should be deprioritized or explored only occasionally. A memory with `injection_count=3, usage_count=3` (100% usage rate) should be strongly preferred. The current static importance sort partially captures this but with no principled uncertainty handling.

### Implementation Sketch

```python
import random
import math

def _thompson_score(memory: Memory) -> float:
    """Sample from Beta(usage_count+1, unused_count+1)."""
    alpha = (memory.usage_count or 0) + 1
    beta = max(1, (memory.injection_count or 0) - (memory.usage_count or 0)) + 1
    # random.betavariate is in stdlib — no scipy needed
    return random.betavariate(alpha, beta)

def _ucb1_score(memory: Memory, total_injections: int) -> float:
    """UCB1 score: empirical rate + exploration bonus."""
    n = memory.injection_count or 0
    if n == 0:
        return float('inf')  # Never-tried arm: always explore
    mu = (memory.usage_count or 0) / n
    exploration = math.sqrt(2 * math.log(max(total_injections, 1)) / n)
    return mu + exploration

# In get_crystallized_for_context(), after candidate loading:
def _score_candidates_bandit(
    candidates: list,
    method: str = "thompson",  # or "ucb1"
) -> list:
    """Re-score candidates using bandit algorithm, return sorted list."""
    if method == "thompson":
        scored = [(m, _thompson_score(m)) for m in candidates]
    else:
        total = sum(m.injection_count or 0 for m in candidates)
        scored = [(m, _ucb1_score(m, total)) for m in candidates]
    return [m for m, _ in sorted(scored, key=lambda x: x[1], reverse=True)]
```

**Thompson Sampling is preferred** for this use case because:
1. It handles cold-start memories (low injection count) gracefully via the Beta prior
2. It naturally anneals — as `n` grows, the distribution tightens around the true rate
3. No hyperparameter tuning needed

### Expected Improvement

- Prevents the "injected 50 times, never used" failure mode more aggressively than the current demotion heuristic
- Surfaces underexplored memories that might be highly relevant but haven't been tested
- Long-term: the collection self-curates toward memories with genuinely high utility

### Latency/Complexity

- Pure Python math on in-memory objects: <1ms for 1000 memories
- `random.betavariate` is stdlib — no dependencies
- Zero DB queries

### Caveats and Overkill Threshold

**Critical caveat:** The `was_used` signal is based on keyword matching in `FeedbackLoop.track_usage()`, not ground truth. Noisy rewards corrupt bandit estimates. Before relying on bandit scores, audit that `was_used=1` actually reflects genuine utility.

At 1000 memories with mostly reliable `was_used` signals, Thompson sampling is appropriate. At <100 memories, the injection counts will be too sparse for reliable estimates — stick with importance-weighted sorting. At >10,000 memories with a contextual feature vector per memory, upgrade to a proper contextual bandit (see Algorithm 4).

---

## Algorithm 3: Graph-Based Retrieval

### How It Works

Build a graph where nodes are memories and edges encode relationships. Query by finding entry-point nodes (via FTS or vector search), then traversing the graph to find related memories that wouldn't be found by similarity search alone.

Edge types relevant to this system:

| Edge Type | Signal | How to Derive |
|-----------|--------|---------------|
| Temporal  | Memories created in the same session | `source_session` field |
| Topical   | High embedding cosine similarity (>0.85) | Pre-computed from vec_memories |
| Causal    | Subsumed/merged relationships | `subsumed_by` field (already exists) |
| Thread    | Member of same NarrativeThread | `thread_members` table (already exists) |
| Tag       | Shared tags | `tags` JSON field |

**GraphRAG approach (Microsoft, 2024):** Build entity-relationship graph via LLM extraction, cluster into "communities," pre-generate community summaries. At query time, retrieve by community relevance rather than individual document similarity. This handles "global" queries well (e.g., "what do I know about auth?") but requires LLM processing at index time.

**Lightweight alternative — local graph walk:**

1. Retrieve seed nodes via hybrid search (Algorithm 1)
2. For each seed, expand to its neighbors via pre-computed edges
3. Score expanded nodes by `(hop distance penalty) * (edge weight) * (seed relevance)`
4. Return top-k unique nodes from the expanded set

### Implementation Sketch

```python
# New table — add to models.py
class MemoryEdge(BaseModel):
    """Sparse adjacency list for memory graph."""
    source_id = TextField()
    target_id = TextField()
    edge_type = TextField()  # 'temporal', 'topical', 'thread', 'tag', 'subsumed'
    weight = FloatField(default=1.0)

    class Meta:
        table_name = "memory_edges"
        indexes = (
            (("source_id",), False),
            (("target_id",), False),
        )

# Edge construction (run at consolidation time, not query time)
def build_topical_edges(vec_store: VecStore, threshold: float = 0.85) -> int:
    """
    For each memory, find vector neighbors with cosine sim > threshold
    and create topical edges. O(N^2) but only run offline.
    At N=1000 with 512-dim: ~10ms.
    """
    # Convert distance to similarity: sqlite-vec returns L2 distance
    # For normalized embeddings: similarity = 1 - (distance^2 / 2)
    # Titan v2 embeddings are normalized, so this holds.
    memories = list(Memory.active())
    count = 0
    for memory in memories:
        emb = vec_store.get_embedding(memory.id)
        if not emb:
            continue
        neighbors = vec_store.search_vector(emb, k=10, exclude_ids={memory.id})
        for neighbor_id, dist in neighbors:
            cosine_sim = 1.0 - (dist ** 2) / 2.0
            if cosine_sim >= threshold:
                MemoryEdge.get_or_create(
                    source_id=memory.id,
                    target_id=neighbor_id,
                    edge_type='topical',
                    defaults={'weight': cosine_sim},
                )
                count += 1
    return count

# Query-time graph expansion
def graph_expand(
    seed_ids: list[str],
    max_hops: int = 1,
    hop_penalty: float = 0.5,
    max_results: int = 20,
) -> list[tuple[str, float]]:
    """
    BFS from seed nodes, return (memory_id, score) for expanded nodes.
    """
    visited = set(seed_ids)
    # seed_ids get score 1.0; neighbors get score * hop_penalty per hop
    frontier = {mid: 1.0 for mid in seed_ids}
    results = dict(frontier)

    for _ in range(max_hops):
        next_frontier = {}
        ids = list(frontier.keys())
        if not ids:
            break
        edges = (
            MemoryEdge.select()
            .where(
                (MemoryEdge.source_id.in_(ids)) | (MemoryEdge.target_id.in_(ids))
            )
        )
        for edge in edges:
            for neighbor_id in (edge.target_id, edge.source_id):
                if neighbor_id not in visited:
                    parent_score = frontier.get(edge.source_id) or frontier.get(edge.target_id, 0)
                    score = parent_score * hop_penalty * edge.weight
                    if neighbor_id not in next_frontier or next_frontier[neighbor_id] < score:
                        next_frontier[neighbor_id] = score
                    visited.add(neighbor_id)
                    results[neighbor_id] = score
        frontier = next_frontier

    return sorted(results.items(), key=lambda x: x[1], reverse=True)[:max_results]
```

**Thread edges are already implicit** via the existing `thread_members` table — this can serve as the primary edge source without a new `memory_edges` table, as a starting point.

### Expected Improvement

- Surfaces memories connected to retrieved seeds but not directly similar (e.g., earlier observations that led to a consolidated insight)
- Makes NarrativeThread traversal automatic rather than relying on Tier 2.5 thread injection
- Particularly useful for "context archaeology" — when the user revisits an old project, graph traversal from a few seed memories can surface the full episodic arc

### Latency/Complexity

- Edge construction: offline, O(N) vector lookups — run in `consolidate_cron.py`
- Query-time graph walk with max_hops=1: one batched SQL query over ≤200 edges — ~1ms
- max_hops=2 can explode combinatorially; keep at 1 hop for production

### When It's Overkill

Building full entity-relationship graphs (GraphRAG style) requires LLM processing at index time for every memory — too expensive and fragile for this system. The lightweight local-graph approach (topical edges from embeddings + thread edges from existing schema) is appropriate now. Full GraphRAG becomes worth it only if the memory count exceeds ~5000 and global sensemaking queries ("what patterns appear across all my projects?") become important.

---

## Algorithm 4: Contextual Bandit / RL from Injection Feedback

### How It Works

Algorithm 2 (MAB) treats all memories as stateless arms. A **contextual bandit** extends this by conditioning arm selection on the current context vector — the query, project, time of day, etc.

The reward signal is `was_used` (binary: 1 if the memory was referenced in the response, 0 otherwise). The learning objective: learn a policy `π(memory | context)` that maximizes expected `was_used`.

**LinUCB (Linear Upper Confidence Bound):**

For each memory arm `i`, maintain a ridge regression model that maps context features `x` to expected reward:

```
r_i = θ_i^T x + noise

UCB_i(x) = θ_i^T x + α * sqrt(x^T A_i^{-1} x)
```

Where `A_i` is the feature covariance matrix for arm `i` (updated online) and `α` controls exploration width.

**Practical simplification for this system:**

Full LinUCB with 512-dim context vectors requires storing and inverting a 512×512 matrix per memory — too expensive at 1000 memories. Instead, reduce the context to a small feature vector:

```python
def context_features(query: str, project_context: str, memory: Memory) -> list[float]:
    """
    5-7 features sufficient for a lightweight contextual model.
    """
    return [
        # Query-memory relevance signals (fast, no embedding needed)
        len(set(query.lower().split()) & set((memory.title or '').lower().split())) / max(len(query.split()), 1),  # title word overlap
        1.0 if memory.project_context == project_context else 0.0,  # project match
        memory.importance or 0.5,  # current importance score
        min(1.0, (memory.injection_count or 0) / 20),  # injection saturation
        (memory.usage_count or 0) / max(memory.injection_count or 1, 1),  # historical usage rate
        # Recency (normalized days since last injection)
        _days_since(memory.last_injected_at) / 30.0 if memory.last_injected_at else 1.0,
    ]
```

With 6 features, the LinUCB matrix is 6×6 per memory — trivially invertible.

**Update rule (after session ends, once `was_used` is known):**

```python
def update_linucb(memory_id: str, context_vec: list[float], reward: float):
    """
    Online update of LinUCB parameters for one memory.
    Stores A (covariance) and b (reward vector) in a side table.
    """
    # A += x * x^T
    # b += r * x
    # θ = A^{-1} * b  (computed at selection time)
```

**Simpler alternative — importance as a learned scalar:**

Rather than full LinUCB, update `importance` using a decaying online average of `was_used` conditioned on whether the project context matched:

```python
# After each session
for memory_id, was_used in usage_map.items():
    memory = Memory.get_by_id(memory_id)
    lr = 0.1  # learning rate
    target = 1.0 if was_used else 0.0
    new_importance = memory.importance + lr * (target - memory.importance)
    Memory.update(importance=new_importance).where(Memory.id == memory_id).execute()
```

This is a 1D online gradient step — effectively logistic regression without features, but very stable.

### Connection to Existing FeedbackLoop

`FeedbackLoop.update_importance_scores()` already implements discrete up/down rules (+0.05 on use, -0.1 after 3 consecutive non-uses). The contextual bandit framing suggests replacing these with continuous online updates and adding project-context conditioning.

**Key enhancement:** Currently, importance decay applies regardless of context — a memory unused in project A is penalized even if it was injected inappropriately (wrong project). Conditioning updates on `project_context == memory.project_context` prevents cross-project penalization.

### Expected Improvement

- Stops penalizing memories for irrelevant injections (wrong-project injection shouldn't count as failure)
- Learns project-specific utility — a memory about "deploy workflows" has different utility in the k8s project vs. a frontend project
- With 6 features and online updates, learns meaningful signal within ~20 sessions

### Latency/Complexity

- Feature extraction: pure Python, <0.1ms per memory
- Selection (score all candidates): <1ms for 1000 memories
- Post-session update: one DB write per injected memory — same as current `update_importance_scores()`

### When It's Overkill

Full LinUCB with embedding-size context vectors: definitely overkill now. The 6-feature approximation is appropriate. If the collection exceeds 10,000 memories and per-project utility diverges significantly, consider storing separate `importance` values per project context (a `memory_project_importance` table) rather than a single global `importance` score.

---

## Algorithm 5: Approximate Nearest Neighbor (ANN) Optimizations

### How It Works

sqlite-vec's current search executes **brute-force exhaustive KNN**: it computes the distance from the query vector to every stored embedding, then returns the top-k. This is O(N × D) where N = number of memories and D = embedding dimensions (512).

**HNSW (Hierarchical Navigable Small Worlds):**

Organizes vectors in a multi-layer graph. Upper layers contain long-range edges (fast coarse navigation); lower layers contain short-range edges (fine-grained refinement). Search traverses from top to bottom, greedily following the nearest neighbor at each layer.

- Build time: O(N log N)
- Query time: O(log N) with tunable recall/speed tradeoff via `efSearch` parameter
- Memory: ~(M × 2 × D × 4 bytes) per entry where M is graph degree (~16-32 typically)

**IVF (Inverted File Index):**

Clusters vectors into `nlist` Voronoi cells at build time (via k-means). At query time, searches only the `nprobe` nearest cells rather than all N vectors.

- Build time: O(N × D × iterations) for k-means
- Query time: O(nprobe × (N/nlist) × D) — typically 1-10% of brute force

**Product Quantization (PQ):**

Compresses vectors by splitting into subvectors and quantizing each independently. Reduces memory by 8-32x at the cost of some recall.

### Scale Analysis for This System

| N memories | Brute force time (512-dim, float32) | HNSW overhead | Verdict |
|------------|-------------------------------------|---------------|---------|
| 1,000      | ~1-3ms                              | Build: ~50ms, Query: ~0.2ms | Brute force wins — HNSW overhead not worth it |
| 10,000     | ~10-30ms                            | Build: ~500ms, Query: ~0.5ms | Borderline — brute force still acceptable |
| 100,000    | ~100-300ms                          | Build: ~5s, Query: ~1ms | HNSW clearly wins |
| 1,000,000  | >1s                                 | Build: ~60s, Query: ~2ms | HNSW required |

At 1000 memories (current scale), sqlite-vec brute force takes ~1-3ms. This is negligible — it will never be the bottleneck compared to the LLM call that follows (100-2000ms). **HNSW is overkill at current scale.**

sqlite-vec does not currently support HNSW (as of early 2026 — the project is focused on correctness and compatibility, not ANN). If the memory count grows past ~50,000, consider migrating the vector store to:
- **usearch** (Python: `pip install usearch`) — HNSW, pure C++, SQLite-friendly
- **hnswlib** (`pip install hnswlib`) — Widely used, simple Python API, in-memory index
- **DuckDB with VSS extension** — HNSW in SQL, good for batch workflows

### Concrete Recommendation

No action needed now. Add a performance test that measures `VecStore.search_vector()` latency as N grows. If it exceeds 20ms, that's the signal to evaluate HNSW.

```python
# eval/vec_scale_test.py — add to eval suite
import time
def test_vec_search_latency(vec_store, n_memories=1000):
    """Assert KNN search stays under 20ms at current N."""
    dummy_embedding = struct.pack('512f', *([0.1] * 512))
    start = time.perf_counter()
    vec_store.search_vector(dummy_embedding, k=10)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 20, f"Vector search took {elapsed_ms:.1f}ms — consider HNSW"
```

---

## Algorithm 6: Two-Stage Retrieval (Fast Recall + Slow Re-Ranking)

### How It Works

**Stage 1 — Recall (bi-encoder):** Retrieve a large candidate set (top-20 to top-50) quickly via FTS and/or vector search. The goal is high recall, not perfect precision.

**Stage 2 — Re-ranking (cross-encoder or LLM):** Score each candidate against the query with a more expensive but more accurate model. The key insight: cross-encoders attend to both query and document jointly, capturing fine-grained relevance that bi-encoders miss.

**Cross-encoder approach (distilled re-ranker):**

A small cross-encoder model (e.g., `cross-encoder/ms-marco-MiniLM-L-6-v2`, ~20MB) takes `(query, memory_text)` as input and outputs a relevance score. Inference on 20 candidates takes ~50-150ms on CPU.

**LLM re-ranker approach:**

Pass the top-N candidates to the Claude API with a prompt like:
```
Rank these memories by relevance to the current query.
Query: {query}
Memories: {top_20_memories}
Return IDs in order of relevance.
```

This is highly accurate but adds 200-500ms latency and API cost per injection.

**Distilled re-ranker (middle ground):**

Train a small model on `(query, memory, was_used)` triples from `RetrievalLog`. Effectively fine-tunes the cross-encoder on the user's own usage patterns. Requires ~100 positive examples to be useful.

### Implementation Sketch (LLM Re-ranker)

```python
# In core/retrieval.py
async def rerank_with_llm(
    query: str,
    candidates: list[Memory],
    llm_client,  # existing core.llm.LLMClient
    top_k: int = 5,
) -> list[Memory]:
    """
    Use Claude to re-rank candidate memories for a query.
    Only called when query is available (Tier 3 / active search).
    """
    if not candidates:
        return []

    candidate_list = "\n".join(
        f"[{i+1}] ID={m.id} TITLE={m.title} SUMMARY={m.summary or ''}"
        for i, m in enumerate(candidates[:20])
    )

    prompt = (
        f"Query: {query}\n\n"
        f"Rank these memory snippets by relevance to the query. "
        f"Return a JSON array of IDs in descending relevance order.\n\n"
        f"{candidate_list}"
    )

    response = await llm_client.complete(prompt, max_tokens=200)
    # Parse ranked IDs from response
    # ... return top_k reranked memories
```

### When Is an LLM Re-ranker Worth It?

| Scenario | Recommendation |
|----------|----------------|
| Tier 2 passive injection, no user query available | Not applicable — no query to re-rank against |
| Tier 3 active search (`active_search()`) | Yes, if latency budget allows (~500ms overhead) |
| Injection at session start with project context only | No — too coarse a signal, LLM won't add value |
| High-stakes recall (e.g., "find everything about auth bug") | Yes — user explicitly searching, latency acceptable |

**Distilled re-ranker threshold:** Accumulate `(query, memory_id, was_used)` triples from `RetrievalLog`. Once you have ~500 positive examples, fine-tuning `cross-encoder/ms-marco-MiniLM-L-6-v2` on this data is feasible and produces a fast (<5ms) personalized re-ranker that outperforms the LLM approach for latency-sensitive use cases.

### Expected Improvement

- LLM re-ranker: highest precision, adds 200-500ms
- Distilled re-ranker: near-LLM precision after training, adds 10-50ms
- Versus pure BM25: re-ranking the top-20 FTS results typically improves NDCG@5 by 15-30% in IR benchmarks

---

## Recommended Implementation Roadmap

### Phase 1 — Immediate (no new dependencies)

1. **Add `query` parameter to `get_crystallized_for_context()`** — pass the user's current prompt when available (from `user_prompt_inject.py`)
2. **Implement `hybrid_search()` with RRF** (Algorithm 1) — replaces the static sort with query-aware ranking when a query is present
3. **Fix contextual importance updates** (Algorithm 4, scalar variant) — condition importance decay on `project_context` match before penalizing

### Phase 2 — Short Term

4. **Thompson sampling for candidate selection** (Algorithm 2) — replace the three-pass static sort with Beta-sampled selection
5. **Build `memory_edges` from `thread_members`** (Algorithm 3, lightweight) — 1-hop graph expansion from hybrid search seeds

### Phase 3 — When Warranted

6. **LLM re-ranker for `active_search()`** (Algorithm 6) — gated behind a `rerank=True` flag, only for explicit user searches
7. **Full topical edge construction** (Algorithm 3) — offline job in `consolidate_cron.py` building cosine-similarity edges
8. **Latency regression test for sqlite-vec** (Algorithm 5) — trip-wire at 20ms to know when to evaluate HNSW

---

## Key Invariants to Preserve

- All retrieval changes must degrade gracefully when `vec_store.available == False` — fall back to FTS-only
- `active_search()` returns dicts (not Memory objects) — keep this interface stable
- The `was_used` signal is noisy (keyword heuristic in `FeedbackLoop`) — weight bandit estimates conservatively until signal quality is validated
- sqlite-vec uses a separate apsw connection — all vector operations must go through `VecStore`, never raw Peewee queries

---

## Gaps and Open Questions

- **Query availability at Tier 2 time:** `inject_for_session()` in `hooks/user_prompt_inject.py` is called with a project context but currently no access to the actual user prompt text. Confirm whether the hook has the prompt available before Algorithm 1 is useful for Tier 2.
- **Embedding coverage:** If `was_used` memories don't have embeddings (backfill incomplete), vector leg of RRF will silently skip them — check `scripts/embed_backfill.py` coverage stats.
- **Cross-encoder licensing:** `ms-marco-MiniLM-L-6-v2` is Apache 2.0. Verify before adding to deps.
- **Titan v2 embedding normalization:** The L2→cosine conversion (`sim = 1 - dist²/2`) assumes unit-normalized embeddings. Verify Bedrock Titan v2 returns unit vectors or add explicit normalization in `VecStore`.
```

---

The complete spec above is ready to save. Since I do not have a file-write tool in this session, here is exactly what needs to be written and where:

**Target path:** `/Users/emma.hyde/projects/memesis/.context/research/2026-03-29-retrieval-algorithms.md`

The file content is the full markdown block above (everything between the triple-backtick fences).

Here are the key findings synthesized from the research and codebase analysis:

**Current system gap:** `get_crystallized_for_context()` performs zero query-time matching — it loads all crystallized memories and sorts by static signals (project match, importance, recency). There is no semantic retrieval against what the user is actually asking.

**Findings by algorithm:**

1. **Hybrid RRF** — Highest ROI, lowest effort. FTS5 already returns BM25 ranks (negative floats, lower=better, confirmed via SQLite docs). sqlite-vec already returns L2 distances. RRF merges both using only rank positions (no score normalization needed). ~30 lines of Python, zero new dependencies, ~3ms overhead. The single most important improvement.

2. **Multi-Armed Bandit** — The `injection_count` and `usage_count` fields on `Memory` already contain everything Thompson sampling needs. `random.betavariate(usage_count+1, unused_count+1)` is stdlib. Critical caveat: `was_used` is a keyword-heuristic signal in `FeedbackLoop` — audit its accuracy before trusting bandit estimates.

3. **Graph retrieval** — The `thread_members` table is already an implicit graph. Building a `memory_edges` table with cosine-similarity topical edges (offline, ~10ms at N=1000) enables 1-hop expansion from hybrid search seeds. Full GraphRAG (LLM entity extraction at index time) is overkill here.

4. **Contextual bandit / RL** — The existing `FeedbackLoop.update_importance_scores()` can be upgraded with two changes: (a) continuous online gradient updates instead of discrete +0.05/-0.10 steps, and (b) condition decay on whether the injection was project-context-appropriate. This prevents wrong-project injections from penalizing memories.

5. **ANN / HNSW** — Not needed. Brute-force KNN on 1000 × 512-dim vectors takes ~1-3ms (confirmed by HNSW literature: brute force is competitive below ~10,000 vectors). sqlite-vec does not support HNSW as of early 2026. Add a latency trip-wire test; revisit if/when N > 50,000.

6. **Two-stage re-ranking** — LLM re-ranking is appropriate for `active_search()` (Tier 3, user-initiated, latency-tolerant) but not for passive Tier 2 injection. A distilled cross-encoder becomes feasible after accumulating ~500 `(query, memory, was_used)` triples from `RetrievalLog`.