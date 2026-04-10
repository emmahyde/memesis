# Relationship Engine: Technical Specification

## Overview

Five features that establish valuable relationships between memories,
complementing NarrativeThreads without competing with them. Threads own
the "narrate the journey" niche. The relationship engine adds
**directionality** (causal edges), **tension** (contradictions),
**temporality** (echoes), **challenge** (adversarial surfacing), and
**tone** (affect signatures).

### Design Principles

- **Threads narrate; relationships encode.** Threads tell stories.
  Relationships encode structural properties (causality, opposition,
  recurrence) that Threads don't capture.
- **sqlite-vec is a first-class citizen.** Embedding similarity drives
  target selection for causal edges, tiebreaking in graph expansion, and
  centroid computation for neighbor ranking.
- **Incremental, not recomputable.** Causal, contradiction, echo, and
  affect edges are created during pipeline steps and preserved across
  `compute_edges()` rebuilds. Only `thread_neighbor` and
  `tag_cooccurrence` are recomputed.
- **Every feature is flag-gated.** Each can be disabled independently
  via `flags.json` without affecting others.

### Dependency Graph

```
Phase 1: Foundation                     [IMPLEMENTED]
  Causal Edges

Phase 2: Tension
  Contradiction Tensors  (uses causal edge infrastructure)
  Temporal Echoes        (independent, same edge infrastructure)

Phase 3: Intelligence
  Affect Signatures      (uses causal + contradiction edges for coloring)
  Adversarial Surfacing  (uses contradictions for candidates, affect for gating)
```

Build order: `1 -> 2 -> 3`. Each phase's features are independent within
the phase.

---

## Shared Schema Changes

All three phases share a single set of schema additions, applied in
Phase 1 to avoid multiple migration rounds.

### `memory_edges` table

| Column | Type | Default | Added in |
|--------|------|---------|----------|
| `metadata` | TEXT | NULL | Phase 1 |

Stores JSON. Structure varies by edge type:

```json
// caused_by, refined_from
{"evidence": "...", "session_id": "...", "created_at": "..."}

// subsumed_into
{"source_title": "...", "crystal_title": "...", "created_at": "..."}

// contradicts (Phase 2)
{"evidence": "...", "resolved": false, "resolution": "...",
 "detected_by": "reconsolidation|thread_narrator", "detected_at": "..."}

// echo (Phase 2)
{"gap_days": 42, "re_emergence_session": "...", "detected_at": "..."}

// Any edge with affect coloring (Phase 3)
{"evidence": "...", "affect": {"frustration": 0.7, "satisfaction": 0.2,
 "momentum": -0.4, "dominant_valence": "friction"}}
```

### `memories` table

| Column | Type | Default | Added in |
|--------|------|---------|----------|
| `echo_count` | INTEGER | 0 | Phase 1 |

### `narrative_threads` table

| Column | Type | Default | Added in |
|--------|------|---------|----------|
| `arc_affect` | TEXT | NULL | Phase 1 |

JSON structure:

```json
{"trajectory": "frustration_to_mastery", "start": "friction",
 "end": "delight", "friction_ratio": 0.6, "arc_type": "correction_chain"}
```

### Edge Type Registry

| edge_type | Direction | Created by | Recomputable | Phase |
|-----------|-----------|------------|--------------|-------|
| `thread_neighbor` | bidirectional | `compute_edges()` | yes | existing |
| `tag_cooccurrence` | bidirectional | `compute_edges()` | yes | existing |
| `caused_by` | A→B "A exists because B" | reconsolidation | no | 1 |
| `refined_from` | A→B "A refines B" | reconsolidation | no | 1 |
| `subsumed_into` | A→B "A crystallized into B" | crystallizer | no | 1 |
| `contradicts` | bidirectional | reconsolidation, thread narrator | no | 2 |
| `echo` | A→B "A re-emerged in context of B" | echo detector | no | 2 |

### Feature Flags

| Flag | Default | Phase |
|------|---------|-------|
| `causal_edges` | true | 1 |
| `contradiction_tensors` | true | 2 |
| `temporal_echoes` | true | 2 |
| `affect_signatures` | true | 3 |
| `adversarial_surfacing` | true | 3 |

---

## Phase 1: Causal Edges [IMPLEMENTED]

### Problem

Threads capture co-occurrence in time. They don't encode **why** one
memory exists because of another. `ThreadMember.position` is narrative
order, not causal order. The system knows "these memories tell a story
together" but not "memory B was created because memory A happened."

