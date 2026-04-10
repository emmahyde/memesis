# Schema Evolution Proposal

**Status**: Draft  
**Date**: 2026-04-09  
**Scope**: memesis SQLite schema — extensibility, structural hygiene, and readiness for future features

---

## Current State

Six ORM tables, one FTS5 virtual table, one sqlite-vec virtual table. Single-file `index.db` with WAL mode. All persistence through Peewee ORM with raw SQL for FTS5 operations and sqlite-vec (via apsw).

### Structural Observations

| Pattern | Where | Count | Risk |
|---------|-------|-------|------|
| Hardcoded stage strings | 12 files, 60+ sites | High | Typo = silent bug, no compile-time safety |
| JSON-in-TEXT columns | `tags`, `metadata`, `arc_affect` | 6 columns | No query support, no schema enforcement |
| Filesystem state | `affect.py`, `habituation.py` | 2 subsystems | Lost on directory cleanup, invisible to queries |
| Soft FK references | `MemoryEdge.source_id/target_id`, `ThreadMember` | All relations | No cascade deletes, orphan risk |
| No user/tenant column | `memories`, all tables | Global | Single-user assumption baked into every query |
| No indexes beyond PKs | `retrieval_log`, `consolidation_log` | Log tables | Full scans on stage+archived_at queries |

---

## Short-Term Proposed Changes

These are low-risk, high-value structural improvements that don't change behavior. Each can be shipped independently.

### 1. Extract Stage Enum

60+ hardcoded string literals across 12 files. A single typo (`"crystalized"` vs `"crystallized"`) would silently break lifecycle progression.

```python
# core/stages.py
from enum import StrEnum

class Stage(StrEnum):
    EPHEMERAL = "ephemeral"
    CONSOLIDATED = "consolidated"
    CRYSTALLIZED = "crystallized"
    INSTINCTIVE = "instinctive"

STAGE_ORDER = [Stage.EPHEMERAL, Stage.CONSOLIDATED, Stage.CRYSTALLIZED, Stage.INSTINCTIVE]
```

**Impact**: Pure refactor. No schema change. Replace string literals with `Stage.CONSOLIDATED` everywhere.

### 2. Migrate Filesystem State to Database

Two subsystems store state in JSON files that are invisible to queries and lost on cleanup:

| Subsystem | Current | Proposed |
|-----------|---------|----------|
| Affect | `ephemeral/.affect-{session_id}.json` | New `session_state` table |
| Habituation | `habituation_counts.json` | New `observation_counts` table |

```sql
CREATE TABLE session_state (
    session_id TEXT PRIMARY KEY,
    subsystem  TEXT NOT NULL,  -- 'affect', 'habituation', etc.
    data       TEXT NOT NULL,  -- JSON blob
    updated_at TEXT NOT NULL
);
```

**Impact**: Affect and habituation code changes their read/write paths. No behavioral change.

### 3. Add Missing Indexes

The two log tables are queried by `memory_id` and `session_id` frequently but have no indexes beyond the auto-increment PK.

```sql
CREATE INDEX idx_retrieval_log_memory ON retrieval_log(memory_id);
CREATE INDEX idx_retrieval_log_session ON retrieval_log(session_id);
CREATE INDEX idx_consolidation_log_memory ON consolidation_log(memory_id);
CREATE INDEX idx_memories_stage_active ON memories(stage, archived_at);
CREATE INDEX idx_memory_edges_source ON memory_edges(source_id);
CREATE INDEX idx_memory_edges_target ON memory_edges(target_id);
CREATE INDEX idx_memory_edges_type ON memory_edges(edge_type);
```

**Impact**: Migration-only. Read performance improvement for lifecycle checks (which query consolidation_log for distinct days) and graph expansion (which queries edges by source/target).

### 4. Typed Edge Metadata

`MemoryEdge.metadata` is a JSON text column with six different shapes depending on the edge type. No validation, no queryability.

Short-term fix: add a Pydantic model for validation at write time, keep the JSON column.

```python
# core/edge_meta.py
from pydantic import BaseModel
from typing import Optional

class CausalMeta(BaseModel):
    evidence: str
    session_id: str
    created_at: str
    affect: Optional[dict] = None

class ContradictionMeta(BaseModel):
    evidence: str
    resolved: bool = False
    resolution: Optional[str] = None
    detected_by: str
    detected_at: str
    thread_id: Optional[str] = None
```

**Impact**: Validation at write time. No schema change. Catches malformed metadata before it enters the DB.

---

## Long-Term Considerations

### The "Everything Points to Memories" Problem

