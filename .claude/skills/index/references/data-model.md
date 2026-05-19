# Data Model Reference

## Memory (`core/models.py`)

Primary record. Table: `memories`.

### Identity & Content
| Field | Type | Notes |
|-------|------|-------|
| `id` | TEXT PK | UUID |
| `title` | TEXT | Short label |
| `summary` | TEXT | ≤24-word retrieval card (`subtitle` alias) |
| `content` | TEXT | Full body |
| `content_hash` | TEXT | SHA-256; dedup gate |
| `tags` | TEXT | JSON array |

### Classification
| Field | Type | Values |
|-------|------|--------|
| `stage` | TEXT | `ephemeral` / `consolidated` / `crystallized` / `instinctive` |
| `kind` | TEXT | `decision` / `finding` / `preference` / `constraint` / `correction` / `open_question` / `hypothesis` |
| `knowledge_type` | TEXT | `factual` / `conceptual` / `procedural` / `metacognitive` |
| `subject` | TEXT | `self` / `user` / `system` / `collaboration` / `workflow` / `aesthetic` / `domain` |
| `work_event` | TEXT | `bugfix` / `feature` / `refactor` / `discovery` / `change` / null |
| `polarity` | TEXT | `positive` / `negative` / `corrective` / `neutral` (within `kind=finding`) |
| `temporal_scope` | TEXT | `session-local` / `cross-session-durable` |
| `source` | TEXT | `human` / `agent` — guards against poisoning |

### Lifecycle Counters
| Field | Type | Notes |
|-------|------|-------|
| `importance` | FLOAT | 0–1; consolidation-assigned |
| `reinforcement_count` | INT | Incremented on dedup-promote |
| `injection_count` | INT | Times offered as context |
| `usage_count` | INT | Times marked actually used |
| `echo_count` | INT | Redundant-signal counter |
| `extraction_confidence` | FLOAT | 0–1 certainty at extraction time |
| `revisable` | TEXT | `'0'`=stable, `'1'`=provisional |
| `is_pinned` | INT | 1 = exempt from auto-pruning |

### Temporal
| Field | Notes |
|-------|-------|
| `created_at` | ISO string |
| `updated_at` | ISO string |
| `last_injected_at` | ISO string |
| `last_used_at` | ISO string |
| `last_accessed_at` | DateTime |
| `archived_at` | ISO string; non-null = archived |
| `expires_at` | Unix int; NULL = never expires |
| `next_injection_due` | ISO string; spaced repetition scheduler |
| `injection_ease_factor` | FLOAT | SM-2 ease factor (default 2.5) |
| `injection_interval_days` | FLOAT | SM-2 interval (default 1.0) |

### Scoping & Attribution
| Field | Notes |
|-------|-------|
| `project_context` | CWD at creation time |
| `project` | Slug form: `-Users-emmahyde-projects-memesis` |
| `source_session` | Session ID that produced this memory |
| `cwd` | Multi-project attribution |
| `actor` | `user` / `assistant` / `system` / `external` |
| `session_type` | `code` / `writing` / `research` / null |

### Relationships
| Field | Notes |
|-------|-------|
| `linked_observation_ids` | JSON list of UUIDs (cosine-linked memories) |
| `subsumed_by` | UUID of the memory that absorbed this one |
| `files_modified` | JSON array of relative paths |
| `resolves_question_id` | UUID of the `open_question` this memory resolves |
| `resolved_at` | DateTime; when question was resolved |

### Key Scopes
```python
Memory.active()   # archived_at IS NULL
Memory.live()     # active() + not expired
Memory.by_stage(stage)   # active + stage filter
Memory.search_fts(query, limit=15)  # FTS5 full-text
```

---

## ConsolidationLog (`core/models.py:592`)

Immutable audit trail for all consolidation decisions. Table: `consolidation_log`.

| Field | Notes |
|-------|-------|
| `action` | `keep` / `prune` / `merge` / `promoted` / `demoted` / `archived` / `rehydrated` |
| `memory_id` | FK to Memory (nullable) |
| `from_stage` / `to_stage` | Stage transition |
| `rationale` | Human-readable explanation |
| `llm_response` | Raw LLM JSON response |
| `input_observation_refs` | Which observations were input |
| `compression_ratio` | output_tokens / input_tokens |
| `session_id` | Session that triggered this action |

---

## Observation (`core/models.py:624`)

Raw observations extracted before consolidation decision. Table: `observations`.

| Field | Notes |
|-------|-------|
| `session_id` | Session that produced this observation |
| `source_path` | Ephemeral buffer file path |
| `ordinal` | 0-indexed. **LLM sees 1-indexed** — add 1 when joining on LLM `obs_ids` |
| `content` | Raw extracted text |
| `filtered_content` | After significance filter |
| `status` | `filtered` / null (if kept) |
| `memory_id` | FK if consolidated into a Memory |
| `metadata` | JSON: tags, importance hints, etc. |
| `project` | Slug for project attribution |

---

## RetrievalLog / RetrievalCandidate

Per-session injection scoring. Use for injection audits.

```python
RetrievalLog.select().where(RetrievalLog.session_id == sid)
# Then for each log:
RetrievalCandidate.select().where(
    RetrievalCandidate.retrieval_log_id == log.id
).order_by(RetrievalCandidate.rank)
```

Candidate fields: `rank`, `fts_rank`, `vector_rank`, `semantic_score`, `recency_score`, `importance_score`, `affect_score`, `reinforcement_score`, `boost_score`.

---

## MemoryEdge

Typed semantic links between memories.

```python
from core.models import MemoryEdge
edges = MemoryEdge.select().where(
    (MemoryEdge.source_id == memory_id) | (MemoryEdge.target_id == memory_id)
)
```

Edge types: `contradicts`, `supports`, `specializes`, `generalizes`.

---

## Promotion Rules (from `core/lifecycle.py`)

| Transition | Gate |
|-----------|------|
| ephemeral → consolidated | Consolidation decision (LLM) |
| consolidated → crystallized | `reinforcement_count >= 3` + temporal spacing |
| crystallized → instinctive | `importance > 0.85` AND usage in 10+ distinct sessions |
| Any → archived | `relevance_score < ARCHIVE_THRESHOLD (0.15)` via RelevanceEngine |

**Demotion:** always valid, can skip stages. Triggered by low usage or staleness.
