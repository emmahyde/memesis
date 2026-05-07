# Graph Report - core+hooks+skills  (2026-04-27)

## Corpus Check
- 56 files · ~44,454 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 742 nodes · 2490 edges · 21 communities detected
- Extraction: 49% EXTRACTED · 51% INFERRED · 0% AMBIGUOUS · INFERRED: 1276 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Lifecycle & Crystallization|Lifecycle & Crystallization]]
- [[_COMMUNITY_Database & Storage Core|Database & Storage Core]]
- [[_COMMUNITY_Module Imports Hub|Module Imports Hub]]
- [[_COMMUNITY_Consolidator (LLM Curation)|Consolidator (LLM Curation)]]
- [[_COMMUNITY_Glyph Diagram DSL|Glyph Diagram DSL]]
- [[_COMMUNITY_Embeddings & Reconsolidation|Embeddings & Reconsolidation]]
- [[_COMMUNITY_Connect Skill & Graph Traversal|Connect Skill & Graph Traversal]]
- [[_COMMUNITY_Affect & Coherence Models|Affect & Coherence Models]]
- [[_COMMUNITY_Ghost Coherence Check|Ghost Coherence Check]]
- [[_COMMUNITY_Cursors & Transcript Ingest|Cursors & Transcript Ingest]]
- [[_COMMUNITY_Orienting & Replay Signals|Orienting & Replay Signals]]
- [[_COMMUNITY_Backfill & Ideation Pipeline|Backfill & Ideation Pipeline]]
- [[_COMMUNITY_Memory Query Filters|Memory Query Filters]]
- [[_COMMUNITY_Stage Filter Query|Stage Filter Query]]
- [[_COMMUNITY_FTS Search|FTS Search]]
- [[_COMMUNITY_FTS Term Sanitizer|FTS Term Sanitizer]]
- [[_COMMUNITY_NL Query Builder|NL Query Builder]]
- [[_COMMUNITY_Tag JSON Parser|Tag JSON Parser]]
- [[_COMMUNITY_Thread Query Builder|Thread Query Builder]]
- [[_COMMUNITY_Thread Member Resolver|Thread Member Resolver]]
- [[_COMMUNITY_Orienting Signal Check|Orienting Signal Check]]

## God Nodes (most connected - your core abstractions)
1. `Memory` - 195 edges
2. `ConsolidationLog` - 102 edges
3. `append()` - 90 edges
4. `MemoryEdge` - 86 edges
5. `LifecycleManager` - 74 edges
6. `ThreadMember` - 70 edges
7. `RetrievalLog` - 66 edges
8. `NarrativeThread` - 53 edges
9. `RelevanceEngine` - 48 edges
10. `Observation` - 41 edges

## Surprising Connections (you probably didn't know these)
- `Update SM-2 schedule fields after injection feedback.      Args:         memory:` --uses--> `Memory`  [INFERRED]
  /Users/emmahyde/projects/memesis/core/spaced.py → /Users/emmahyde/projects/memesis/core/models.py
- `Check if a memory is eligible for injection (not suppressed by SM-2).      Retur` --uses--> `Memory`  [INFERRED]
  /Users/emmahyde/projects/memesis/core/spaced.py → /Users/emmahyde/projects/memesis/core/models.py
- `Truncate a string to at most max_len characters.` --uses--> `Memory`  [INFERRED]
  /Users/emmahyde/projects/memesis/core/manifest.py → /Users/emmahyde/projects/memesis/core/models.py