The current schema is a hub-and-spoke: `memories` is the center, everything references it. This works for a single-user, single-project system. It breaks when:

1. **Memory count exceeds ~10K**: FTS5 and sqlite-vec scale well, but the graph layer's `compute_edges()` does O(n²) pairwise comparisons within threads. Needs partition-by-project or incremental computation.

2. **Multiple projects share memories**: `project_context` exists as a TEXT column but isn't indexed or used as a partition key. Cross-project queries would need to either join on project_context or accept scanning all memories.

3. **Lifecycle rules grow per-stage logic**: Each stage transition has bespoke Python logic (reinforcement_count >= 3, distinct days >= 2, importance > 0.85). Adding new stages or branching paths requires editing lifecycle.py's conditional chain.

### Schema Flexibility Assessment

| Dimension | Current Readiness | Gap |
|-----------|-------------------|-----|
| New memory attributes | Good — ALTER TABLE ADD COLUMN + migration pattern established | Migration file is procedural (no versioning) |
| New edge types | Good — string-typed, RECOMPUTABLE_TYPES set controls rebuild | No metadata schema per type |
| New tables | Good — Peewee create_tables(safe=True) handles IF NOT EXISTS | No migration framework (Peewee-migrate, Alembic) |
| New stages | Poor — hardcoded strings, bespoke transition logic | Needs Stage enum + data-driven transition rules |
| Multi-user | Poor — no user_id anywhere, filesystem paths assume single user | Fundamental schema addition |
| Plugin data | Poor — no generic extension table or metadata namespace | Needs a `memory_meta` or `extensions` table |

---

## Feature Impact Analysis

Ten potential features, their schema impact, and what breaks.

### 1. Multi-User Memory Isolation

**What**: Multiple Claude Code users sharing a machine, each with isolated memories.

**Schema impact**: Add `user_id TEXT` to `memories`, `narrative_threads`, `retrieval_log`, `consolidation_log`. Add to all queries as a filter. Index on `(user_id, stage, archived_at)`.

**What breaks**: Every query that assumes a single global memory pool. Retrieval, lifecycle, consolidation all need user scoping. Affect session files need user prefixes.

**Difficulty**: Medium. Mechanical but pervasive.

### 2. Cross-Project Memory Sharing

**What**: A memory learned in project A is relevant in project B. Currently `project_context` is a single text field — a memory belongs to one project.

**Schema impact**: New `memory_projects` join table (many-to-many). Add `visibility` enum (`private`, `shared`, `global`) to memories. Index on `(project_context, visibility)`.

```sql
CREATE TABLE memory_projects (
    memory_id TEXT REFERENCES memories(id),
    project_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, project_id)
);
```

**What breaks**: Retrieval's project_context filter becomes a join. Consolidation needs to handle memories visible across projects without double-processing.

**Difficulty**: Medium-high. Retrieval and injection paths need rethinking.

### 3. Semantic Versioning of Memories

**What**: Track how a memory's content evolves over time. Currently `updated_at` overwrites — there's no history.

**Schema impact**: New `memory_versions` table.

```sql
CREATE TABLE memory_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT,
    summary TEXT,
    content TEXT,
    created_at TEXT NOT NULL,
    diff_from_prev TEXT,  -- JSON patch or summary of changes
    trigger TEXT           -- 'consolidation', 'reconsolidation', 'user_edit'
);
```

**What breaks**: Nothing — purely additive. Crystallizer and consolidator would write version rows alongside their current update logic. FTS would index the current version (already does).

**Difficulty**: Low. Additive table, opt-in writes.

### 4. Emotion/Affect History

**What**: Track emotional context over time, not just per-session. Currently affect state is ephemeral (filesystem JSON, cleared between sessions).

**Schema impact**: `session_state` table (proposed in short-term) handles the immediate need. Long-term: add `affect_snapshot` column to `consolidation_log` or create a dedicated `affect_log` table.

```sql
CREATE TABLE affect_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    frustration REAL,
    momentum REAL,
    dominant_valence TEXT,
    trigger_memory_id TEXT
);
```

**What breaks**: Nothing — additive. Reconsolidation already captures affect in edge metadata; this surfaces it for trend analysis.

**Difficulty**: Low.

### 5. Memory Dependencies (DAG)

**What**: Formal "memory A depends on memory B" — if B is pruned, A should be flagged for review.

**Schema impact**: Already supported by `MemoryEdge` with a new edge type `depends_on`. The graph layer's recomputable/incremental split handles this naturally. Need a new query: "find all memories that depend on a given memory" (already possible with `source_id/target_id` indexes).