Reconsolidation already detects three causal relationships per session
(confirmed, contradicted, refined) but only acts on them transactionally.
The relationship itself is lost after the LLM call returns.

### Detection

#### Reconsolidation path (`core/reconsolidation.py`)

When the LLM returns a "refined" or "contradicted" decision:

1. Build a **target pool** from co-injected memories. Prefer confirmed
   memories (the session validated them) over the full injection set.
2. **Rank by sqlite-vec cosine similarity** to the affected memory.
   `_rank_by_similarity()` fetches stored embeddings via `VecStore`,
   unpacks float32 vectors, and computes dot products (embeddings are
   pre-normalized by Titan v2, so dot product = cosine similarity).
3. Create up to `_MAX_CAUSAL_EDGES` (3) edges. Edge type is
   `refined_from` for refinements, `caused_by` for contradictions.
4. Skip duplicates — checks for existing edge with same (source, target,
   type) before creation.
5. Falls back to weight=0.5 for all candidates if embeddings unavailable.

**Concern — vector similarity suppressing opposing memories:**
The cosine similarity ranking selects the most *semantically similar*
co-injected targets. A memory representing the *opposite* of what was
discussed would rank low. This is acceptable for `refined_from` edges
(refinements are semantically close by nature). For `caused_by` edges
on contradictions, the target pool is filtered to *confirmed* memories
first — these are memories the session validated while contradicting
the source. The contradiction relationship is captured by the edge
existing at all, not by the similarity score. Phase 2's Contradiction
Tensors address the deeper concern by creating explicit bidirectional
opposition edges.

#### Crystallization path (`core/crystallizer.py`)

When `_crystallize_group()` archives source memories:

1. For each source memory in the group, create a `subsumed_into` edge
   from source → crystallized memory.
2. Weight is always 1.0 (crystallization is deterministic).
3. Metadata includes source and crystal titles.

This makes crystallization lineage walkable via the graph (single
indexed query on `MemoryEdge.target_id`) rather than requiring a scan
of `Memory.subsumed_by` across all archived memories.

### Graph Expansion (`core/graph.py`)

#### `compute_edges()`

Only clears recomputable edge types (`thread_neighbor`,
`tag_cooccurrence`). Incremental edges survive rebuilds. The exists
check for tag co-occurrence edges is scoped to recomputable types so
a causal edge between A→B doesn't suppress the tag co-occurrence edge.

#### `expand_neighbors()`

Priority ordering by edge type:

| Priority | Edge Types |
|----------|-----------|
| 0 | `caused_by` |
| 1 | `refined_from` |
| 2 | `subsumed_into` |
| 3 | `contradicts`, `echo` |
| 4 | `thread_neighbor` |
| 5 | `tag_cooccurrence` |

Within the same priority tier, **sqlite-vec centroid similarity** breaks
ties. The seed set centroid is computed by averaging seed embeddings
(fetched via `VecStore.get_embedding()`), normalizing, then computing
dot products against each candidate neighbor's embedding. This ensures
neighbors that are semantically closest to the *overall seed context*
are preferred.

Accepts optional `vec_store` parameter. The retrieval engine passes the
`VecStore` singleton through; tests can inject mocks.

### Relevance Integration (`core/relevance.py`)

The `integration_factor` in `compute_relevance()` now checks three
signals instead of two:

```
connected = has_thread_membership OR has_tag_overlap OR has_causal_edges
```

A memory with causal edges (as source or target of `caused_by`,
`refined_from`, or `subsumed_into`) is never considered "isolated"
regardless of thread membership or tag overlap. This prevents archival
of causally important memories that are topically orphaned.

### Pipeline Position

No new pipeline step. Edges are created inside existing steps:
- Reconsolidation (pre_compact.py step 2)
- Crystallization (pre_compact.py step 4)
- `compute_edges()` called inside `build_threads()` (step 6) — change
  to preserve causal edges takes effect here.

### Files Modified

| File | Change |
|------|--------|
| `core/models.py` | `metadata` on MemoryEdge, `echo_count` on Memory, `arc_affect` on NarrativeThread, `RECOMPUTABLE_TYPES` class attr |
| `core/database.py` | Migrations for all three new columns |
| `core/flags.py` | `causal_edges` flag |
| `core/reconsolidation.py` | `_create_causal_edges()`, `_rank_by_similarity()` |
| `core/crystallizer.py` | `_create_subsumption_edges()` |
| `core/graph.py` | `compute_edges()` preservation, `expand_neighbors()` priority + centroid tiebreaker |
| `core/relevance.py` | `_has_causal_edges()`, updated integration_factor logic |
| `core/retrieval.py` | Pass `vec_store` to `expand_neighbors()` |
| `tests/test_causal_edges.py` | 24 tests covering all changes |

