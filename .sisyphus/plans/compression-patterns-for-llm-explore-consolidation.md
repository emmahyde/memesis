# Consolidation Pipeline Analysis

## Executive Summary

The memesis consolidation pipeline transforms ephemeral observations into curated memories through a multi-stage LLM-driven process. This document details the data structures, prompt formats, and compression injection points.

---

## 1. PreCompact Hook Implementation

**File:** `/Users/emmahyde/projects/memesis/hooks/pre_compact.py`

### Purpose
Runs during Claude Code's `PreCompact` hook lifecycle event. Uses a lock-snapshot-clear pattern to prevent double-processing.

### Key Flow
```
1. Lock ephemeral buffer (fcntl.flock)
2. Snapshot content → clear buffer
3. Process snapshot (unlocked)
4. Run consolidation via Consolidator
5. Embed new memories (vec_store)
6. Crystallize candidates
7. Build narrative threads
8. Promote to instinctive
9. Run relevance maintenance
10. Periodic self-reflection
```

### Critical Variables
- `REFLECTION_INTERVAL = 5` — self-reflection runs every 5 consolidations
- Session affect state loaded pre-consolidation for reconsolidation signals

---

## 2. LLM Curation Pass (Consolidation)

**File:** `/Users/emmahyde/projects/memesis/core/consolidator.py`

### Consolidator Class

```python
class Consolidator:
    def __init__(self, lifecycle: LifecycleManager, model: str = "claude-sonnet-4-6"):
        self.lifecycle = lifecycle
        self.model = model
```

### Main Entry Point

```python
def consolidate_session(self, ephemeral_path: str, session_id: str) -> dict:
    """
    Returns: {
        "kept": [memory_id, ...],
        "pruned": [{"observation": ..., "rationale": ...}, ...],
        "promoted": [memory_id, ...],
        "conflicts": [{"observation": ..., "contradicts": memory_id}, ...],
        "resolved": [...],
        "rehydrated": [...]
    }
    """
```

### Pipeline Steps

1. **Read ephemeral content** from markdown file
2. **Habituation filter** — suppress routine events before LLM
3. **Replay priority** — sort observations by salience
4. **Record observations** — persist to `observations` table with status="pending"
5. **Build manifest summary** — existing memories by stage
6. **Build open_questions block** — unresolved questions (WS-H)
7. **Call LLM** with `CONSOLIDATION_PROMPT`
8. **Execute decisions** — keep/prune/promote
9. **Resolve conflicts** — contradiction resolution via LLM
10. **Check rehydration** — archived memories matching new observations

### Decision Execution

**KEEP Action:**
- Creates new `Memory` with stage="consolidated"
- Writes file to `base_dir/consolidated/<target_path>`
- Tags with observation_type, valence (somatic marker)
- Card fields: `temporal_scope`, `confidence`, `affect_valence`, `actor`, `criterion_weights`, `rejected_options`
- Importance: `min(0.5 + importance_boost, 1.0)` or LLM-provided `card.importance`

**PRUNE Action:**
- Logs to `ConsolidationLog` with pseudo-id
- No memory created

**PROMOTE Action:**
- Increments `reinforcement_count` on existing memory
- Triggers lifecycle promotion check

---

## 3. MemoryStore / Database

**File:** `/Users/emmahyde/projects/memesis/core/database.py`

### Initialization

```python
def init_db(project_context: str = None, base_dir: str = None) -> Path:
    # Resolves: ~/.claude/projects/<hash>/memory or ~/.claude/memory
    # Creates: index.db (SQLite with WAL)
    # Creates: FTS5 virtual table (memories_fts)
    # Initializes: VecStore singleton
```

