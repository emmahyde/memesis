# Memesis Schema Analysis: Compression Patterns for LLM Context

## Executive Summary

This document details the complete data model for memesis memories and observations, identifying where compression can be applied for LLM context window optimization.

**Key Finding**: The primary content field (`content`) stores full markdown with YAML frontmatter. A single memory can be 5-50KB of text. The token budget for Tier-2 (crystallized) injection is ~16,000 tokens (8% of 200K context window).

---

## 1. Observation Model

**File**: `/Users/emmahyde/projects/memesis/core/models.py` (lines 576-593)

### Fields

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `id` | AutoField | PRIMARY KEY | Auto-incrementing integer |
| `created_at` | TextField | default=isoformat | `datetime.now().isoformat()` |
| `session_id` | TextField | null=True | Links to session |
| `source_path` | TextField | null=True | File path to ephemeral source |
| `ordinal` | IntegerField | null=True | Position in session |
| `content` | TextField | NOT NULL | **Raw observation text** |
| `filtered_content` | TextField | null=True | After habituation filter |
| `content_hash` | TextField | null=True | MD5 hash for dedup |
| `status` | TextField | null=True | pending/kept/pruned/promoted |
| `memory_id` | TextField | null=True | Links to created Memory |
| `metadata` | TextField | null=True | JSON blob |

### Storage Format

- `content`: Raw text (typically 100-2000 chars per observation)
- `metadata`: JSON string with source info
- Observations are **split from ephemeral markdown files** using `_split_observation_blocks()` (consolidator.py lines 886-907)
- Each block becomes one Observation row

### Compression Opportunities

1. **filtered_content** is redundant with content after filtering - could be dropped
2. **metadata** JSON could be compressed or schema fields extracted
3. Observations are **ephemeral** (Stage 1) - not injected, so compression less critical

---

## 2. Memory Model

**File**: `/Users/emmahyde/projects/memesis/core/models.py` (lines 77-429)

### Core Fields

| Field | Type | Default | Compression Target |
|-------|------|---------|-------------------|
| `id` | TextField (UUID) | uuid.uuid4() | - |
| `stage` | TextField | - | **KEY** - determines injection eligibility |
| `title` | TextField | null | **INJECTED** - shown in retrieval |
| `summary` | TextField | null | **INJECTED** - shown as italic subtext |
| `content` | TextField | null | **MAIN CONTENT** - full markdown body |
| `tags` | TextField (JSON) | null | **INJECTED** - parsed as list |
| `importance` | FloatField | 0.5 | Scoring metadata |
| `reinforcement_count` | IntegerField | 0 | Promotion eligibility |
| `created_at` | TextField | null | ISO timestamp |
| `updated_at` | TextField | null | ISO timestamp |
| `last_injected_at` | TextField | null | ISO timestamp |
| `last_used_at` | TextField | null | ISO timestamp |
| `injection_count` | IntegerField | 0 | Usage tracking |
| `usage_count` | IntegerField | 0 | Usage tracking |
| `project_context` | TextField | null | Context matching |
| `source_session` | TextField | null | Session attribution |
| `content_hash` | TextField | null | MD5 hash for dedup |
| `archived_at` | TextField | null | Archive flag |
| `subsumed_by` | TextField | null | Crystallization reference |
| `echo_count` | IntegerField | 0 | - |
| `next_injection_due` | TextField | null | Spaced repetition |
| `injection_ease_factor` | FloatField | 2.5 | SM-2 scheduling |
| `injection_interval_days` | FloatField | 1.0 | SM-2 scheduling |
| `files_modified` | TextField (JSON) | "[]" | File attribution |

### W2 Schema Additions (Stage 2 enrichment)

| Field | Type | Notes |
|-------|------|-------|
| `kind` | TextField | decision/finding/preference/constraint/correction/open_question |
| `knowledge_type` | TextField | factual/conceptual/procedural/metacognitive |
| `knowledge_type_confidence` | TextField | low/high |
| `subject` | TextField | self/user/system/collaboration/workflow/aesthetic/domain |
| `work_event` | TextField | bugfix/feature/refactor/discovery/change/null |
| `subtitle` | TextField | ≤24 word retrieval card |
| `cwd` | TextField | Multi-project attribution |
| `session_type` | TextField | code/writing/research/null |
| `raw_importance` | FloatField | Stage 1 importance preserved for audit |
| `linked_observation_ids` | TextField (JSON) | UUIDs of source observations |

### WS-H / Sprint B Fields

| Field | Type | Notes |
|-------|------|-------|
| `resolves_question_id` | TextField | UUID of open_question this resolves |
| `resolved_at` | DateTimeField | When question was resolved |
| `is_pinned` | IntegerField | 1 = exempt from auto-pruning |

### Agentic-Memory BLOCKER (B4 + E3)

| Field | Type | Notes |
|-------|------|-------|
| `expires_at` | IntegerField | Unix timestamp; NULL = never |
| `source` | TextField | 'human'/'agent' poisoning guard |