---

## Phase 2: Contradiction Tensors

### Problem

Reconsolidation detects contradictions per-session, but the response is
a tag (`contradiction_flagged`) and a log entry. The *relationship* —
"memory A and memory B are in tension" — isn't persisted. After the tag
is added, the system has no way to know *what* memory A contradicts or
*when each side applies*.

Some contradictions are errors (one memory is wrong). Others are
**contextual truths** — "use mocks for unit tests" and "never mock in
integration tests" are both correct in their respective domains.

### Detection — Three Sources

#### Source 1: Reconsolidation (zero marginal cost)

When `action == "contradicted"` and other memories in the same session
were `"confirmed"`, create bidirectional `contradicts` edges between the
contradicted memory and each confirmed memory. The session chose the
confirmed position over the contradicted one.

```python
confirmed_ids = result.get("confirmed", [])
for confirmed_id in confirmed_ids:
    if confirmed_id != mid:
        _create_contradiction_edge(mid, confirmed_id, evidence, session_id)
```

Bidirectional creation: both (A→B) and (B→A) edges. `resolved` starts
as `false`. Weight represents severity (0.7 default from reconsolidation).

#### Source 2: Thread narration (piggyback on existing LLM call)

When `ThreadNarrator.narrate_cluster()` returns `arc_type ==
"correction_chain"`, the early members (low position) represent the
corrected position and late members the current understanding. Create
`contradicts` edges between early[0] and late[-1] with `resolved: true`
and resolution referencing the thread narrative.

Weight is 0.3 (low — already resolved). These are historical
contradictions, not active tensions.

#### Source 3: Pairwise scan (deferred)

At consolidation time, compare crystallized memories pairwise via LLM.
Expensive. Only run when new crystallizations happen. Catches
contradictions between memories that were never co-injected.

**Deferred to a later phase.** Sources 1 and 2 capture the most
valuable contradictions at zero marginal LLM cost.

### Retrieval — Active Tensions Tier (2.6)

After Tier 2.5 (thread narratives), before closing section:

```
## Active Tensions (conflicting memories — context determines which applies)

### Mocking strategy depends on test type
Position A: use mocks for unit test speed
Position B: never mock the database
Context: A applies to pure unit tests; B applies to integration tests
```

Budget: `TENSION_BUDGET_CHARS = 2,000`. Only surfaces **unresolved**
contradictions (where `metadata.resolved == false`).

### Relevance Integration

Contradiction edges count as integration — both sides are protected
from archival. A memory in an active tension is structurally important.

### `compute_edges()` Behavior

`contradicts` edges are incremental, not recomputable. Preserved across
rebuilds.

### Files to Modify

| File | Change |
|------|--------|
| `core/reconsolidation.py` | `_create_contradiction_edge()` — bidirectional, with resolved/evidence metadata |
| `core/threads.py` | In `build_threads()`, after creating correction_chain threads, create resolved contradiction edges |
| `core/retrieval.py` | `_get_active_tensions()` method, Tier 2.6 section in `inject_for_session()` |
| `core/relevance.py` | `_has_contradiction_edges()`, added to integration_factor check |
| `core/flags.py` | `contradiction_tensors` flag |

---

## Phase 2: Temporal Echoes

### Problem

The system tracks injection and usage timestamps, and the relevance
engine decays memories over time. But there's no signal for
**re-emergence** — when a dormant memory becomes relevant again after a
long gap. This pattern is common: learn something in week 1, don't think
about it for a month, hit the same problem in week 5.

If the memory decayed below `ARCHIVE_THRESHOLD` (0.15) during the gap,
it gets archived. Even if rehydrated, there's no record that this is a
*recurring* pattern.

### Detection (`core/echoes.py` — new file)

```python
def detect_echoes(session_id: str, injected_ids: list[str]) -> list[dict]:
```

For each injected memory, query `RetrievalLog` for the two most recent
injections. If the gap between them exceeds `ECHO_GAP_DAYS` (30), it's
an echo event:

1. Bump `Memory.echo_count`.
2. Create `echo` edges to co-injected memories (what context triggered
   re-emergence). Weight = `gap_days / 30.0`, capped at 5.0.
3. Return echo event metadata for pipeline summary.

### Pipeline Position

In `pre_compact.py`, after usage tracking (step 1) and before
consolidation (step 3). Runs on the `injected_ids` list already
computed.

### Retrieval — Recurring Patterns Tier (2.7)