### Tables Created
- `memories` — primary memory store
- `narrative_threads` — episodic arc grouping
- `thread_members` — thread↔memory relationships
- `memory_edges` — graph edges (thread_neighbor, tag_cooccurrence, caused_by, etc.)
- `retrieval_log` — injection tracking
- `consolidation_log` — curation decisions
- `observations` — raw/filtered observations before decision
- `retrieval_candidates` — per-candidate scoring details
- `affect_log` — point-in-time affect snapshots
- `eval_runs` — eval metadata

### Key Functions
- `get_vec_store()` — returns VecStore singleton (may be None)
- `get_base_dir()` — returns Path to memory directory
- `close_db()` — WAL checkpoint + close

---

## 4. Observation Model

**File:** `/Users/emmahyde/projects/memesis/core/models.py`

### Observation Table Schema

```python
class Observation(BaseModel):
    id = AutoField()                    # Primary key
    created_at = TextField()            # ISO timestamp
    session_id = TextField()            # Session identifier
    source_path = TextField()           # Ephemeral file path
    ordinal = IntegerField()           # Position in session
    content = TextField()               # RAW observation text
    filtered_content = TextField()      # After habituation filter
    content_hash = TextField()          # MD5 of content
    status = TextField()               # pending|kept|pruned|promoted
    memory_id = TextField()            # Linked memory (if kept)
    metadata = TextField()             # JSON: source, etc.
```

### Status Lifecycle
1. `status="pending"` — created during `_record_observations()`
2. `status="kept"` — LLM decided KEEP, linked to memory_id
3. `status="pruned"` — LLM decided PRUNE
4. `status="promoted"` — LLM decided PROMOTE, linked to reinforced memory_id

---

## 5. Memory Model

**File:** `/Users/emmahyde/projects/memesis/core/models.py`

### Memory Table Schema (Key Fields)

```python
class Memory(BaseModel):
    id = TextField(primary_key=True, default=lambda: str(uuid.uuid4()))
    stage = TextField()                 # ephemeral|consolidated|crystallized|instinctive
    title = TextField(null=True)
    summary = TextField(null=True)      # First 150 chars of content
    content = TextField(null=True)      # Full markdown with frontmatter
    tags = TextField(null=True)         # JSON array
    importance = FloatField(default=0.5)
    reinforcement_count = IntegerField(default=0)
    created_at = TextField()
    updated_at = TextField()
    content_hash = TextField(null=True) # MD5 of full_content
    archived_at = TextField(null=True)
    
    # W2 schema additions
    kind = TextField(null=True)         # decision|finding|preference|constraint|correction|open_question
    knowledge_type = TextField(null=True) # factual|conceptual|procedural|metacognitive
    subject = TextField(null=True)      # self|user|system|collaboration|workflow|aesthetic|domain
    work_event = TextField(null=True)   # bugfix|feature|refactor|discovery|change|null
    subtitle = TextField(null=True)     # ≤24 word retrieval card
    
    # Task 3.1 card fields
    confidence = FloatField(null=True)  # 0.0-1.0
    affect_valence = TextField(null=None) # friction|delight|surprise|neutral|mixed
    criterion_weights = TextField(null=True)  # JSON dict
    rejected_options = TextField(null=True)   # JSON list
```

### Memory Content Format

Memories are stored as markdown with YAML frontmatter:

```markdown
---
name: Memory Title
description: Summary text
type: memory
---

Memory body content here. The actual insight, observation, or pattern.
```

---

## 6. LLM Curation Prompt Structure

**File:** `/Users/emmahyde/projects/memesis/core/prompts.py`

### CONSOLIDATION_PROMPT

The prompt is a multi-part template:

```
You are reviewing a buffer of Stage 1 observations...
THE BEHAVIORAL GATE: For each observation, ask — "Would I do something wrong without this?"

SESSION OBSERVATIONS (Stage 1 buffer):
{ephemeral_content}

EXISTING MEMORY MANIFEST:
{manifest_summary}

UNRESOLVED OPEN QUESTIONS (from prior sessions, awaiting resolution):
{open_questions_block}

MANDATORY KEEP:
- Observations prefixed with [PRIORITY] were explicitly stored by the user via /learn.

KEEP gates (in priority order):
1. CORRECTIONS
2. PREFERENCE SIGNALS (only if surprising/counter-intuitive)
3. SELF-OBSERVATIONS
4. WORKFLOW PATTERNS

PRUNE if:
- Re-derivable from code, git log, docs, or codebase reading
- One-time task mechanics, file paths, commit hashes, test output
- Generic observations true of most engineers
...

IMPORTANCE RE-SCORING:
0.2  routine finding
0.5  useful context
0.8  load-bearing decision
0.95 correction or hard constraint

STAGE 2 AXIS PROMPTS:
subject, work_event, subtitle fields...

BEHAVIORAL FRAMING:
- Phrase friction signals as workflow patterns, not feelings.

CONFLICT CHECK:
- Does any observation CONTRADICT an existing memory?
- Does any observation REINFORCE an existing memory?
```

### Expected LLM Response Format

```json
{
  "decisions": [
    {
      "raw_importance": 0.0,
      "importance": 0.0,
      "kind": "decision|finding|preference|constraint|correction|open_question",
      "knowledge_type": "factual|conceptual|procedural|metacognitive",
      "knowledge_type_confidence": "low|high",
      "facts": ["Named subject did what, when/where — no pronouns"],
      "cwd": "/abs/path/or/null",
      "subject": "self|user|system|collaboration|workflow|aesthetic|domain",
      "work_event": "bugfix|feature|refactor|discovery|change|null",
      "subtitle": "retrieval card no longer than twenty-four words",
      "action": "keep|prune|promote",
      "rationale": "why this decision",
      "target_path": "category/filename.md (keep only)",
      "reinforces": "memory_id or null",
      "contradicts": "memory_id or null",
      "resolves_question_id": "memory_id or null"
    }
  ]
}
```

---

## 7. Crystallization (Stage 2 Promotion)

**File:** `/Users/emmahyde/projects/memesis/core/crystallizer.py`

### CRYSTALLIZATION_PROMPT

```
You are transforming episodic observations into semantic knowledge...

SOURCE OBSERVATIONS (these have proven valuable across multiple sessions):
{observations}

YOUR TASK: Synthesize these into ONE crystallized insight.

THE TRANSFORMATION:
- Strip away session-specific details (dates, file paths, one-time contexts)
- Extract the PATTERN — the general principle these observations share
- Preserve the behavioral teeth — what would I do differently because of this?
- Be denser than the sources — a crystallized memory should pack more signal per word

Respond ONLY with valid JSON:
{
  "title": "General principle (not a specific fact)",
  "insight": "The crystallized understanding — dense, behavioral, pattern-level",
  "observation_type": "...",
  "tags": ["tag1", "tag2"],
  "source_pattern": "One sentence: what these observations have in common"
}
```

### Crystallization Flow
1. Get promotion candidates from lifecycle (reinforcement_count >= threshold)
2. Group by theme (embedding cosine similarity OR tag overlap)
3. For each group: call LLM with CRYSTALLIZATION_PROMPT
4. Create crystallized memory (stage="crystallized")
5. Archive source memories (subsumed_by = crystallized_id)

---

## 8. Self-Reflection (Periodic)

**File:** `/Users/emmahyde/projects/memesis/core/self_reflection.py`

### SELF_REFLECTION_PROMPT

```
Review the consolidation log from recent sessions and identify patterns in your own behavior.

RECENT CONSOLIDATION DECISIONS:
{consolidation_history}

CURRENT SELF-MODEL:
{current_self_model}

Look for:
1. What kinds of observations do you consistently KEEP vs. PRUNE?
2. Are there recurring corrections? What underlying tendency produces them?
3. What observation types are underrepresented?
4. Have any crystallized memories been contradicted recently?

Respond with JSON:
{
  "observations": [
    {
      "tendency": "what I do",
      "evidence": "specific examples",
      "trigger": "when this tendency manifests",
      "correction": "what to do instead",
      "confidence": 0.0
    }
  ],
  "deprecated": ["tendency descriptions that are no longer accurate"]
}
```