### Stage 1.5 Extended Metadata

| Field | Type | Notes |
|-------|------|-------|
| `temporal_scope` | TextField | session-local/cross-session-durable |
| `extraction_confidence` | FloatField | 0-1 certainty |
| `actor` | TextField | user/assistant/system/external |
| `polarity` | TextField | positive/negative/corrective/neutral |
| `revisable` | TextField | '0'=stable, '1'=provisional |

### Wave 3 / Task 3.1

| Field | Type | Notes |
|-------|------|-------|
| `confidence` | FloatField | 0.0-1.0, default 0.7 |
| `affect_valence` | TextField | friction/delight/surprise/neutral/mixed |

### Tier-3 Audit (Wave A)

| Field | Type | Notes |
|-------|------|-------|
| `criterion_weights` | TextField (JSON) | {criterion: hard_veto/strong/weak/mentioned} |
| `rejected_options` | TextField (JSON) | [{option, reason}] |

### Content Storage Format

The `content` field stores **full markdown with YAML frontmatter**:

```markdown
---
name: Memory Title
description: Summary text
type: memory
---

Memory body text here. This can be quite long - multiple paragraphs,
code examples, etc.
```

**Generated by**: `_format_markdown()` in consolidator.py (lines 874-883)

---

## 3. MemoryEdge Model

**File**: `/Users/emmahyde/projects/memesis/core/models.py` (lines 494-516)

### Fields

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `id` | AutoField | PRIMARY KEY | - |
| `source_id` | TextField | - | Memory UUID |
| `target_id` | TextField | - | Memory UUID |
| `edge_type` | TextField | - | thread_neighbor/tag_cooccurrence/caused_by/refined_from/subsumed_into/contradicts/echo |
| `weight` | FloatField | 1.0 | Edge strength |
| `metadata` | TextField (JSON) | null | evidence/affect/timestamps |

### Edge Types

**Recomputable** (rebuilt by `compute_edges()`):
- `thread_neighbor`
- `tag_cooccurrence`

**Incremental** (created during pipeline):
- `caused_by`
- `refined_from`
- `subsumed_into`
- `contradicts`
- `echo`

---

## 4. ConsolidationLog Model

**File**: `/Users/emmahyde/projects/memesis/core/models.py` (lines 548-569)

### Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | AutoField | PRIMARY KEY |
| `timestamp` | TextField | ISO format |
| `session_id` | TextField | Session UUID |
| `action` | TextField | kept/pruned/promoted/demoted/merged/deprecated/subsumed/archived |
| `memory_id` | TextField | Memory UUID (or pseudo-id for pruned) |
| `from_stage` | TextField | Source stage |
| `to_stage` | TextField | Target stage |
| `rationale` | TextField | Why the decision |
| `prompt` | TextField | **LLM prompt sent** (can be very large) |
| `llm_response` | TextField | **LLM response received** |
| `model` | TextField | Model used |
| `input_tokens` | IntegerField | Token count estimate |
| `output_tokens` | IntegerField | Token count estimate |
| `latency_ms` | IntegerField | Response time |
| `input_observation_refs` | TextField (JSON) | List of observation IDs |

### Compression Opportunities

The `prompt` and `llm_response` fields can be **very large** - they contain full LLM interactions. These are primarily for debugging/audit and could be:
1. Truncated after N characters
2. Compressed with zlib
3. Moved to separate audit table

---

## 5. SQLite Schema (CREATE TABLE statements)

**File**: `/Users/emmahyde/projects/memesis/claude-mem/src/services/sqlite/schema.sql`

Note: This is the **claude-mem** schema (different project), but shows an alternative observation storage pattern with structured fields:

```sql
CREATE TABLE observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  text TEXT,
  type TEXT NOT NULL,
  title TEXT,
  subtitle TEXT,
  facts TEXT,
  narrative TEXT,
  concepts TEXT,
  files_read TEXT,
  files_modified TEXT,
  prompt_number INTEGER,
  discovery_tokens INTEGER DEFAULT 0,
  content_hash TEXT,
  agent_type TEXT,
  agent_id TEXT,
  merged_into_project TEXT,
  generated_by_model TEXT,
  metadata TEXT,
  created_at TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL,
  UNIQUE(memory_session_id, content_hash)
);
```

**Key difference**: claude-mem stores structured fields (facts, narrative, concepts) separately, while memesis stores everything in the `content` markdown field.

---

## 6. Retrieval Injection Format

**File**: `/Users/emmahyde/projects/memesis/core/retrieval.py` (lines 93-197)

When memories are injected into session context:

```markdown
---MEMORY CONTEXT---

## Your Behavioral Guidelines (always active)

### Title Here
Full content body here...

## Context-Relevant Knowledge

### Title (importance: 0.85)
*Provenance signal*
*Summary text*
Full content body...

## Narrative Threads (how understanding evolved)

### Thread Title
Thread narrative text...

## Active Tensions (conflicting memories)

### Tension
Position A: Memory title: summary
Position B: Memory title: summary
```