After Active Tensions (Tier 2.6):

```
## Recurring Patterns (re-emerging after dormancy)

### Error handling in async contexts (echoed 3 times, last gap: 42 days)
[memory summary]
```

Budget: `ECHO_BUDGET_CHARS = 1,500`. Surfaces memories where
`echo_count > 0`, sorted by echo_count descending. Max 3 annotations.

### Instinctive Promotion Fast-Track

In `lifecycle.py:_can_promote_to_instinctive()`:

```
if echo_count >= 3 and importance > 0.7:
    return True, "Recurring pattern: echoed N times"
```

Memories that keep re-emerging across long gaps are structurally
important — they capture recurring patterns. They deserve instinctive
status even without meeting the standard threshold (importance > 0.85,
10+ sessions).

### Relevance Integration

Echo count is an anti-decay signal. Memories with `echo_count >= 2` get
a recency floor of 0.3 — they resist archival even during dormant
periods because they've proven they come back.

### Files to Create/Modify

| File | Change |
|------|--------|
| `core/echoes.py` | New: `detect_echoes()` |
| `hooks/pre_compact.py` | Call `detect_echoes()` after usage tracking |
| `core/retrieval.py` | `_get_echo_context()`, Tier 2.7 section |
| `core/lifecycle.py` | Echo fast-track in `_can_promote_to_instinctive()` |
| `core/relevance.py` | Echo resilience: recency floor for high-echo memories |
| `core/flags.py` | `temporal_echoes` flag |

---

## Phase 3: Adversarial Surfacing

### Problem

Retrieval optimizes for agreement — memories that match context get
surfaced. Thompson sampling adds stochastic exploration, but still
optimizes for "memories you'll probably find useful." There's no
mechanism to deliberately surface memories that **challenge** the
current trajectory.

Threads synthesize consensus. Over time, they can calcify — the
conclusion becomes dogma even if it was slightly wrong or
context-dependent.

### Design

This is a **retrieval-time computation**, not a stored relationship.
It uses existing edges (contradiction edges from Phase 2, correction
chain thread members) to find challengers.

#### Algorithm

In `inject_for_session()`, after Tier 2 selection:

1. Collect dominant direction of injected memories.
2. Search for opposing memories:
   - **Strategy 1**: Unresolved `contradicts` edges to injected memories
   - **Strategy 2**: Early members of `correction_chain` threads where
     the injected memory is a later member
3. Select at most 1 adversarial memory.
4. Frame explicitly: "**Counterpoint:** [title]"

#### Gating

Three gates:

1. **Feature flag** (`adversarial_surfacing`)
2. **Thompson sampling**: Dedicated Beta distribution
   (`meta/adversarial-sampling.json`). Updated per session based on
   whether the adversarial memory was used.
3. **Affect guard**: No challenges when `AffectState.frustration > 0.4`.
   A frustrated user needs support, not challenge.

#### Budget

`ADVERSARIAL_BUDGET_CHARS = 500`. One memory maximum.

#### Feedback Loop

Log adversarial injections with `retrieval_type='adversarial'` (distinct
from `'injected'`). In `feedback.py:track_usage()`, detect adversarial
injections and update Thompson state:

- Used → increment successes
- Ignored → increment failures

When adversarial memories are consistently engaged with, flag the
associated thread for potential re-narration by updating its
`updated_at`.

### Dependencies

Works best with contradiction edges (Phase 2) but degrades gracefully
to correction_chain thread members without them.

### Files to Modify

| File | Change |
|------|--------|
| `core/retrieval.py` | `_get_adversarial_memory()`, `_adversarial_thompson_draw()`, Tier 2.8 section |
| `core/feedback.py` | Adversarial usage tracking, Thompson state update |
| `core/flags.py` | `adversarial_surfacing` flag |

---

## Phase 3: Affect Signatures

### Problem

`core/affect.py` tracks session-level emotional state (frustration,
satisfaction, momentum, repair patterns). `core/somatic.py` classifies
observations into valence categories. But this emotional signal is
ephemeral — it dies when the session ends.

Relationships between memories carry emotional valence that pure
semantic similarity misses. A correction chain *feels* different from a
knowledge-building arc. That difference is invisible to retrieval.

### Where Affect Data Already Lives

| Source | Lifetime | Location |
|--------|----------|----------|
| `SomaticResult.valence` | Permanent | `valence:` tag on Memory |
| `AffectState` | Session | `ephemeral/.affect-{session_id}.json` |
| `InteractionAnalyzer.corrections` | Session | Same JSON file |