**What breaks**: Pruning logic needs to check dependency edges before archiving. Relevance scoring could factor in dependency depth.

**Difficulty**: Low. Leverage existing edge infrastructure.

### 6. Plugin Data Storage

**What**: Third-party plugins store custom metadata on memories (e.g., a code-review plugin tags memories with PR URLs, a Jira plugin links memories to tickets).

**Schema impact**: Generic key-value extension table.

```sql
CREATE TABLE memory_meta (
    memory_id TEXT NOT NULL,
    namespace TEXT NOT NULL,    -- plugin identifier, e.g. 'jira', 'github'
    key TEXT NOT NULL,
    value TEXT,                 -- JSON or plain text
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, namespace, key)
);
```

**What breaks**: Nothing — purely additive. Plugins read/write their own namespace. Core memesis never queries this table.

**Difficulty**: Low.

### 7. Memory Clusters (Beyond Threads)

**What**: Group memories by topic, project phase, or user-defined categories. Threads are narrative arcs; clusters are taxonomic groupings.

**Schema impact**: New `clusters` and `cluster_members` tables (parallel to threads/thread_members but with different semantics).

```sql
CREATE TABLE clusters (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cluster_type TEXT,  -- 'topic', 'phase', 'manual'
    created_at TEXT,
    metadata TEXT        -- JSON
);

CREATE TABLE cluster_members (
    cluster_id TEXT,
    memory_id TEXT,
    PRIMARY KEY (cluster_id, memory_id)
);
```

**What breaks**: Nothing — additive. Retrieval could optionally boost memories in the same cluster.

**Difficulty**: Low-medium. Table is simple; the clustering algorithm is the hard part.

### 8. Retrieval Quality Feedback Loop

**What**: Track not just was_used (binary) but *how useful* the retrieval was — user explicitly rates, or implicit signal from conversation.

**Schema impact**: Extend `retrieval_log` with `quality_score REAL`, `feedback_type TEXT` ('implicit'/'explicit'), `feedback_detail TEXT`.

**What breaks**: Nothing — additive columns. Thompson sampling in retrieval already uses `was_used`; extending to a continuous quality score improves arm selection.

**Difficulty**: Low.

### 9. Time-Decay Curves per Memory Type

**What**: Different memory types decay at different rates. Technical knowledge decays slowly; emotional context decays fast. Currently all memories use the same SM-2 parameters.

**Schema impact**: Add `decay_profile TEXT` to memories (or derive from stage + tags). Alternatively, a `decay_profiles` config table that maps tag patterns to SM-2 parameter overrides.

```sql
CREATE TABLE decay_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    tag_pattern TEXT,           -- regex or glob matched against memory tags
    ease_factor_base REAL,
    interval_multiplier REAL,
    min_importance_floor REAL
);
```

**What breaks**: SM-2 logic in `spaced.py` needs to look up the profile instead of using memory-level fields directly.

**Difficulty**: Medium. The table is simple; the matching logic and parameter tuning are the work.

### 10. Audit Trail / Compliance

**What**: Immutable log of every mutation to a memory — who changed what, when, why. For enterprise deployments where memory content may be sensitive.

