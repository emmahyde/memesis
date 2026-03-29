---
name: recall
description: Use when the user asks "what do you know about X", "recall X", "search memories for X", "look up X", or wants to retrieve information from memory. This skill searches memories and returns results. Does NOT overlap with memory stats or health diagnostics — use /memory for those.
---

# Recall — Search Memories and Synthesize Results

Search across stored memories using both full-text and semantic search, then either synthesize a natural conversational response or render a ranked detail list.

## Usage

```
/memesis:recall [query]            # Conversational synthesis (default)
/memesis:recall [query] --detail   # Ranked list with scores and metadata
```

## Procedure

1. Embed the query using `embed_text()` for semantic search
2. Run `Memory.search_fts(query, limit=15)` for full-text matches
3. Run `get_vec_store().search_vector(query_embedding, k=15)` for semantic matches (skip gracefully if VecStore unavailable)
4. Hydrate semantic results: fetch `Memory` objects for each `memory_id` returned by `search_vector()`
5. Merge and deduplicate by memory ID — a memory appearing in both lists gets `retrieval_method = "both"` and a boosted composite score
6. Sort merged results by composite score descending
7. Render output based on mode:
   - **Default (conversational):** Synthesize the top results into a natural prose response. Weave in key facts and context. Cite memory IDs inline as `[mem:id]`
   - **--detail:** Render a ranked list (see format below)

### Score Normalization

FTS rank from SQLite FTS5 is negative (lower = better). Normalize to [0, 1]:

```
fts_score = 1.0 / (1.0 + abs(rank))
```

Semantic distance from sqlite-vec is L2 (lower = better). Normalize to [0, 1]:

```
semantic_score = 1.0 / (1.0 + distance)
```

Composite score when a memory appears in both:

```
composite_score = 0.5 * fts_score + 0.5 * semantic_score
```

When only in one source, use that source's score alone.

### Stage Badges

| Stage | Badge |
|-------|-------|
| instinctive | `[instinct]` |
| crystallized | `[crystal]` |
| consolidated | `[consol]` |
| ephemeral | `[ephemeral]` |

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db, get_vec_store
from core.models import Memory
from core.embeddings import embed_text
init_db(project_context=os.getcwd())

query = "<user query>"
limit = 15

# --- FTS search ---
fts_results = Memory.search_fts(query, limit=limit)

# Build FTS score map: memory_id -> normalized score
fts_map = {}
for mem in fts_results:
    rank = getattr(mem, '_rank', None) or -1.0
    fts_map[mem.id] = 1.0 / (1.0 + abs(rank))

# --- Semantic search (graceful fallback) ---
vec_map = {}  # memory_id -> normalized score
vec_available = False

vec_store = get_vec_store()
if vec_store and vec_store.available:
    query_embedding = embed_text(query)
    if query_embedding is not None:
        vec_available = True
        raw_vec_results = vec_store.search_vector(query_embedding, k=limit)
        for memory_id, distance in raw_vec_results:
            vec_map[memory_id] = 1.0 / (1.0 + distance)

# --- Merge and deduplicate ---
all_ids = set(fts_map.keys()) | set(vec_map.keys())

merged = []
for memory_id in all_ids:
    in_fts = memory_id in fts_map
    in_vec = memory_id in vec_map

    if in_fts and in_vec:
        method = "both"
        score = 0.5 * fts_map[memory_id] + 0.5 * vec_map[memory_id]
    elif in_fts:
        method = "fts"
        score = fts_map[memory_id]
    else:
        method = "semantic"
        score = vec_map[memory_id]

    merged.append((memory_id, score, method))

# Sort by composite score descending
merged.sort(key=lambda x: x[1], reverse=True)

# Hydrate Memory objects (FTS results are already objects; vec-only need lookup)
fts_obj_map = {mem.id: mem for mem in fts_results}

results = []
for memory_id, score, method in merged:
    if memory_id in fts_obj_map:
        mem = fts_obj_map[memory_id]
    else:
        try:
            mem = Memory.get_by_id(memory_id)
        except Memory.DoesNotExist:
            continue
    results.append((mem, score, method))

# --- Output ---
if not results:
    print("No memories found matching that query.")
else:
    # Default mode: conversational synthesis
    # Synthesize key facts from results into natural prose.
    # Cite memories inline as [mem:<id>].
    # Example citation: "The payment pipeline uses optimistic locking [mem:abc123]."

    # --detail mode: ranked list
    stage_badges = {
        "instinctive": "[instinct]",
        "crystallized": "[crystal]",
        "consolidated": "[consol]",
        "ephemeral":    "[ephemeral]",
    }
    for rank, (mem, score, method) in enumerate(results, 1):
        badge = stage_badges.get(mem.stage, f"[{mem.stage}]")
        title = mem.title or "(untitled)"
        summary = mem.summary or ""
        print(f"{rank}. {badge} {title}  (score: {score:.3f}, via: {method})")
        if summary:
            print(f"   {summary}")
        print(f"   id: {mem.id}")
        print()
```

## Conversational Mode (default)

After running the merge/dedup logic above, synthesize results into a natural response:

- Write 1–3 paragraphs covering the key facts, patterns, or context from matching memories
- Cite memory IDs inline: e.g. "...the consolidation pipeline runs hourly via cron [mem:4f2a...]"
- If zero results: respond naturally — "I don't have anything stored about that yet"
- If only FTS (vec unavailable): add a brief note — "(semantic search unavailable — showing text matches only)"

## Detail Mode (--detail flag)

Render a ranked list. Each entry shows:

```
1. [crystal] Cron Python Path Fix  (score: 0.821, via: both)
   The hourly consolidation cron used /usr/bin/python3 which lacks the anthropic package.
   id: 4f2a8c1e-...

2. [consol] Memesis Architecture Context  (score: 0.634, via: fts)
   Core architecture decisions and system overview for the memesis memory lifecycle project.
   id: 9b3d...
```

After the list, if semantic search was unavailable, append:

```
Note: semantic search unavailable (sqlite-vec not loaded) — results are text-only.
```

## Examples

```
/memesis:recall what do you know about the consolidation pipeline
```

Conversational response synthesizing memories about consolidation, the cron setup, prompts, and any corrections, with inline citations.

```
/memesis:recall cron python path --detail
```

```
1. [consol] Cron Python Path Fix  (score: 0.941, via: both)
   Hourly consolidation cron was broken; /usr/bin/python3 lacks anthropic, fixed to /usr/local/bin/python3.
   id: 8e1f2a...

2. [crystal] Memesis Architecture Context  (score: 0.512, via: fts)
   Core architecture decisions and system overview for the memesis memory lifecycle project.
   id: 4b9c...
```