### Token Budget

- **CONTEXT_WINDOW_CHARS** = 200,000 × 4 = 800,000 chars (line 39)
- **token_budget_pct** default = 0.08 (8%)
- **Tier-2 limit** = ~16,000 tokens (64,000 chars)

### Budget Enforcement

```python
budget_remaining = token_limit
selected = []
for record in records_sorted:
    content = record.content or ""
    cost = len(content)
    if cost <= budget_remaining:
        selected.append(record)
        budget_remaining -= cost
```

---

## 7. Existing Compression/Formatting Logic

### Token Counter (Glyph)

**File**: `/Users/emmahyde/projects/memesis/core/glyph/token_counter.py`

```python
class TokenCounter:
    def count(self, text: str) -> int:
        return len(self.enc.encode(text))  # tiktoken
```

Uses `tiktoken` with `cl100k_base` encoding. **Not currently used for memory compression**.

### Thread Narrative Truncation

**File**: `/Users/emmahyde/projects/memesis/core/retrieval.py` (lines 505-513)

```python
_THREAD_NARRATIVE_CAP = 1_000  # chars

for t in candidates:
    narrative = t.narrative or ""
    if len(narrative) > _THREAD_NARRATIVE_CAP:
        truncated = narrative[:_THREAD_NARRATIVE_CAP]
        last_period = truncated.rfind(".")
        if last_period > _THREAD_NARRATIVE_CAP // 2:
            truncated = truncated[:last_period + 1]
        t.narrative = truncated
```

**Pattern**: Truncate at sentence boundary near cap.

### FTS5 Stop Words

**File**: `/Users/emmahyde/projects/memesis/core/models.py` (lines 51-64)

```python
_FTS_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    # ... ~90 common words
})
```

Used for query sanitization, not compression.

### Content Hashing

**File**: `/Users/emmahyde/projects/memesis/core/models.py` (lines 330-332)

```python
def compute_hash(self, full_content: str) -> str:
    """Compute MD5 hash of the full markdown content."""
    return hashlib.md5(full_content.encode("utf-8")).hexdigest()
```

Used for deduplication, not compression.

---

## 8. Stage-to-Tier Mapping

**File**: `/Users/emmahyde/projects/memesis/core/tiers.py`

| Stage | Tier | TTL | Injection |
|-------|------|-----|-----------|
| instinctive | T1 | None (never) | Always (Tier 1) |
| crystallized | T2 | 180 days | Token-budgeted (Tier 2) |
| consolidated | T3 | 90 days | On-demand (Tier 3) |
| ephemeral | T4 | 30 days | Never injected |

---

## 9. Compression Opportunities Summary

### High Priority

1. **`content` field** - Main compression target
   - Stores full markdown with frontmatter
   - Can be 5-50KB per memory
   - **Suggestion**: Store stripped content (no frontmatter), compress old memories

2. **`prompt`/`llm_response` in ConsolidationLog** - Audit fields
   - Can be 10-100KB each
   - **Suggestion**: Truncate after 10KB or move to separate audit table

3. **`summary` field** - Already a summary
   - Could be used instead of full content for low-importance memories
   - **Suggestion**: Use summary-only for importance < 0.5

### Medium Priority

4. **`tags` JSON** - Small but parseable
   - Could be stored as normalized table
   - **Suggestion**: Keep as-is, minimal savings

5. **`files_modified` JSON** - File list
   - Usually < 1KB
   - **Suggestion**: Keep as-is

### Low Priority

6. **`metadata` fields** - Already structured
   - Observation metadata, edge metadata
   - **Suggestion**: Consider compression for archived memories

### Compression Patterns to Implement

1. **Frontmatter Stripping**: Remove YAML frontmatter from content before storage
2. **Summary Fallback**: Use `summary` field when `importance < threshold`
3. **Truncation with Sentence Boundary**: Like thread narratives (see `_THREAD_NARRATIVE_CAP`)
4. **Zlib Compression**: For archived/old memories
5. **Field Extraction**: Move `prompt`/`llm_response` to separate audit table

---

## 10. Key Files Reference

| File | Purpose |
|------|---------|
| `/Users/emmahyde/projects/memesis/core/models.py` | Peewee ORM models |
| `/Users/emmahyde/projects/memesis/core/database.py` | DB initialization, migrations |
| `/Users/emmahyde/projects/memesis/core/consolidator.py` | Observation→Memory conversion |
| `/Users/emmahyde/projects/memesis/core/retrieval.py` | Memory injection formatting |
| `/Users/emmahyde/projects/memesis/core/crystallizer.py` | Memory transformation |
| `/Users/emmahyde/projects/memesis/core/lifecycle.py` | Stage transitions |
| `/Users/emmahyde/projects/memesis/core/tiers.py` | TTL, decay constants |
| `/Users/emmahyde/projects/memesis/core/prompts.py` | LLM prompt templates |