- `Format a single memory as a MEMORY.md list item.      Pattern: `- [title](relati` --uses--> `Memory`  [INFERRED]
  /Users/emmahyde/projects/memesis/core/manifest.py → /Users/emmahyde/projects/memesis/core/models.py
- `Generates a MEMORY.md index from SQLite metadata.` --uses--> `Memory`  [INFERRED]
  /Users/emmahyde/projects/memesis/core/manifest.py → /Users/emmahyde/projects/memesis/core/models.py

## Hyperedges (group relationships)
- **Memory observability triad — stats, health, usage share Memory model and present complementary system views** —  [INFERRED 0.88]
- **Thread pipeline — ThreadDetector, ThreadNarrator, and NarrativeThread together implement automatic thread detection and synthesis** —  [EXTRACTED 1.00]
- **Backfill pipeline — scan, consolidate, seed run in sequence to bootstrap memory store from transcripts** —  [EXTRACTED 1.00]

## Communities

### Community 0 - "Lifecycle & Crystallization"
Cohesion: 0.04
Nodes (67): process_buffer(), Find all ephemeral session files with actual observations., Read the consolidation counter from meta/consolidation-count.json., Increment and persist the consolidation counter. Returns new count., Run full lifecycle on a single ephemeral buffer., Crystallizer, Crystallization engine — transforms consolidated memories into higher-level insi, Transforms consolidated memories into crystallized insights.      When memories (+59 more)

### Community 1 - "Database & Storage Core"
Cohesion: 0.07
Nodes (100): Database initialisation and lifecycle management for the Peewee ORM layer.  Prov, Return the VecStore singleton (may be None if init_db hasn't been called)., Return the current database file path., Return the base directory (parent of index.db)., WAL checkpoint and close the database., Create the FTS5 virtual table if it doesn't exist., Run schema migrations for backwards compatibility.      - Add 'content' column t, Resolve the database path and base directory.      Returns:         (base_dir, d (+92 more)

### Community 2 - "Module Imports Hub"
Cohesion: 0.05
Nodes (55): _create_subsumption_edges(), _get_embeddings(), _create_fts_table(), get_db_path(), init_db(), _resolve_db_path(), _run_migrations(), _compute_usage_score() (+47 more)

### Community 3 - "Consolidator (LLM Curation)"
Cohesion: 0.06
Nodes (44): Consolidator, _format_markdown(), Consolidation engine for LLM-based memory curation during PreCompact.  Reads eph, Persist raw observations before the consolidation LLM decision., Best-effort map from an LLM decision observation back to captured rows., Update observation audit rows after the decision has been applied., Return shared LLM instrumentation fields for consolidation log rows., Rough token estimate for a list of memory objects.          Uses the common heur (+36 more)

### Community 4 - "Glyph Diagram DSL"
Cohesion: 0.11
Nodes (23): append(), Append an observation to the ephemeral buffer with file locking.      Args:, Attribute, ClassMember, DiagramAST, Edge, Entity, InferredActor (+15 more)

### Community 5 - "Embeddings & Reconsolidation"
Cohesion: 0.08
Nodes (31): get_vec_store(), embed_for_memory(), embed_text(), _get_bedrock_client(), Embedding service — wraps AWS Bedrock Titan Text Embeddings v2.  Computes text e, Lazy-init the Bedrock runtime client., Embed text via Bedrock Titan Text Embeddings v2.      Returns raw float32 bytes, Embed a memory's key fields for storage in vec_memories.      Combines title + s (+23 more)

### Community 6 - "Connect Skill & Graph Traversal"
Cohesion: 0.07
Nodes (52): Connect Path A — Direct memory IDs, Connect Path B — Topic FTS search then select, connect skill, find_ephemeral_buffers(), _get_consolidation_count(), _increment_consolidation_count(), main(), core.database — init_db, get_vec_store (+44 more)

### Community 7 - "Affect & Coherence Models"
Cohesion: 0.09
Nodes (40): AffectState, coherence_probe(), CoherenceResult, format_guidance(), from_dict(), InteractionAnalyzer, _jaccard(), likely_degraded() (+32 more)

### Community 8 - "Ghost Coherence Check"
Cohesion: 0.11
Nodes (21): check_coherence(), _is_rate_limited(), Ghost coherence check — validates self-model claims against memory evidence.  Co, Check if coherence was already run today., Record that a coherence check was performed., Run ghost coherence check.      Returns:         {             "consistent": [{", _record_check(), call_llm() (+13 more)

### Community 9 - "Cursors & Transcript Ingest"
Cohesion: 0.11
Nodes (23): CursorRow, CursorStore, Global transcript cursor store at ~/.claude/memesis/cursors.db.  Tracks the last, _clean_text(), _extract_tool_summary(), append_to_ephemeral(), discover_transcripts(), extract_observations() (+15 more)

### Community 10 - "Orienting & Replay Signals"
Cohesion: 0.19
Nodes (16): has_signals(), OrientingDetector, OrientingResult, OrientingSignal, OrientingDetector — rule-based high-signal moment detection.  Identifies moments, Detect orienting signals in the given text.          Args:             text: The, A single detected orienting signal., Aggregated result from OrientingDetector.detect(). (+8 more)

### Community 11 - "Backfill & Ideation Pipeline"
Cohesion: 0.18
Nodes (14): scripts/consolidate.py — LLM-based consolidation with --focus flag, scripts/scan.py — transcript scanning, scripts/seed.py — dedup-safe seeding of consolidated memories, backfill skill, Ideation cron — */2 * * * * /memesis:ideate, Priority stack: works? > missing? > broken? > ugly?, ideate skill, WANT phase — autonomous self-directed development ideation (+6 more)

### Community 12 - "Memory Query Filters"
Cohesion: 1.0
Nodes (1): Return a query for non-archived memories.

### Community 13 - "Stage Filter Query"
Cohesion: 1.0
Nodes (1): Return a query filtered by stage.

### Community 14 - "FTS Search"
Cohesion: 1.0
Nodes (1): Full-text search across memories via FTS5.          Returns a list of Memory mod

### Community 15 - "FTS Term Sanitizer"
Cohesion: 1.0
Nodes (1): Sanitize a term for safe use in FTS5 queries.          Wraps the term in double-

### Community 16 - "NL Query Builder"
Cohesion: 1.0
Nodes (1): Convert a natural language query into an FTS5 OR query.          Strips stop wor

### Community 17 - "Tag JSON Parser"
Cohesion: 1.0
Nodes (1): Parse the JSON tags string into a Python list.

### Community 18 - "Thread Query Builder"
Cohesion: 1.0
Nodes (1): Return ordered Memory query via ThreadMember join.

### Community 19 - "Thread Member Resolver"
Cohesion: 1.0
Nodes (1): Return list of memory_id strings in order.

### Community 20 - "Orienting Signal Check"
Cohesion: 1.0
Nodes (1): True if any orienting signals were detected.

## Knowledge Gaps
- **76 isolated node(s):** `Manages vector embeddings in a sqlite-vec virtual table.      Uses apsw for all`, `Open an apsw connection with the sqlite-vec extension loaded.`, `Store (or replace) a vector embedding for a memory.`, `KNN search against stored embeddings.          Returns list of (memory_id, dista`, `Get the stored embedding for a memory, or None.` (+71 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Memory Query Filters`** (1 nodes): `Return a query for non-archived memories.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Stage Filter Query`** (1 nodes): `Return a query filtered by stage.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `FTS Search`** (1 nodes): `Full-text search across memories via FTS5.          Returns a list of Memory mod`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `FTS Term Sanitizer`** (1 nodes): `Sanitize a term for safe use in FTS5 queries.          Wraps the term in double-`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `NL Query Builder`** (1 nodes): `Convert a natural language query into an FTS5 OR query.          Strips stop wor`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Tag JSON Parser`** (1 nodes): `Parse the JSON tags string into a Python list.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Thread Query Builder`** (1 nodes): `Return ordered Memory query via ThreadMember join.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Thread Member Resolver`** (1 nodes): `Return list of memory_id strings in order.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Orienting Signal Check`** (1 nodes): `True if any orienting signals were detected.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `append()` connect `Glyph Diagram DSL` to `Lifecycle & Crystallization`, `Database & Storage Core`, `Module Imports Hub`, `Consolidator (LLM Curation)`, `Embeddings & Reconsolidation`, `Connect Skill & Graph Traversal`, `Affect & Coherence Models`, `Ghost Coherence Check`, `Cursors & Transcript Ingest`, `Orienting & Replay Signals`?**
  _High betweenness centrality (0.243) - this node is a cross-community bridge._
- **Why does `Memory` connect `Database & Storage Core` to `Lifecycle & Crystallization`, `Module Imports Hub`, `Consolidator (LLM Curation)`, `Embeddings & Reconsolidation`, `Ghost Coherence Check`?**
  _High betweenness centrality (0.206) - this node is a cross-community bridge._
- **Why does `core.database — init_db, get_vec_store` connect `Connect Skill & Graph Traversal` to `Consolidator (LLM Curation)`, `Embeddings & Reconsolidation`?**
  _High betweenness centrality (0.124) - this node is a cross-community bridge._
- **Are the 183 inferred relationships involving `Memory` (e.g. with `Consolidator` and `Consolidation engine for LLM-based memory curation during PreCompact.  Reads eph`) actually correct?**
  _`Memory` has 183 INFERRED edges - model-reasoned connections that need verification._
- **Are the 98 inferred relationships involving `ConsolidationLog` (e.g. with `Consolidator` and `Consolidation engine for LLM-based memory curation during PreCompact.  Reads eph`) actually correct?**
  _`ConsolidationLog` has 98 INFERRED edges - model-reasoned connections that need verification._
- **Are the 87 inferred relationships involving `append()` (e.g. with `.consolidate_session()` and `._record_observations()`) actually correct?**
  _`append()` has 87 INFERRED edges - model-reasoned connections that need verification._
- **Are the 82 inferred relationships involving `MemoryEdge` (e.g. with `Database initialisation and lifecycle management for the Peewee ORM layer.  Prov` and `Resolve the database path and base directory.      Returns:         (base_dir, d`) actually correct?**
  _`MemoryEdge` has 82 INFERRED edges - model-reasoned connections that need verification._