**Schema impact**: New `audit_log` table, populated by triggers or ORM hooks.

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    table_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    action TEXT NOT NULL,      -- 'insert', 'update', 'delete'
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    actor TEXT                  -- 'system', 'user', 'plugin:name'
);
```

**What breaks**: Nothing — additive. Performance impact from trigger-based logging on every save, but SQLite WAL handles write contention well.

**Difficulty**: Low (table) to medium (wiring triggers across all models).

---

## Data Crowding Scenarios

### Scenario A: "The 50K Memory Problem"

A power user accumulates 50,000 memories over 18 months. Current bottlenecks:

- **FTS5**: Handles 50K well (BM25 is O(log n) per query). No change needed.
- **sqlite-vec KNN**: 50K × 512-dim vectors ≈ 100MB. KNN search is O(n) brute-force in sqlite-vec. At 50K this takes ~200ms. Mitigation: partition by project_context, or switch to IVF index when available.
- **Graph expansion**: `compute_edges()` rebuilds thread_neighbor and tag_cooccurrence edges. With 50K memories in 2000 threads, the thread_neighbor pass is O(Σ thread_size²). Mitigation: only rebuild edges for memories modified since last run (already has `RECOMPUTABLE_TYPES` for this — add a `last_computed_at` watermark).
- **Lifecycle checks**: Querying consolidation_log for "2+ distinct days" per memory is O(n × log table size). With 50K memories and 200K log rows, this needs the proposed index on `consolidation_log(memory_id)`.

**Schema changes needed**: Indexes (short-term #3), watermark column on memories or a separate computation state table.

### Scenario B: "The Multi-Project Knowledge Base"

A user works on 8 projects. Some memories are project-specific, some are general engineering knowledge. Current `project_context` is a single TEXT field set at creation time.

- **Retrieval**: Currently filters on exact project_context match. General knowledge (no project_context) is always included. Cross-project retrieval would need `memory_projects` join table or a `visibility` enum.
- **Consolidation**: Currently processes all memories regardless of project. With 8 projects, the consolidator would process 8× more memories per run. Mitigation: partition consolidation by project_context, process one project per cron cycle.
- **Threads**: Currently global. A thread could span memories from multiple projects (which is actually desirable for cross-project insights). No change needed, but the UI should indicate project provenance.

**Schema changes needed**: `memory_projects` table (feature #2), project_context index, partition-aware consolidation.

---

## Annotated Schema Diff

Changes organized by priority tier.

### Tier 1: Structural Hygiene (no behavioral change)

```diff
 -- memories table
 CREATE TABLE memories (
     id TEXT PRIMARY KEY,
-    stage TEXT,
+    stage TEXT NOT NULL CHECK(stage IN ('ephemeral','consolidated','crystallized','instinctive')),
     title TEXT,
     summary TEXT,
     content TEXT,
     tags TEXT,
-    importance FLOAT,
+    importance REAL CHECK(importance >= 0.0 AND importance <= 1.0),
     ...
 );

+-- Indexes for common query patterns
+CREATE INDEX idx_memories_stage_active ON memories(stage, archived_at);
+CREATE INDEX idx_memories_project ON memories(project_context) WHERE project_context IS NOT NULL;
+CREATE INDEX idx_memory_edges_source ON memory_edges(source_id);
+CREATE INDEX idx_memory_edges_target ON memory_edges(target_id);
+CREATE INDEX idx_memory_edges_type ON memory_edges(edge_type);
+CREATE INDEX idx_retrieval_log_memory ON retrieval_log(memory_id);
+CREATE INDEX idx_consolidation_log_memory ON consolidation_log(memory_id);
```

**Reasoning**: CHECK constraints catch invalid data at write time instead of silently corrupting. Indexes eliminate full scans on the two most common query patterns (stage filtering and log aggregation).

### Tier 2: Filesystem → Database Migration

```diff
+-- Replaces ephemeral/.affect-{session_id}.json and habituation_counts.json
+CREATE TABLE session_state (
+    session_id TEXT NOT NULL,
+    subsystem  TEXT NOT NULL,
+    data       TEXT NOT NULL,
+    updated_at TEXT NOT NULL,
+    PRIMARY KEY (session_id, subsystem)
+);
```

**Reasoning**: Two subsystems store state in JSON files that are invisible to queries, lost on cleanup, and can't participate in transactions. Moving them to a single key-value table keeps the flexible JSON format while gaining durability and queryability.

### Tier 3: Extensibility Foundation

```diff
+-- Plugin/extension metadata (generic key-value per memory)
+CREATE TABLE memory_meta (
+    memory_id TEXT NOT NULL,
+    namespace TEXT NOT NULL,
+    key       TEXT NOT NULL,
+    value     TEXT,
+    created_at TEXT NOT NULL,
+    PRIMARY KEY (memory_id, namespace, key)
+);

+-- Memory version history
+CREATE TABLE memory_versions (
+    id         INTEGER PRIMARY KEY AUTOINCREMENT,
+    memory_id  TEXT NOT NULL,
+    version    INTEGER NOT NULL,
+    title      TEXT,
+    summary    TEXT,
+    content    TEXT,
+    created_at TEXT NOT NULL,
+    trigger    TEXT
+);
+CREATE INDEX idx_memory_versions_mid ON memory_versions(memory_id, version);