The persisted `.affect-{session_id}.json` file is the bridge — it
survives across hook invocations within a session, and PreCompact can
read it.

### Affect State Availability at PreCompact

Add `current_state()` method to `InteractionAnalyzer`:

```python
def current_state(self) -> AffectState:
    """Return current affect state without processing a new message."""
```

In `pre_compact.py`, load it:

```python
analyzer = load_analyzer(base_dir, session_id)
session_affect = analyzer.current_state()
```

### Attaching Affect to Edges

**In reconsolidation**: When creating causal/contradiction edges, include
`session_affect` in metadata:

```json
{"evidence": "...", "affect": {"frustration": 0.7, "momentum": -0.4,
 "dominant_valence": "friction"}}
```

**In thread building**: Compute `arc_affect` from source memory somatic
valences:

```python
def _compute_arc_affect(valences: list[str], arc_type: str) -> dict:
```

Trajectory detection:
- `friction → delight` = `"frustration_to_mastery"`
- `friction → neutral` = `"frustration_to_resolution"`
- `neutral → delight` = `"curiosity_to_mastery"`
- `surprise → neutral/delight` = `"discovery"`
- all friction = `"sustained_struggle"`

### Affect-Aware Retrieval

In `_get_thread_narratives()`, when session affect is available and
frustration > 0.3:

- Prioritize `frustration_to_mastery` threads (resolved frustration —
  "here's how I got past this")
- Deprioritize `sustained_struggle` threads (won't help)

### Meta-Observation: Recurring Emotional Patterns (deferred)

Detect that "the last 3 times you worked on [topic], frustration
spiked." Correlate affect data on edges with topic tags across sessions.
Topics where average frustration exceeds 0.5 across 3+ edges are
flagged. Could feed observation creation.

**Deferred — this is a future enhancement, not part of Phase 3 core.**

### Files to Modify

| File | Change |
|------|--------|
| `core/affect.py` | `current_state()` method on InteractionAnalyzer |
| `core/reconsolidation.py` | Accept `session_affect` param, include in edge metadata |
| `core/threads.py` | `_compute_arc_affect()`, set `arc_affect` on threads after creation |
| `core/retrieval.py` | Affect-aware thread ordering in `_get_thread_narratives()`, load session affect in `inject_for_session()` |
| `hooks/pre_compact.py` | Load session affect, pass to reconsolidation |
| `core/flags.py` | `affect_signatures` flag |

---

## Injection Tier Summary

| Tier | Name | Source | Budget |
|------|------|--------|--------|
| 1 | Instinctive | Always injected | Unbounded |
| 2 | Crystallized | Context-matched, token-budgeted | 8% context window |
| 2.5 | Thread Narratives | Threads whose members are in Tier 2 | 8,000 chars |
| 2.6 | Active Tensions | Unresolved contradiction edges | 2,000 chars |
| 2.7 | Recurring Patterns | Memories with echo_count > 0 | 1,500 chars |
| 2.8 | Counterpoint | Single adversarial memory | 500 chars |
| 3 | Active Search | Agent-initiated hybrid search | On demand |

---

## How These Five Features Interrelate

```
                    Causal Edges (P1)
                   /               \
Contradiction (P2) <---> Adversarial (P3)
      |                       |
  Temporal Echoes (P2)   Affect Signatures (P3)
                    \       /
                     ------
```

- **Causal Edges** are the foundation — they make Contradictions
  detectable and give Adversarial Surfacing its candidate pool
- **Contradictions** feed Adversarial directly (unresolved tensions are
  the best adversarial candidates)
- **Affect Signatures** color Echoes (how it felt last time informs how
  to surface it this time) and gate Adversarial (don't challenge when
  frustrated)
- **Temporal Echoes** provide the promotion fast-track and the strongest
  signal that a memory is structurally important

## Vector Similarity Limitations

Cosine similarity is used throughout for target ranking and tiebreaking.
It has a known blind spot: **semantically opposed concepts can have
moderate-to-high similarity** because they share vocabulary and context.
"Always use mocks" and "never use mocks" are more similar than either
is to "use PostgreSQL."

This means:
- Contradiction detection should NOT rely on low similarity to find
  opposing memories — that's the LLM's job (reconsolidation, pairwise
  scan).
- Adversarial surfacing should NOT use embedding distance to find
  challengers — it should traverse contradiction edges instead.
- Causal edge target selection by similarity is appropriate because
  causal relationships ARE semantically proximate — a refinement is
  close to what it refines.

Where similarity is used defensively (e.g., expansion tiebreaking), it's
a soft signal, not a hard filter.