---

## 9. Compression Injection Points

### Where Compression Should Be Injected

1. **Pre-LLM Curation (consolidator.py line ~106)**
   - After habituation filter and replay priority sorting
   - Before `_record_observations()` is called
   - Input: `filtered_content` (markdown string)
   - Output: Compressed `filtered_content`
   - This would reduce token count going into CONSOLIDATION_PROMPT

2. **Within CONSOLIDATION_PROMPT (prompts.py line ~111)**
   - The `{ephemeral_content}` placeholder receives the observations
   - Could add a compression guidance section to the prompt itself
   - The prompt already has COMPACTION_GUIDANCE content in self_reflection.py

3. **Pre-Crystallization (crystallizer.py line ~305)**
   - Before CRYSTALLIZATION_PROMPT is called
   - Input: `observations_text` (formatted group of memories)
   - Could compress the observation list

4. **Pre-Self-Reflection (self_reflection.py line ~275)**
   - Before SELF_REFLECTION_PROMPT is called
   - Input: `consolidation_history` (formatted log entries)
   - Could compress the history text

### Data Structures Holding Memory Content

| Structure | Type | Location |
|-----------|------|----------|
| `Memory.content` | TextField (markdown+frontmatter) | core/models.py:84 |
| `Memory.summary` | TextField (first 150 chars) | core/models.py:83 |
| `Observation.content` | TextField (raw text) | core/models.py:584 |
| `Observation.filtered_content` | TextField (post-habituation) | core/models.py:585 |
| `ConsolidationLog.prompt` | TextField (full prompt sent) | core/models.py:559 |
| `ConsolidationLog.llm_response` | TextField (raw LLM output) | core/models.py:560 |

### Compression Target Fields

When compressing for LLM context, the relevant fields are:

1. **Memory.title** — string
2. **Memory.summary** — string (already truncated to 150 chars)
3. **Memory.content** — full markdown with frontmatter
4. **Observation.content** — raw observation text
5. **Observation.filtered_content** — after habituation filter

### Recommended Compression Approach

For the consolidation pipeline, compression should:

1. **Preserve high-value fields:**
   - `kind`, `knowledge_type`, `subject`, `work_event`
   - `importance`, `confidence`, `affect_valence`
   - `title`, `summary` (already compact)

2. **Compress content strategically:**
   - Strip YAML frontmatter (already has title/description)
   - Remove redundant context
   - Truncate long content with ellipsis marker

3. **Maintain structure for LLM:**
   - Keep decision array format intact
   - Preserve observation blocks as units
   - Maintain the `{ephemeral_content}` block structure

---

## 10. Key Files Summary

| File | Purpose |
|------|---------|
| `/Users/emmahyde/projects/memesis/hooks/pre_compact.py` | PreCompact hook entry point |
| `/Users/emmahyde/projects/memesis/hooks/consolidate_cron.py` | Hourly cron consolidation |
| `/Users/emmahyde/projects/memesis/core/consolidator.py` | LLM curation engine |
| `/Users/emmahyde/projects/memesis/core/database.py` | DB initialization, MemoryStore |
| `/Users/emmahyde/projects/memesis/core/models.py` | ORM models (Memory, Observation, etc.) |
| `/Users/emmahyde/projects/memesis/core/prompts.py` | LLM prompt templates |
| `/Users/emmahyde/projects/memesis/core/crystallizer.py` | Crystallization engine |
| `/Users/emmahyde/projects/memesis/core/self_reflection.py` | Self-model reflection |
| `/Users/emmahyde/projects/memesis/core/llm.py` | LLM transport layer |
| `/Users/emmahyde/projects/memesis/core/vec.py` | Vector store (sqlite-vec) |