+-- Cross-project sharing
+CREATE TABLE memory_projects (
+    memory_id  TEXT NOT NULL,
+    project_id TEXT NOT NULL,
+    added_at   TEXT NOT NULL,
+    PRIMARY KEY (memory_id, project_id)
+);
```

**Reasoning**: These three tables unlock the most-requested extensibility vectors (plugin data, version history, cross-project sharing) without touching existing tables. All are purely additive — existing code continues to work unchanged.

---

## Structural End-Goal

### Table Relationships (post-evolution)

```
                        ┌─────────────────┐
                        │    memories      │
                        │─────────────────│
                        │ PK id           │
                        │    stage (enum) │
                        │    importance    │
                        │    project_ctx   │
                        │    ...22 cols    │
                        └────────┬────────┘
               ┌────────┬───────┼───────┬────────┬──────────┐
               │        │       │       │        │          │
               ▼        ▼       ▼       ▼        ▼          ▼
          ┌─────────┐ ┌─────┐ ┌─────┐ ┌──────┐ ┌────────┐ ┌──────────┐
          │ memory  │ │ mem │ │ mem │ │thread│ │retriev.│ │consolid. │
          │ _edges  │ │_fts │ │_ver-│ │_memb-│ │  _log  │ │  _log    │
          │─────────│ │─────│ │sions│ │ ers  │ │────────│ │──────────│
          │ src_id  │ │rowid│ │─────│ │──────│ │mem_id  │ │ mem_id   │
          │ tgt_id  │ │title│ │mem_id│ │thr_id│ │session │ │ action   │
          │ type    │ │...  │ │vers │ │mem_id│ │quality │ │ stage    │
          │ weight  │ └─────┘ │trigg│ │posn  │ └────────┘ └──────────┘
          │ metadata│         └─────┘ └──────┘
          └─────────┘              │
                                   ▼
                            ┌──────────────┐
                            │  narrative   │
                            │  _threads    │
                            │──────────────│
                            │ PK id        │
                            │    title     │
                            │    arc_affect│
                            └──────────────┘

  ┌─────────────┐   ┌──────────────┐   ┌─────────────┐
  │ memory_meta │   │memory_projects│  │session_state │
  │─────────────│   │──────────────│   │─────────────│
  │ mem_id (FK) │   │ mem_id (FK)  │   │ session_id  │
  │ namespace   │   │ project_id   │   │ subsystem   │
  │ key         │   │ added_at     │   │ data (JSON) │
  │ value       │   └──────────────┘   └─────────────┘
  └─────────────┘
```

### Data Flow After Evolution

```
  Session Input
       │
       ▼
  ┌──────────┐    ┌──────────────┐    ┌──────────────┐
  │  Ingest   │───▶│   memories   │───▶│ memory_ver-  │
  │ (observe) │    │  (current)   │    │   sions      │
  └──────────┘    └──────┬───────┘    │ (history)    │
                         │            └──────────────┘
            ┌────────────┼────────────┐
            ▼            ▼            ▼
    ┌─────────────┐ ┌─────────┐ ┌──────────┐
    │  FTS5 + Vec │ │  Graph  │ │  Threads │
    │  (search)   │ │ (edges) │ │  (arcs)  │
    └──────┬──────┘ └────┬────┘ └────┬─────┘
           │             │           │
           └──────┬──────┘───────────┘
                  ▼
           ┌─────────────┐    ┌──────────────┐
           │  Retrieval   │───▶│ retrieval_log│
           │  (RRF + SM2) │    │ + quality    │
           └──────┬──────┘    └──────────────┘
                  │
                  ▼
           ┌─────────────┐    ┌──────────────┐
           │  Lifecycle   │───▶│consolidation │
           │  (promote)   │    │    _log      │
           └─────────────┘    └──────────────┘
```

### Migration Strategy

```
Phase 1 (now)     Phase 2 (next)      Phase 3 (later)
─────────────     ──────────────      ───────────────
Stage enum        session_state       memory_versions
Indexes           memory_meta         memory_projects
CHECK constraints Edge validation     Audit log
                  Typed metadata      Decay profiles
                                      Cluster tables
```

Each phase is independently shippable. Phase 1 has zero behavioral change. Phase 2 is additive tables. Phase 3 introduces new subsystems that build on the Phase 2 foundation.

---

## Summary

The current schema is well-designed for its original scope (single-user, single-project memory lifecycle). Its main extensibility limitation is not structural but organizational: hardcoded strings instead of enums, filesystem state that should be in the database, and missing indexes that will matter at scale.

The proposed changes fall into three tiers. Tier 1 (structural hygiene) is pure upside with zero risk. Tier 2 (filesystem migration) consolidates state into the database for durability and queryability. Tier 3 (extensibility tables) unlocks plugin data, version history, and cross-project sharing without modifying any existing table.

The `MemoryEdge` graph is the most naturally extensible part of the schema — new edge types require only a new string constant and optional metadata schema, with the recomputable/incremental split handling rebuild semantics automatically. The least extensible part is the lifecycle stage system, which needs a Stage enum and eventually data-driven transition rules to support custom stage paths.
